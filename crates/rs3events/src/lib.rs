//! RS3 event library.
//!
//! ```no_run
//! use rs3events::{Reader, ReaderConfig, Target};
//! let offsets = rs3events::OffsetsFile::load("offsets.toml").unwrap();
//! let bosses = rs3events::load_bosses("data/bosses").unwrap();
//! let reader = Reader::start(ReaderConfig { target: Target::FirstClient, offsets, bosses });
//! let rx = reader.subscribe();
//! for ev in rx {
//!     println!("{}", serde_json_like(&ev));
//! }
//! # fn serde_json_like<T: std::fmt::Debug>(t: &T) -> String { format!("{t:?}") }
//! ```
//!
//! One sampler thread runs per attached client. Each cycle (default 1 ms) it
//! reads every configured signal block twice (torn-read guard), diffs it against
//! the previous cycle and emits typed [`Event`]s wrapped in [`Envelope`]s.

mod emit;
mod gcd;
pub mod locate;
mod position;
mod sampler;
mod tick;

pub use rsmodel::*;
pub use rsoffsets::{load_bosses, BossDef, OffsetsFile};

use crossbeam_channel::{Receiver, Sender};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// Which process(es) to attach to.
#[derive(Clone, Debug)]
pub enum Target {
    /// The lowest-pid game client window (`rs2client.exe` with a `RuneScape*` window).
    FirstClient,
    /// Every game client window, one source each.
    AllClients,
    /// A specific process id.
    Pid(u32),
    /// Every process with this executable name (no window required; used for tests).
    ProcessName(String),
}

pub struct ReaderConfig {
    pub target: Target,
    pub offsets: OffsetsFile,
    pub bosses: Vec<BossDef>,
}

pub(crate) struct Shared {
    pub stop: AtomicBool,
    pub seq: AtomicU64,
    pub t0: Instant,
    pub subs: Mutex<Vec<Sender<Arc<Envelope>>>>,
    pub snapshots: RwLock<BTreeMap<u32, Snapshot>>,
}

/// Handle to the running reader. Dropping it stops all sampler threads.
pub struct Reader {
    shared: Arc<Shared>,
    supervisor: Mutex<Option<JoinHandle<()>>>,
}

impl Reader {
    pub fn start(cfg: ReaderConfig) -> Self {
        let shared = Arc::new(Shared {
            stop: AtomicBool::new(false),
            seq: AtomicU64::new(1),
            t0: Instant::now(),
            subs: Mutex::new(Vec::new()),
            snapshots: RwLock::new(BTreeMap::new()),
        });
        let cfg = Arc::new(cfg);
        let sh = shared.clone();
        let supervisor = std::thread::Builder::new()
            .name("rs3events-supervisor".into())
            .spawn(move || supervise(sh, cfg))
            .expect("spawn supervisor");
        Self { shared, supervisor: Mutex::new(Some(supervisor)) }
    }

    /// Subscribe to all events from now on. If the receiver falls more than
    /// 65 536 events behind it is disconnected; re-subscribe and re-read snapshots.
    pub fn subscribe(&self) -> Receiver<Arc<Envelope>> {
        self.subscribe_with_capacity(1 << 16)
    }

    pub fn subscribe_with_capacity(&self, cap: usize) -> Receiver<Arc<Envelope>> {
        let (tx, rx) = crossbeam_channel::bounded(cap);
        self.shared.subs.lock().unwrap().push(tx);
        rx
    }

    /// Latest full state of every attached source.
    pub fn snapshots(&self) -> Vec<Snapshot> {
        self.shared.snapshots.read().unwrap().values().cloned().collect()
    }

    pub fn snapshot(&self, src: u32) -> Option<Snapshot> {
        self.shared.snapshots.read().unwrap().get(&src).cloned()
    }

    pub fn sources(&self) -> Vec<u32> {
        self.shared.snapshots.read().unwrap().keys().copied().collect()
    }

    /// Stop sampling and join all threads.
    pub fn stop(&self) {
        self.shared.stop.store(true, Ordering::SeqCst);
        if let Some(h) = self.supervisor.lock().unwrap().take() {
            let _ = h.join();
        }
    }
}

impl Drop for Reader {
    fn drop(&mut self) {
        self.stop();
    }
}

fn discover(target: &Target, cfg: &OffsetsFile) -> Vec<(u32, Option<String>)> {
    let alive: std::collections::HashSet<u32> = rsmem::list_processes().into_iter().map(|p| p.pid).collect();
    let found: Vec<(u32, Option<String>)> = match target {
        Target::Pid(p) => vec![(*p, None)],
        Target::FirstClient => rsmem::find_clients(&cfg.client.exe, &cfg.client.window_title_prefix)
            .into_iter()
            .take(1)
            .map(|c| (c.pid, c.launch_world))
            .collect(),
        Target::AllClients => rsmem::find_clients(&cfg.client.exe, &cfg.client.window_title_prefix)
            .into_iter()
            .map(|c| (c.pid, c.launch_world))
            .collect(),
        Target::ProcessName(n) => rsmem::list_processes()
            .into_iter()
            .filter(|p| p.name.eq_ignore_ascii_case(n))
            .map(|p| (p.pid, None))
            .collect(),
    };
    found.into_iter().filter(|(pid, _)| alive.contains(pid)).collect()
}

fn supervise(shared: Arc<Shared>, cfg: Arc<ReaderConfig>) {
    let mut running: BTreeMap<u32, JoinHandle<()>> = BTreeMap::new();
    let mut finished: std::collections::HashSet<u32> = Default::default();
    while !shared.stop.load(Ordering::SeqCst) {
        running.retain(|pid, h| {
            if h.is_finished() {
                finished.insert(*pid);
                false
            } else {
                true
            }
        });
        for (pid, label) in discover(&cfg.target, &cfg.offsets) {
            // A pid whose sampler ended is only retried if it is still alive later
            // (sampler exits on process death or a fatal attach error).
            if running.contains_key(&pid) || (finished.contains(&pid) && matches!(cfg.target, Target::Pid(_))) {
                continue;
            }
            let sh = shared.clone();
            let c = cfg.clone();
            match std::thread::Builder::new()
                .name(format!("rs3events-sampler-{pid}"))
                .spawn(move || sampler::run(sh, c, pid, label))
            {
                Ok(h) => {
                    running.insert(pid, h);
                }
                Err(e) => tracing::error!("failed to spawn sampler for {pid}: {e}"),
            }
        }
        for _ in 0..10 {
            if shared.stop.load(Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    for (_, h) in running {
        let _ = h.join();
    }
}
