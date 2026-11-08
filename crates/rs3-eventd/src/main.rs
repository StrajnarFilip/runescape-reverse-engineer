use clap::Parser;
use rs3events::{load_bosses, OffsetsFile, Reader, ReaderConfig, Target};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

/// Stream RS3 client events over a localhost WebSocket.
#[derive(Parser, Debug)]
#[command(version)]
struct Args {
    /// offsets.toml describing where game state lives in memory.
    #[arg(long, default_value = "offsets.toml")]
    offsets: PathBuf,
    /// Directory of boss definitions (*.toml).
    #[arg(long, default_value = "data/bosses")]
    bosses: PathBuf,
    /// Attach to this process id instead of auto-discovering the game window.
    #[arg(long)]
    pid: Option<u32>,
    /// Attach to every running client (one source each) instead of the first.
    #[arg(long)]
    all: bool,
    /// Attach to processes with this exe name (no window required).
    #[arg(long)]
    process_name: Option<String>,
    /// Address to listen on. Keep it on localhost.
    #[arg(long, default_value = "127.0.0.1:7878")]
    bind: SocketAddr,
    /// Override the sampling period (microseconds).
    #[arg(long)]
    period_us: Option<u64>,
    /// Append every event as a JSON line to this file.
    #[arg(long)]
    record: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();
    let args = Args::parse();
    let mut offsets = if args.offsets.exists() {
        OffsetsFile::load(&args.offsets)?
    } else {
        tracing::warn!("{} not found; starting with no signals (run rsscan to discover offsets)", args.offsets.display());
        OffsetsFile::default()
    };
    if let Some(p) = args.period_us {
        offsets.sampler.period_us = p;
    }
    let bosses = load_bosses(&args.bosses)?;
    let target = match (args.pid, &args.process_name, args.all) {
        (Some(p), _, _) => Target::Pid(p),
        (None, Some(n), _) => Target::ProcessName(n.clone()),
        (None, None, true) => Target::AllClients,
        _ => Target::FirstClient,
    };
    if !args.bind.ip().is_loopback() {
        tracing::warn!("binding to non-loopback address {}: anyone on your network can read the stream", args.bind);
    }
    tracing::info!("target {target:?}, {} signals, {} bosses", offsets.signals.len(), bosses.len());
    let reader = Arc::new(Reader::start(ReaderConfig { target, offsets, bosses }));
    let hub = rs3_eventd::Hub::start(reader.clone(), args.record.clone())?;
    let listener = tokio::net::TcpListener::bind(args.bind).await?;
    let local = listener.local_addr()?;
    tracing::info!("listening on ws://{local}/events (viewer: http://{local}/)");
    tokio::select! {
        r = rs3_eventd::serve(listener, hub) => r?,
        _ = tokio::signal::ctrl_c() => tracing::info!("shutting down"),
    }
    reader.stop();
    Ok(())
}
