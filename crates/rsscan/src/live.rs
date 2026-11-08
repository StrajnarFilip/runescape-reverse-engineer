//! `rsscan live`: a tiny local web page showing a set of values updating in real time,
//! so a human can match them against the game by eye.

use crate::scan::read_runs;
use rsmem::MemRead;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Clone, Debug)]
pub struct Row {
    pub label: String,
    pub key: Option<i32>,
    pub addr: u64,
}

#[derive(Clone, Debug, Default)]
struct Cell {
    value: Option<i32>,
    changes: u32,
    last_change: Option<Instant>,
    history: Vec<i32>,
}

pub struct Live {
    rows: Vec<Row>,
    cells: Mutex<Vec<Cell>>,
    started: Instant,
    title: String,
}

const PAGE: &str = include_str!("live.html");

fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'%' if i + 2 < b.len() => {
                match u8::from_str_radix(std::str::from_utf8(&b[i + 1..i + 3]).unwrap_or("zz"), 16) {
                    Ok(v) => {
                        out.push(v);
                        i += 3;
                        continue;
                    }
                    Err(_) => out.push(b'%'),
                }
            }
            b'+' => out.push(b' '),
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

impl Live {
    pub fn new(rows: Vec<Row>, title: String) -> Arc<Self> {
        let n = rows.len();
        Arc::new(Self { rows, cells: Mutex::new(vec![Cell::default(); n]), started: Instant::now(), title })
    }

    pub fn len(&self) -> usize {
        self.rows.len()
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    /// Sample forever at `hz` (call on its own thread).
    pub fn sample_loop<M: MemRead>(&self, mem: &M, hz: f64) {
        let mut order: Vec<usize> = (0..self.rows.len()).collect();
        order.sort_by_key(|&i| self.rows[i].addr);
        let addrs: Vec<u64> = order.iter().map(|&i| self.rows[i].addr).collect();
        let period = Duration::from_secs_f64(1.0 / hz.max(1.0));
        loop {
            let now = Instant::now();
            let mut vals = vec![None; addrs.len()];
            read_runs(mem, &addrs, 4, |k, b| vals[k] = b.map(|b| i32::from_le_bytes(b.try_into().unwrap())));
            {
                let mut cells = self.cells.lock().unwrap();
                for (k, v) in vals.into_iter().enumerate() {
                    let c = &mut cells[order[k]];
                    if c.value.is_some() && v.is_some() && c.value != v {
                        c.changes += 1;
                        c.last_change = Some(now);
                        c.history.push(v.unwrap());
                        if c.history.len() > 6 {
                            c.history.remove(0);
                        }
                    }
                    if c.value.is_none() {
                        if let Some(v) = v {
                            c.history.push(v);
                        }
                    }
                    c.value = v;
                }
            }
            let el = now.elapsed();
            if el < period {
                std::thread::sleep(period - el);
            }
        }
    }

    /// Rows that changed, carry a label, or match `q` (key/value/label substring); at most 3000.
    fn json(&self, q: &str) -> String {
        let cells = self.cells.lock().unwrap();
        let now = Instant::now();
        let q = q.to_ascii_lowercase();
        let mut s = String::with_capacity(4096);
        s.push_str(&format!(
            r#"{{"title":{:?},"uptime":{:.1},"total":{},"rows":["#,
            self.title,
            self.started.elapsed().as_secs_f64(),
            self.rows.len()
        ));
        let mut sent = 0;
        for (r, c) in self.rows.iter().zip(cells.iter()) {
            let matches = !q.is_empty()
                && (r.key.is_some_and(|k| k.to_string().contains(&q))
                    || c.value.is_some_and(|v| v.to_string().contains(&q))
                    || r.label.to_ascii_lowercase().contains(&q));
            if !(matches || c.changes > 0 || !r.label.is_empty() || self.rows.len() <= 500) {
                continue;
            }
            if sent >= 3000 {
                break;
            }
            if sent > 0 {
                s.push(',');
            }
            sent += 1;
            let ago = c.last_change.map(|t| now.duration_since(t).as_secs_f64());
            let hist: Vec<String> = c.history.iter().map(|v| v.to_string()).collect();
            s.push_str(&format!(
                r#"{{"label":{:?},"key":{},"addr":"{:#x}","value":{},"changes":{},"ago":{},"hist":[{}]}}"#,
                r.label,
                r.key.map(|k| k.to_string()).unwrap_or_else(|| "null".into()),
                r.addr,
                c.value.map(|v| v.to_string()).unwrap_or_else(|| "null".into()),
                c.changes,
                ago.map(|a| format!("{a:.2}")).unwrap_or_else(|| "null".into()),
                hist.join(",")
            ));
        }
        s.push_str("]}");
        s
    }

    fn handle(&self, mut stream: TcpStream) {
        let mut buf = [0u8; 2048];
        let n = stream.read(&mut buf).unwrap_or(0);
        let req = String::from_utf8_lossy(&buf[..n]);
        let path = req.split_whitespace().nth(1).unwrap_or("/");
        let (ctype, body) = if path.starts_with("/values") {
            let q = path.split_once("?q=").map(|(_, q)| percent_decode(q)).unwrap_or_default();
            ("application/json", self.json(&q))
        } else {
            ("text/html; charset=utf-8", PAGE.to_string())
        };
        let _ = write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
    }

    /// Serve the page forever.
    pub fn serve(self: &Arc<Self>, listener: TcpListener) {
        for s in listener.incoming().flatten() {
            let me = self.clone();
            std::thread::spawn(move || me.handle(s));
        }
    }
}
