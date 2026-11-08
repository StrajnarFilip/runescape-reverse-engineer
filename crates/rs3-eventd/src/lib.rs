//! WebSocket fan-out for [`rs3events::Reader`].
//!
//! Protocol (JSON text frames on `ws://<bind>/events`):
//! 1. server -> `{"type":"hello","protocol":1,"server":"...","sources":[pid,...]}`
//! 2. server -> one `{"type":"snapshot","snapshot":{...}}` per source
//! 3. server -> events as bare envelopes: `{"seq":..,"type":"player.prayer",...}`
//!
//! Client messages: `{"type":"subscribe","topics":["player.*","boss.telos.*"]}`,
//! `{"type":"source","src":1234}` (or `null`), `{"type":"snapshot"}`.
//! A client that falls behind gets `{"type":"resync","missed":N}` plus fresh snapshots.

use futures_util::{SinkExt, StreamExt};
use rs3events::{topic_matches, ClientMsg, Reader, ServerMsg, PROTOCOL_VERSION};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::broadcast;
use tokio_tungstenite::tungstenite::handshake::server::{ErrorResponse, Request, Response};
use tokio_tungstenite::tungstenite::http::StatusCode;
use tokio_tungstenite::tungstenite::Message;

/// One serialized event ready for fan-out.
pub struct Out {
    pub topic: String,
    pub src: u32,
    pub json: String,
}

pub struct Hub {
    pub reader: Arc<Reader>,
    pub tx: broadcast::Sender<Arc<Out>>,
}

impl Hub {
    /// Start bridging reader events into a broadcast channel (and optionally a JSONL recording).
    pub fn start(reader: Arc<Reader>, record: Option<PathBuf>) -> anyhow::Result<Arc<Self>> {
        let (tx, _) = broadcast::channel::<Arc<Out>>(1 << 16);
        let rx = reader.subscribe();
        let mut file = match &record {
            Some(p) => Some(std::io::BufWriter::new(
                std::fs::OpenOptions::new().create(true).append(true).open(p)?,
            )),
            None => None,
        };
        let btx = tx.clone();
        std::thread::Builder::new().name("rs3-eventd-bridge".into()).spawn(move || {
            let mut since_flush = 0u32;
            loop {
                match rx.recv_timeout(std::time::Duration::from_millis(200)) {
                    Ok(env) => {
                        let json = match serde_json::to_string(&*env) {
                            Ok(j) => j,
                            Err(e) => {
                                tracing::error!("serialize: {e}");
                                continue;
                            }
                        };
                        if let Some(f) = file.as_mut() {
                            let _ = writeln!(f, "{json}");
                            since_flush += 1;
                            if since_flush >= 64 {
                                let _ = f.flush();
                                since_flush = 0;
                            }
                        }
                        let _ = btx.send(Arc::new(Out { topic: env.ev.topic(), src: env.src, json }));
                    }
                    Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                        if let Some(f) = file.as_mut() {
                            let _ = f.flush();
                            since_flush = 0;
                        }
                    }
                    Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                }
            }
        })?;
        Ok(Arc::new(Self { reader, tx }))
    }
}

/// Accept WebSocket clients forever.
pub async fn serve(listener: TcpListener, hub: Arc<Hub>) -> anyhow::Result<()> {
    loop {
        let (stream, peer) = listener.accept().await?;
        let hub = hub.clone();
        tokio::spawn(async move {
            if let Err(e) = handle(stream, hub).await {
                tracing::debug!("client {peer}: {e}");
            }
        });
    }
}

fn text(msg: &ServerMsg) -> Message {
    Message::Text(serde_json::to_string(msg).expect("serialize server msg"))
}

/// The bundled viewer, served at `/` for plain HTTP requests.
pub const VIEWER_HTML: &str = include_str!("../../../clients/viewer.html");

