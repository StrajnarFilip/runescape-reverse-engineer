use clap::Parser;
use rs3events::{load_bosses, OffsetsFile, Reader, ReaderConfig, Target};
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Stream RS3 client events: WebSocket at /events and a live viewer page at /, on one port.
#[derive(Parser, Debug)]
#[command(version)]
struct Args {
    /// offsets.toml describing where game state lives in memory
    /// (relative paths are also searched next to the executable and its parent folders).
    #[arg(long, default_value = "offsets.toml")]
    offsets: PathBuf,
    /// Directory of boss definitions (*.toml), searched the same way.
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
    /// Open the viewer in the default browser once the server is listening.
    #[arg(long)]
    open: bool,
}

/// Use `p` as given if it exists (or is absolute); otherwise look for it next to the
/// executable and in its parent folders (so `target/release/rs3-eventd.exe` finds the repo files).
fn find_file(p: &Path) -> PathBuf {
    if p.is_absolute() || p.exists() {
        return p.to_path_buf();
    }
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors().skip(1) {
            let c = dir.join(p);
            if c.exists() {
                return c;
            }
        }
    }
    p.to_path_buf()
}

fn open_browser(url: &str) {
    #[cfg(windows)]
    let r = std::process::Command::new("cmd").args(["/C", "start", "", url]).spawn();
    #[cfg(not(windows))]
    let r = std::process::Command::new("xdg-open").arg(url).spawn();
    if let Err(e) = r {
        tracing::warn!("could not open a browser ({e}); open {url} yourself");
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();
    let args = Args::parse();
    let offsets_path = find_file(&args.offsets);
    let bosses_path = find_file(&args.bosses);
    let mut offsets = if offsets_path.exists() {
        tracing::info!("offsets: {}", offsets_path.display());
        OffsetsFile::load(&offsets_path)?
    } else {
        tracing::warn!("{} not found; starting with no signals (run rsscan to discover offsets)", args.offsets.display());
        OffsetsFile::default()
    };
    if let Some(p) = args.period_us {
        offsets.sampler.period_us = p;
    }
    let bosses = load_bosses(&bosses_path)?;
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
    let listener = tokio::net::TcpListener::bind(args.bind)
        .await
        .map_err(|e| anyhow::anyhow!("cannot listen on {} ({e}); is rs3-eventd already running?", args.bind))?;
    let local = listener.local_addr()?;
    let url = format!("http://{local}/");
    println!();
    println!("  RS3 event server running");
    println!("    viewer:    {url}");
    println!("    websocket: ws://{local}/events");
    println!("  It attaches to the RuneScape client automatically (finding values takes ~20-60 s).");
    println!("  Press Ctrl+C to stop.");
    println!();
    if args.open {
        open_browser(&url);
    }
    tokio::select! {
        r = rs3_eventd::serve(listener, hub) => r?,
        _ = tokio::signal::ctrl_c() => tracing::info!("shutting down"),
    }
    reader.stop();
    Ok(())
}