/// Peek the request head; plain HTTP (no websocket upgrade) gets the viewer page.
async fn maybe_serve_http(stream: &mut TcpStream) -> anyhow::Result<bool> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut buf = [0u8; 4096];
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(3);
    let n = loop {
        let n = tokio::time::timeout_at(deadline, stream.peek(&mut buf)).await??;
        if n == 0 {
            return Ok(true);
        }
        if n == buf.len() || buf[..n].windows(4).any(|w| w == b"\r\n\r\n") {
            break n;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    };
    let head = String::from_utf8_lossy(&buf[..n]).to_ascii_lowercase();
    if head.contains("upgrade: websocket") {
        return Ok(false);
    }
    let _ = stream.read(&mut buf[..n]).await?;
    let path = head.split_whitespace().nth(1).unwrap_or("/");
    let (status, ctype, body) = match path {
        "/" | "/viewer" | "/index.html" => ("200 OK", "text/html; charset=utf-8", VIEWER_HTML),
        _ => ("404 Not Found", "text/plain", "not found; the viewer is at / and the WebSocket at /events\n"),
    };
    let resp = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(resp.as_bytes()).await?;
    stream.shutdown().await?;
    Ok(true)
}

async fn handle(mut stream: TcpStream, hub: Arc<Hub>) -> anyhow::Result<()> {
    stream.set_nodelay(true)?;
    if maybe_serve_http(&mut stream).await? {
        return Ok(());
    }
    #[allow(clippy::result_large_err)]
    let check = |req: &Request, resp: Response| -> Result<Response, ErrorResponse> {
        match req.uri().path() {
            "/" | "/events" => Ok(resp),
            _ => {
                let mut r = ErrorResponse::new(Some("use /events".into()));
                *r.status_mut() = StatusCode::NOT_FOUND;
                Err(r)
            }
        }
    };
    let ws = tokio_tungstenite::accept_hdr_async(stream, check).await?;
    let (mut sink, mut incoming) = ws.split();
    let mut rx = hub.tx.subscribe();

    let mut topics: Vec<String> = vec!["*".into()];
    let mut src_filter: Option<u32> = None;

    sink.send(text(&ServerMsg::Hello {
        protocol: PROTOCOL_VERSION,
        server: format!("rs3-eventd {}", env!("CARGO_PKG_VERSION")),
        sources: hub.reader.sources(),
    }))
    .await?;
    send_snapshots(&mut sink, &hub, src_filter).await?;

    loop {
        tokio::select! {
            msg = rx.recv() => match msg {
                Ok(out) => {
                    if src_filter.is_some_and(|s| s != out.src) || !topic_matches(&topics, &out.topic) {
                        continue;
                    }
                    sink.send(Message::Text(out.json.clone())).await?;
                }
                Err(broadcast::error::RecvError::Lagged(missed)) => {
                    sink.send(text(&ServerMsg::Resync { missed })).await?;
                    send_snapshots(&mut sink, &hub, src_filter).await?;
                }
                Err(broadcast::error::RecvError::Closed) => break,
            },
            inc = incoming.next() => match inc {
                Some(Ok(Message::Text(t))) => match serde_json::from_str::<ClientMsg>(&t) {
                    Ok(ClientMsg::Subscribe { topics: t }) => topics = t,
                    Ok(ClientMsg::Source { src }) => src_filter = src,
                    Ok(ClientMsg::Snapshot) => send_snapshots(&mut sink, &hub, src_filter).await?,
                    Err(e) => sink.send(text(&ServerMsg::Error { message: format!("bad message: {e}") })).await?,
                },
                Some(Ok(Message::Close(_))) | None => break,
                Some(Ok(_)) => {}
                Some(Err(e)) => return Err(e.into()),
            },
        }
    }
    Ok(())
}

async fn send_snapshots<S>(sink: &mut S, hub: &Hub, src: Option<u32>) -> anyhow::Result<()>
where
    S: SinkExt<Message> + Unpin,
    <S as futures_util::Sink<Message>>::Error: std::error::Error + Send + Sync + 'static,
{
    for snapshot in hub.reader.snapshots() {
        if src.is_some_and(|s| s != snapshot.src) {
            continue;
        }
        sink.send(text(&ServerMsg::Snapshot { snapshot })).await?;
    }
    Ok(())
}
