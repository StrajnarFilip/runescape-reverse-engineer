//! One sampler per attached client process.

use crate::emit::{unix_us, Emitter};
use crate::gcd::GcdTracker;
use crate::tick::{TickTracker, TickUpdate};
use crate::{ReaderConfig, Shared};
use rsmem::{pe, MemRead, ModuleInfo, Process, ReadError, Ticker};
use rsmodel::{Event, NpcInfo, Num, PlayerState, Snapshot, SourceState};
use rsoffsets::{resolve_anchors, Anchors, BossDef, CodeCache, GcdUnit, NpcLayout, OffsetsFile, SignalDef, ValType};
use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Largest gap between slots kept in one block read.
const MAX_BLOCK_GAP: i64 = 64 * 1024;

#[derive(Clone, PartialEq, Eq, Hash, Debug)]
enum GroupKey {
    Base { base: String, chain: Vec<i64> },
    Var { array: String },
}

struct Slot {
    idx: usize,
    pos: usize,
    ty: ValType,
    bits: Option<(u8, u8)>,
    scale: Option<f64>,
}

struct Group {
    key: GroupKey,
    lo: i64,
    slots: Vec<Slot>,
    a: Vec<u8>,
    b: Vec<u8>,
}

/// Percentage rounded to 0.1 (avoids 28.000000000000004 in the output).
fn pct1(v: f64, max: f64) -> f64 {
    (v / max * 1000.0).round() / 10.0
}

fn decode(ty: ValType, bytes: &[u8], bits: Option<(u8, u8)>, scale: Option<f64>) -> Num {
    let v = ty.decode_bits(bytes, bits);
    match scale {
        Some(s) => Num::F(v.as_f64() * s),
        None => v,
    }
}

/// Build block-read groups: signals sharing an address root are read in one span.
fn build_groups(cfg: &OffsetsFile, defs: &[(String, SignalDef)]) -> Vec<Group> {
    let mut by_key: HashMap<GroupKey, Vec<(usize, i64, &SignalDef)>> = HashMap::new();
    for (idx, (_, d)) in defs.iter().enumerate() {
        let (key, rel) = match (&d.base, d.var) {
            (Some(b), _) => (GroupKey::Base { base: b.clone(), chain: d.chain.clone() }, d.offset),
            (None, Some(id)) => {
                let stride = cfg.vars.get(&d.var_array).map(|v| v.stride).unwrap_or(4) as i64;
                (GroupKey::Var { array: d.var_array.clone() }, id * stride + d.offset)
            }
            (None, None) => continue,
        };
        by_key.entry(key).or_default().push((idx, rel, d));
    }
    let mut groups = Vec::new();
    for (key, mut items) in by_key {
        items.sort_by_key(|x| x.1);
        let mut cur: Vec<(usize, i64, &SignalDef)> = Vec::new();
        let flush = |cur: &mut Vec<(usize, i64, &SignalDef)>, groups: &mut Vec<Group>| {
            if cur.is_empty() {
                return;
            }
            let lo = cur[0].1;
            let hi = cur.iter().map(|(_, r, d)| r + d.ty.size() as i64).max().unwrap();
            let slots = cur
                .iter()
                .map(|(idx, r, d)| Slot { idx: *idx, pos: (r - lo) as usize, ty: d.ty, bits: d.bits, scale: d.scale })
                .collect();
            let len = (hi - lo) as usize;
            groups.push(Group { key: key.clone(), lo, slots, a: vec![0; len], b: vec![0; len] });
            cur.clear();
        };
        for it in items {
            if let Some(first) = cur.first() {
                if it.1 - first.1 > MAX_BLOCK_GAP {
                    flush(&mut cur, &mut groups);
                }
            }
            cur.push(it);
        }
        flush(&mut cur, &mut groups);
    }
    groups
}

fn base_addr<M: MemRead>(mem: &M, cfg: &OffsetsFile, anchors: &Anchors, base: &str) -> Result<u64, ReadError> {
    let a = *anchors.map.get(base).ok_or(ReadError { addr: 0, len: 0, code: 0 })?;
    mem.follow_chain(a, &cfg.bases[base].chain)
}

fn group_addr<M: MemRead>(mem: &M, cfg: &OffsetsFile, anchors: &Anchors, key: &GroupKey) -> Result<u64, ReadError> {
    match key {
        GroupKey::Base { base, chain } => mem.follow_chain(base_addr(mem, cfg, anchors, base)?, chain),
        GroupKey::Var { array } => {
            let v = &cfg.vars[array];
            mem.follow_chain(base_addr(mem, cfg, anchors, &v.base)?, &v.chain)
        }
    }
}

/// Read the group's span twice until the slot bytes agree (torn-read guard).
/// On success the accepted bytes are in `g.a`.
fn read_group<M: MemRead>(mem: &M, g: &mut Group, addr: u64, retries: u32) -> bool {
    let start = addr.wrapping_add(g.lo as u64);
    if mem.read_into(start, &mut g.a).is_err() {
        return false;
    }
    for _ in 0..=retries {
        if mem.read_into(start, &mut g.b).is_err() {
            return false;
        }
        let same = g.slots.iter().all(|s| {
            let n = s.ty.size();
            g.a[s.pos..s.pos + n] == g.b[s.pos..s.pos + n]
        });
        std::mem::swap(&mut g.a, &mut g.b);
        if same {
            return true;
        }
    }
    true // values kept changing; accept the newest read
}

/// A signal read by key from a located container.
struct Keyed {
    idx: usize,
    locator: String,
    key: i64,
    ty: ValType,
    bits: Option<(u8, u8)>,
    scale: Option<f64>,
    key_off: u32,
    value_off: u32,
    /// Entry (table row or hash node) address once located.
    entry: Option<u64>,
    /// For vtable-object locators: the expected qword at +0 (validated every read).
    vtable: Option<u64>,
    is_vtable: bool,
    /// Other live copies (hash nodes) to fail over to before rescanning.
    alternates: Vec<u64>,
    /// Read through a var index every cycle (no cached entry).
    is_index: bool,
}

struct Locators {
    tx: crossbeam_channel::Sender<crate::locate::LocateResult>,
    rx: crossbeam_channel::Receiver<crate::locate::LocateResult>,
    pending: std::collections::HashSet<String>,
    last_request: HashMap<String, Instant>,
    tables: HashMap<String, (u64, u64, bool)>,
    /// Located var indexes: (array start, bucket count).
    indexes: HashMap<String, (u64, u64)>,
    errors: HashMap<String, String>,
}

struct NpcBufs {
    table: Vec<u8>,
    a: Vec<u8>,
    b: Vec<u8>,
}

pub(crate) struct Sampler {
    proc: Process,
    em: Emitter,
    shared: Arc<Shared>,
    cfg: Arc<ReaderConfig>,
    label: Option<String>,
    build: Option<String>,
    build_note: Option<String>,
    modules: Vec<ModuleInfo>,
    main: ModuleInfo,
    code: CodeCache,
    anchors: Anchors,
    last_anchor_try: Instant,
    defs: Vec<(String, SignalDef)>,
    index: HashMap<String, usize>,
    groups: Vec<Group>,
    keyed: Vec<Keyed>,
    loc: Locators,
    cur: Vec<Option<Num>>,
    prev: Vec<Option<Num>>,
    state: SourceState,
    bad_since: Option<Instant>,
    failing: Vec<String>,
    tick: TickTracker,
    gcd: GcdTracker,
    npcs: BTreeMap<u64, NpcInfo>,
    last_npc_poll: Option<Instant>,
    npc_bufs: Option<NpcBufs>,
    dirty: bool,
}

impl Sampler {
    fn attach(proc: Process, shared: Arc<Shared>, cfg: Arc<ReaderConfig>, label: Option<String>) -> anyhow::Result<Self> {
        let pid = proc.pid();
        let modules = proc.modules()?;
        let main = proc.main_module()?;
        let build = pe::read_headers(&proc, main.base).ok().map(|h| h.build_id());
        let o = &cfg.offsets;
        let build_note = match (&o.client.build, &build) {
            (Some(want), Some(have)) if want != have => {
                Some(format!("offsets.toml was made for build {want}, client is {have}; relying on signatures + checks"))
            }
            _ => None,
        };
        let mut code = CodeCache::default();
        let anchors = resolve_anchors(o, &proc, &modules, &main, &mut code);
        for n in &anchors.notes {
            tracing::warn!("[{pid}] {n}");
        }
        for f in &anchors.failures {
            tracing::error!("[{pid}] {f}");
        }
        let defs: Vec<(String, SignalDef)> = o.signals.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        let index = defs.iter().enumerate().map(|(i, (k, _))| (k.clone(), i)).collect();
        let groups = build_groups(o, &defs);
        let keyed: Vec<Keyed> = defs
            .iter()
            .enumerate()
            .filter_map(|(idx, (_, d))| {
                let l = d.locator.as_ref()?;
                let def = o.locators.get(l)?;
                let is_vtable = matches!(def, rsoffsets::LocatorDef::VtableObject { .. });
                Some(Keyed {
                    idx,
                    locator: l.clone(),
                    key: d.key?,
                    ty: d.ty,
                    bits: d.bits,
                    scale: d.scale,
                    key_off: def.key_off(),
                    value_off: if is_vtable { d.offset as u32 } else { def.value_off() },
                    entry: None,
                    vtable: None,
                    is_vtable,
                    alternates: Vec::new(),
                    is_index: matches!(def, rsoffsets::LocatorDef::VarIndex { .. }),
                })
            })
            .collect();
        let (tx, rx) = crossbeam_channel::unbounded();
        let loc = Locators {
            tx,
            rx,
            pending: Default::default(),
            last_request: Default::default(),
            tables: Default::default(),
            indexes: Default::default(),
            errors: Default::default(),
        };
        let n = defs.len();
        let npc_bufs = o.npcs.as_ref().map(|d| NpcBufs {
            table: vec![0; (d.capacity as usize) * (d.stride as usize)],
            a: vec![0; d.struct_size as usize],
            b: vec![0; d.struct_size as usize],
        });
        Ok(Self {
            em: Emitter::new(shared.clone(), pid),
            proc,
            shared,
            label,
            build,
            build_note,
            modules,
            main,
            code,
            anchors,
            last_anchor_try: Instant::now(),
            defs,
            index,
            groups,
            keyed,
            loc,
            cur: vec![None; n],
            prev: vec![None; n],
            state: SourceState::Attaching,
            bad_since: None,
            failing: Vec::new(),
            tick: TickTracker::new(shared_origin()),
            gcd: GcdTracker::new(cfg.offsets.gcd.ticks),
            npcs: BTreeMap::new(),
            last_npc_poll: None,
            npc_bufs,
            dirty: true,
            cfg,
        })
    }

    fn o(&self) -> &OffsetsFile {
        &self.cfg.offsets
    }

    fn emit(&self, ev: Event, now: Instant) {
        self.em.emit(ev, self.tick.stamp(now));
    }

    fn status(&self, detail: Option<String>) {
        let detail = match (detail, &self.build_note) {
            (Some(d), Some(b)) => Some(format!("{d}; {b}")),
            (d, b) => d.or_else(|| b.clone()),
        };
        self.em.emit(
            Event::Status {
                state: self.state,
                build: self.build.clone(),
                label: self.label.clone(),
                failing: self.failing.clone(),
                detail,
            },
            (None, None),
        );
    }

    fn value(&self, name: &str) -> Option<Num> {
        self.index.get(name).and_then(|&i| self.cur[i])
    }

    fn max_of(&self, name: &str) -> Option<i64> {
        self.value(&format!("{name}_max"))
            .map(|n| n.as_i64())
            .or_else(|| self.index.get(name).and_then(|&i| self.defs[i].1.max))
    }

    /// Ask the background scanner to locate `name` (rate-limited per locator).
    fn request_locate(&mut self, name: &str, now: Instant) {
        if self.loc.pending.contains(name) {
            return;
        }
        if self.loc.last_request.get(name).is_some_and(|t| now.duration_since(*t) < Duration::from_secs(20)) {
            return;
        }
        let Some(def) = self.cfg.offsets.locators.get(name).cloned() else { return };
        let mut ctx = crate::locate::LocateCtx { via: None };
        if let rsoffsets::LocatorDef::VarIndex { via, .. } = &def {
            // Needs its `via` table first; try again on a later cycle until it is located.
            match self.loc.tables.get(via).copied() {
                Some(t) => ctx.via = Some((self.cfg.offsets.locators[via].clone(), t)),
                None => {
                    let via = via.clone();
                    self.request_locate(&via, now);
                    return;
                }
            }
        }
        let keys: Vec<i64> = self.keyed.iter().filter(|k| k.locator == name).map(|k| k.key).collect();
        self.loc.pending.insert(name.to_string());
        self.loc.last_request.insert(name.to_string(), now);
        let (tx, pid, n) = (self.loc.tx.clone(), self.proc.pid(), name.to_string());
        tracing::info!("[{pid}] locating {n} (scanning memory for keys {keys:?})");
        let _ = std::thread::Builder::new()
            .name(format!("rs3events-locate-{n}"))
            .spawn(move || {
                let _ = tx.send(crate::locate::run_locate(pid, n, def, keys, ctx));
            });
    }

    fn poll_locates(&mut self) {
        while let Ok(r) = self.loc.rx.try_recv() {
            self.loc.pending.remove(&r.locator);
            let pid = self.proc.pid();
            match r.result {
                Ok(found) => {
                    let mut vt = None;
                    let mut alts: HashMap<i64, Vec<u64>> = HashMap::new();
                    let entries = match found {
                        crate::locate::Located::Table { start, count, desc, entries } => {
                            tracing::info!(
                                "[{pid}] {} located in {:.1}s: table {start:#x} x {count} ({})",
                                r.locator,
                                r.secs,
                                if desc { "descending" } else { "ascending" }
                            );
                            self.loc.tables.insert(r.locator.clone(), (start, count, desc));
                            entries
                        }
                        crate::locate::Located::Nodes { entries, alternates } => {
                            let copies: Vec<String> = alternates.iter().map(|(k, v)| format!("{k}x{}", v.len())).collect();
                            tracing::info!("[{pid}] {} located in {:.1}s: live copies {}", r.locator, r.secs, copies.join(", "));
                            alts = alternates;
                            entries
                        }
                        crate::locate::Located::Objects { vtable, entries, count, alternates } => {
                            tracing::info!(
                                "[{pid}] {} located in {:.1}s: {count} objects with vtable {vtable:#x}, {} agreeing copies",
                                r.locator,
                                r.secs,
                                alternates.len()
                            );
                            vt = Some(vtable);
                            alts = HashMap::from([(0, alternates)]);
                            entries
                        }
                        crate::locate::Located::Index { start, buckets } => {
                            tracing::info!("[{pid}] {} located in {:.1}s: var index {start:#x}, {buckets} buckets", r.locator, r.secs);
                            self.loc.indexes.insert(r.locator.clone(), (start, buckets));
                            HashMap::new()
                        }
                    };
                    self.loc.errors.remove(&r.locator);
                    for k in self.keyed.iter_mut().filter(|k| k.locator == r.locator) {
                        k.entry = entries.get(&k.key).copied();
                        k.vtable = vt;
                        k.alternates = alts.get(&k.key).cloned().unwrap_or_default();
                    }
                }
                Err(e) => {
                    tracing::warn!("[{pid}] locating {} failed after {:.1}s: {e}", r.locator, r.secs);
                    self.loc.errors.insert(r.locator.clone(), e);
                }
            }
            self.dirty = true;
        }
    }

    fn read_keyed(&mut self, now: Instant) {
        let mut need: Vec<String> = Vec::new();
        for i in 0..self.keyed.len() {
            let k = &self.keyed[i];
            if k.is_index {
                let l = k.locator.clone();
                let Some(&(start, buckets)) = self.loc.indexes.get(&l) else {
                    if !self.loc.pending.contains(&l) {
                        need.push(l);
                    }
                    continue;
                };
                let n = k.ty.size();
                let next_off = match self.cfg.offsets.locators.get(&l) {
                    Some(rsoffsets::LocatorDef::VarIndex { entry_next_off, .. }) => *entry_next_off,
                    _ => 0x28,
                };
                let mut vbuf = [0u8; 8];
                let hit = crate::locate::index_lookup(&self.proc, start, buckets, k.key, k.key_off, next_off)
                    .filter(|p| self.proc.read_into(p + k.value_off as u64, &mut vbuf[..n]).is_ok());
                match hit {
                    Some(_) => self.cur[k.idx] = Some(decode(k.ty, &vbuf, k.bits, k.scale)),
                    None => {
                        // Index gone (rebuilt/freed) if even the probe keys no longer resolve.
                        let probes_ok = match self.cfg.offsets.locators.get(&l) {
                            Some(rsoffsets::LocatorDef::VarIndex { probe_keys, .. }) => probe_keys
                                .iter()
                                .all(|pk| crate::locate::index_lookup(&self.proc, start, buckets, *pk, k.key_off, next_off).is_some()),
                            _ => false,
                        };
                        if !probes_ok {
                            tracing::warn!("[{}] {l}: var index no longer valid; re-locating", self.proc.pid());
                            self.loc.indexes.remove(&l);
                            need.push(l);
                        }
                    }
                }
                continue;
            }
            if k.is_vtable {
                let ok = match (k.entry, k.vtable) {
                    (Some(e), Some(vt)) => self.proc.read::<u64>(e).is_ok_and(|q| q == vt),
                    _ => false,
                };
                let mut vbuf = [0u8; 8];
                if ok {
                    let n = k.ty.size();
                    if let Some(e) = k.entry {
                        if self.proc.read_into(e + k.value_off as u64, &mut vbuf[..n]).is_ok() {
                            self.cur[k.idx] = Some(decode(k.ty, &vbuf, k.bits, k.scale));
                            continue;
                        }
                    }
                }
                // Fail over to another object that still carries the vtable.
                let (cur, vt) = (k.entry, k.vtable);
                let alt = vt.and_then(|vt| {
                    self.keyed[i].alternates.iter().copied().filter(|a| Some(*a) != cur).find(|a| self.proc.read::<u64>(*a).is_ok_and(|q| q == vt))
                });
                if let Some(a) = alt {
                    tracing::info!("[{}] {}: object freed, failing over to copy {a:#x}", self.proc.pid(), self.keyed[i].locator);
                    self.keyed[i].alternates.retain(|x| Some(*x) != cur);
                    self.keyed[i].entry = Some(a);
                    continue;
                }
                let l = self.keyed[i].locator.clone();
                if self.keyed[i].entry.is_some() || !self.loc.pending.contains(&l) {
                    self.keyed[i].entry = None;
                    need.push(l);
                }
                continue;
            }
            let lo = k.key_off.min(k.value_off) as u64;
            let hi = (k.key_off as u64 + 4).max(k.value_off as u64 + k.ty.size() as u64);
            let mut buf = [0u8; 64];
            let len = (hi - lo) as usize;
            let ok = match k.entry {
                Some(e) if len <= buf.len() => self.proc.read_into(e + lo, &mut buf[..len]).is_ok() && {
                    let ko = (k.key_off as u64 - lo) as usize;
                    i32::from_le_bytes(buf[ko..ko + 4].try_into().unwrap()) as i64 == k.key
                },
                _ => false,
            };
            if ok {
                let vo = (k.value_off as u64 - lo) as usize;
                self.cur[k.idx] = Some(decode(k.ty, &buf[vo..], k.bits, k.scale));
                continue;
            }
            // Entry missing or moved: cheap re-lookup in a known sorted table, else rescan.
            let (locator, key, key_off) = (k.locator.clone(), k.key, k.key_off);
            let mut fixed = None;
            if let (Some(&(start, count, desc)), Some(rsoffsets::LocatorDef::SortedTable { stride, .. })) =
                (self.loc.tables.get(&locator), self.cfg.offsets.locators.get(&locator))
            {
                fixed = crate::locate::table_lookup(&self.proc, start, count, desc, *stride, key_off, key);
            }
            if fixed.is_none() {
                // Fail over to another live copy whose key still matches.
                let cur = self.keyed[i].entry;
                fixed = self.keyed[i]
                    .alternates
                    .iter()
                    .copied()
                    .filter(|a| Some(*a) != cur)
                    .find(|a| self.proc.read::<i32>(a + key_off as u64).is_ok_and(|k| k as i64 == key));
                if let Some(a) = fixed {
                    tracing::info!("[{}] {locator} key {key}: entry moved, failing over to copy {a:#x}", self.proc.pid());
                    self.keyed[i].alternates.retain(|x| Some(*x) != cur);
                }
            }
            match fixed {
                Some(e) => self.keyed[i].entry = Some(e),
                None => {
                    if self.keyed[i].entry.is_some() || !self.loc.pending.contains(&locator) {
                        self.keyed[i].entry = None;
                        self.loc.tables.remove(&locator);
                        need.push(locator);
                    }
                }
            }
        }
        need.sort();
        need.dedup();
        for l in need {
            self.request_locate(&l, now);
        }
    }

    fn read_signals(&mut self) {
        for v in self.cur.iter_mut() {
            *v = None;
        }
        let retries = self.cfg.offsets.sampler.torn_read_retries;
        for g in self.groups.iter_mut() {
            let Ok(addr) = group_addr(&self.proc, &self.cfg.offsets, &self.anchors, &g.key) else {
                continue;
            };
            if !read_group(&self.proc, g, addr, retries) {
                continue;
            }
            for s in &g.slots {
                self.cur[s.idx] = Some(decode(s.ty, &g.a[s.pos..], s.bits, s.scale));
            }
        }
    }

    /// Returns (desired state, failing reasons).
    fn evaluate(&self) -> (SourceState, Vec<String>) {
        let o = self.o();
        if o.signals.is_empty() && o.npcs.is_none() {
            return (SourceState::OffsetsStale, vec!["offsets.toml defines no signals or npcs yet".into()]);
        }
        if !self.anchors.failures.is_empty() {
            return (SourceState::OffsetsStale, self.anchors.failures.clone());
        }
        let mut unreadable = Vec::new();
        let mut violated = Vec::new();
        if o.checks.is_empty() {
            if !self.cur.is_empty() && self.cur.iter().all(|v| v.is_none()) {
                unreadable.push("no signal is readable".to_string());
            }
        }
        for c in &o.checks {
            let Some(v) = self.value(&c.signal) else {
                let why = o
                    .signals
                    .get(&c.signal)
                    .and_then(|s| s.locator.as_ref())
                    .map(|l| {
                        if self.loc.pending.contains(l) {
                            format!("locating {l} (scanning memory, ~1 min)")
                        } else if let Some(e) = self.loc.errors.get(l) {
                            format!("locator {l}: {e}")
                        } else {
                            format!("{} unreadable", c.signal)
                        }
                    })
                    .unwrap_or_else(|| format!("{} unreadable", c.signal));
                unreadable.push(why);
                continue;
            };
            let v = v.as_f64();
            if c.min.is_some_and(|m| v < m) || c.max.is_some_and(|m| v > m) {
                violated.push(format!("{}={} outside [{:?}, {:?}]", c.signal, v, c.min, c.max));
            }
            if let Some(other) = &c.le_signal {
                match self.value(other) {
                    Some(w) if v > w.as_f64() => violated.push(format!("{}={} > {}={}", c.signal, v, other, w.as_f64())),
                    None => unreadable.push(format!("{other} unreadable")),
                    _ => {}
                }
            }
        }
        unreadable.dedup();
        violated.dedup();
        if !violated.is_empty() {
            (SourceState::OffsetsStale, violated)
        } else if !unreadable.is_empty() {
            (SourceState::Waiting, unreadable)
        } else {
            (SourceState::Ok, Vec::new())
        }
    }

    fn update_state(&mut self, now: Instant) {
        let (want, failing) = self.evaluate();
        if want == self.state {
            self.bad_since = None;
            if want != SourceState::Ok && failing != self.failing {
                self.failing = failing;
                self.status(None);
                self.dirty = true;
            }
            return;
        }
        if self.state == SourceState::Ok {
            // Leave `ok` only after the grace period (loading screens, brief nulls).
            let since = *self.bad_since.get_or_insert(now);
            if now.duration_since(since) < Duration::from_millis(self.o().sampler.check_grace_ms) {
                return;
            }
            self.leave_ok(now);
        }
        self.bad_since = None;
        self.state = want;
        self.failing = failing;
        if want == SourceState::Ok {
            for p in self.prev.iter_mut() {
                *p = None;
            }
        }
        self.status(None);
        self.dirty = true;
    }

    fn leave_ok(&mut self, now: Instant) {
        let gone: Vec<NpcInfo> = std::mem::take(&mut self.npcs).into_values().collect();
        for n in gone {
            self.emit(Event::NpcDespawn { uid: n.uid, npc_id: n.npc_id }, now);
        }
        self.gcd.active = None;
        self.tick.reset();
    }

    fn process_signals(&mut self, now: Instant) {
        let now_unix = unix_us();
        // 1. Tick first so every other event is stamped with the new tick.
        if let Some(&ti) = self.index.get("tick") {
            if let Some(v) = self.cur[ti] {
                if let Some(u) = self.tick.on_counter(v.as_i64(), now) {
                    self.emit_tick(u, now);
                }
            }
        }
        if !self.tick.has_counter() {
            if let Some(u) = self.tick.poll_estimated(now) {
                self.emit_tick(u, now);
            }
        }

        let gcd_cfg = self.o().gcd.clone();
        for i in 0..self.defs.len() {
            let (old, new) = (self.prev[i], self.cur[i]);
            let Some(new) = new else { continue };
            if old == Some(new) {
                continue;
            }
            self.dirty = true;
            let name = self.defs[i].0.clone();
            let old_i = old.map(|o| o.as_i64());
            let new_i = new.as_i64();
            match name.as_str() {
                "tick" => {}
                "lifepoints" => {
                    self.tick.observe_server_change(now);
                    let max = self.max_of("lifepoints");
                    self.emit(Event::Lifepoints { old: old_i, new: new_i, max }, now);
                }
                "prayer" => {
                    self.tick.observe_server_change(now);
                    let max = self.max_of("prayer");
                    self.emit(Event::Prayer { old: old_i, new: new_i, max }, now);
                }
                "adrenaline" => {
                    self.tick.observe_server_change(now);
                    let pct = self.max_of("adrenaline").filter(|m| *m > 0).map(|m| pct1(new.as_f64(), m as f64));
                    self.emit(Event::Adrenaline { old: old_i, new: new_i, pct }, now);
                    if gcd_cfg.derive_from_adrenaline && old.is_some() && !self.gcd.is_active() {
                        let basis = "adrenaline_heuristic".to_string();
                        self.emit(Event::AbilityUsed { basis: basis.clone(), value: Some(new) }, now);
                        let ev = self.gcd.start(basis, now, now_unix, &self.tick);
                        self.emit(ev, now);
                    }
                }
                n if gcd_cfg.ability_signal.as_deref() == Some(n) => {
                    self.tick.observe_server_change(now);
                    if old.is_some() {
                        let basis = format!("signal:{n}");
                        self.emit(Event::AbilityUsed { basis: basis.clone(), value: Some(new) }, now);
                        let ev = self.gcd.start(basis, now, now_unix, &self.tick);
                        self.emit(ev, now);
                    }
                }
                n if gcd_cfg.remaining_signal.as_deref() == Some(n) => {
                    let basis = format!("signal:{n}");
                    let v = new.as_f64();
                    match gcd_cfg.remaining_unit {
                        GcdUnit::Ticks | GcdUnit::Ms => {
                            let ms = if gcd_cfg.remaining_unit == GcdUnit::Ticks { v * self.tick.interval_ms() } else { v };
                            let was = old.map(|o| o.as_f64()).unwrap_or(0.0);
                            if v > 0.0 && (was <= 0.0 || v > was) {
                                let ev = self.gcd.start_remaining(basis, ms, now, now_unix, &self.tick);
                                self.emit(ev, now);
                            } else if v <= 0.0 {
                                if let Some(b) = self.gcd.finish() {
                                    self.emit(Event::GcdReady { basis: b }, now);
                                }
                            }
                        }
                        GcdUnit::EndTick => {
                            let end = new.as_i64().max(0) as u64;
                            if self.tick.counter_tick().is_some_and(|c| end > c) {
                                let ev = self.gcd.start_end_tick(basis, end, now, now_unix, &self.tick);
                                self.emit(ev, now);
                            }
                        }
                    }
                }
                _ => {
                    self.emit(Event::Signal { name: name.clone(), old, new }, now);
                }
            }
        }

        // End-tick GCD from a direct signal: ready when the counter reaches it.
        if gcd_cfg.remaining_unit == GcdUnit::EndTick {
            if let (Some(a), Some(c)) = (&self.gcd.active, self.tick.counter_tick()) {
                if a.end_tick.is_some_and(|e| c >= e) {
                    if let Some(b) = self.gcd.finish() {
                        self.emit(Event::GcdReady { basis: b }, now);
                    }
                }
            }
        }
        if let Some(b) = self.gcd.poll(now, &self.tick) {
            self.emit(Event::GcdReady { basis: b }, now);
            self.dirty = true;
        }
        self.prev.clone_from(&self.cur);
    }

    fn emit_tick(&mut self, u: TickUpdate, now: Instant) {
        self.dirty = true;
        self.emit(Event::Tick { n: u.n, basis: u.basis, interval_ms: u.interval_ms }, now);
    }

    fn boss_for(&self, npc_id: Option<i32>) -> Option<&BossDef> {
        let id = npc_id?;
        self.cfg.bosses.iter().find(|b| b.matches(id))
    }

    fn read_npcs(&mut self) -> Option<BTreeMap<u64, NpcInfo>> {
        let def = self.cfg.offsets.npcs.as_ref()?;
        let bufs = self.npc_bufs.as_mut()?;
        let o = &self.cfg.offsets;
        let base = base_addr(&self.proc, o, &self.anchors, &def.base).ok()?;
        let table = self.proc.follow_chain(base, &def.chain).ok()?;
        self.proc.read_into(table, &mut bufs.table).ok()?;
        let retries = o.sampler.torn_read_retries;
        let mut out = BTreeMap::new();
        for i in 0..def.capacity as usize {
            let (addr, default_uid) = match def.layout {
                NpcLayout::PtrArray => {
                    let off = i * def.stride as usize;
                    let p = u64::from_le_bytes(bufs.table[off..off + 8].try_into().unwrap());
                    if p == 0 {
                        continue;
                    }
                    (p.wrapping_add(def.entry_offset as u64), p)
                }
                NpcLayout::Inline => (table + (i as u64) * def.stride as u64, i as u64),
            };
            if self.proc.read_into(addr, &mut bufs.a).is_err() {
                continue;
            }
            for _ in 0..=retries {
                if self.proc.read_into(addr, &mut bufs.b).is_err() {
                    break;
                }
                let same = def.fields.values().all(|f| {
                    let (s, n) = (f.offset as usize, f.ty.size());
                    s + n > bufs.a.len() || bufs.a[s..s + n] == bufs.b[s..s + n]
                });
                std::mem::swap(&mut bufs.a, &mut bufs.b);
                if same {
                    break;
                }
            }
            let mut vals: BTreeMap<&str, Num> = BTreeMap::new();
            for (name, f) in &def.fields {
                let (s, n) = (f.offset as usize, f.ty.size());
                if s + n <= bufs.a.len() {
                    vals.insert(name.as_str(), f.ty.decode_bits(&bufs.a[s..], f.bits));
                }
            }
            if vals.get("valid").is_some_and(|v| v.as_i64() == 0) {
                continue;
            }
            let id = vals.get("id").map(|v| v.as_i64());
            if let Some(id) = id {
                if id < def.id_range.0 || id > def.id_range.1 {
                    continue;
                }
            }
            let uid = vals.get("uid").map(|v| v.as_i64() as u64).unwrap_or(default_uid);
            let geti = |k: &str| vals.get(k).map(|v| v.as_i64());
            let hp = geti("hp");
            let hp_max = geti("hp_max");
            let headbar = geti("headbar");
            let hp_pct = match (hp, hp_max, headbar, def.headbar_max) {
                (Some(h), Some(m), _, _) if m > 0 => Some(pct1(h as f64, m as f64)),
                (_, _, Some(h), Some(m)) if m > 0 => Some(pct1(h as f64, m as f64)),
                _ => None,
            };
            let mut extra = BTreeMap::new();
            for (k, v) in &vals {
                if !matches!(*k, "id" | "uid" | "valid" | "x" | "y" | "plane" | "anim" | "spotanim" | "hp" | "hp_max" | "headbar") {
                    extra.insert(k.to_string(), *v);
                }
            }
            let npc_id = id.map(|i| i as i32);
            out.insert(
                uid,
                NpcInfo {
                    uid,
                    npc_id,
                    name: None,
                    x: geti("x").map(|v| v as i32),
                    y: geti("y").map(|v| v as i32),
                    plane: geti("plane").map(|v| v as i32),
                    hp: hp.or(headbar),
                    hp_max: hp_max.or(if hp.is_none() { def.headbar_max } else { None }),
                    hp_pct,
                    anim: geti("anim").map(|v| v as i32),
                    spotanim: geti("spotanim").map(|v| v as i32),
                    extra,
                },
            );
        }
        for n in out.values_mut() {
            n.name = self.cfg.bosses.iter().find(|b| n.npc_id.is_some_and(|id| b.matches(id))).map(|b| b.display().to_string());
        }
        Some(out)
    }

    fn process_npcs(&mut self, now: Instant) {
        let Some(period) = self.o().npcs.as_ref().map(|_| Duration::from_micros(self.o().sampler.npc_period_us)) else {
            return;
        };
        if self.last_npc_poll.is_some_and(|t| now.duration_since(t) < period) {
            return;
        }
        self.last_npc_poll = Some(now);
        let Some(seen) = self.read_npcs() else { return };
        let old = std::mem::take(&mut self.npcs);
        for (uid, o) in &old {
            if !seen.contains_key(uid) {
                self.dirty = true;
                self.emit(Event::NpcDespawn { uid: *uid, npc_id: o.npc_id }, now);
            }
        }
        for (uid, n) in &seen {
            match old.get(uid) {
                None => {
                    self.dirty = true;
                    self.emit(Event::NpcSpawn { npc: n.clone() }, now);
                    self.boss_events(None, n, now);
                }
                Some(o) => {
                    if o != n {
                        self.dirty = true;
                        self.npc_diff(o, n, now);
                    }
                }
            }
        }
        self.npcs = seen;
    }

    fn npc_diff(&self, o: &NpcInfo, n: &NpcInfo, now: Instant) {
        let (uid, npc_id) = (n.uid, n.npc_id);
        if o.npc_id != n.npc_id {
            if let Some(id) = n.npc_id {
                self.emit(Event::NpcField { uid, npc_id, name: "id".into(), old: o.npc_id.map(|v| Num::I(v as i64)), new: Num::I(id as i64) }, now);
            }
        }
        if o.hp != n.hp {
            if let Some(h) = n.hp {
                self.emit(Event::NpcHealth { uid, npc_id, old: o.hp, new: h, max: n.hp_max, pct: n.hp_pct }, now);
            }
        }
        if o.anim != n.anim {
            if let Some(a) = n.anim {
                self.emit(Event::NpcAnimation { uid, npc_id, old: o.anim, new: a }, now);
            }
        }
        if o.spotanim != n.spotanim {
            if let Some(s) = n.spotanim {
                self.emit(Event::NpcSpotanim { uid, npc_id, old: o.spotanim, new: s }, now);
            }
        }
        let emit_extra = self.o().npcs.as_ref().map(|d| &d.fields);
        for (k, v) in &n.extra {
            if o.extra.get(k) != Some(v) && emit_extra.and_then(|f| f.get(k)).is_none_or(|f| f.emit) {
                self.emit(Event::NpcField { uid, npc_id, name: k.clone(), old: o.extra.get(k).copied(), new: *v }, now);
            }
        }
        self.boss_events(Some(o), n, now);
    }

    fn boss_events(&self, o: Option<&NpcInfo>, n: &NpcInfo, now: Instant) {
        let Some(boss) = self.boss_for(n.npc_id) else { return };
        let uid = n.uid;
        if o.map(|o| o.hp) != Some(n.hp) {
            if let Some(hp) = n.hp {
                self.emit(Event::BossHp { boss: boss.name.clone(), uid, hp, max: n.hp_max, pct: n.hp_pct }, now);
            }
        }
        if o.map(|o| o.anim) != Some(n.anim) {
            if let Some(a) = n.anim.filter(|a| !boss.is_idle(*a)) {
                let action = boss.anim_action(a).map(str::to_string).unwrap_or_else(|| format!("anim:{a}"));
                self.emit(Event::BossAction { boss: boss.name.clone(), uid, action, anim_id: Some(a), spotanim_id: None }, now);
            }
        }
        if o.map(|o| o.spotanim) != Some(n.spotanim) {
            if let Some(s) = n.spotanim {
                if let Some(action) = boss.spotanim_action(s) {
                    self.emit(Event::BossAction { boss: boss.name.clone(), uid, action: action.to_string(), anim_id: None, spotanim_id: Some(s) }, now);
                }
            }
        }
    }

    fn process_boss_phases(&mut self, now: Instant) {
        for boss in &self.cfg.bosses {
            let Some(sig) = &boss.phase_signal else { continue };
            let Some(&i) = self.index.get(sig) else { continue };
            let (Some(new), old) = (self.cur[i], self.prev_phase_value(i)) else { continue };
            if old == Some(new) {
                continue;
            }
            if let Some(n) = self.npcs.values().find(|n| n.npc_id.is_some_and(|id| boss.matches(id))) {
                self.emit(Event::BossPhase { boss: boss.name.clone(), uid: n.uid, phase: new.as_i64() }, now);
            }
        }
    }

    fn prev_phase_value(&self, i: usize) -> Option<Num> {
        self.prev[i]
    }

    fn publish_snapshot(&mut self) {
        if !self.dirty {
            return;
        }
        self.dirty = false;
        let mut signals = BTreeMap::new();
        for (i, (name, _)) in self.defs.iter().enumerate() {
            if let Some(v) = self.cur[i] {
                signals.insert(name.clone(), v);
            }
        }
        let geti = |n: &str| self.value(n).map(|v| v.as_i64());
        let adren = geti("adrenaline");
        let player = PlayerState {
            lifepoints: geti("lifepoints"),
            lifepoints_max: self.max_of("lifepoints"),
            prayer: geti("prayer"),
            prayer_max: self.max_of("prayer"),
            adrenaline: adren,
            adrenaline_pct: adren.and_then(|a| self.max_of("adrenaline").filter(|m| *m > 0).map(|m| pct1(a as f64, m as f64))),
            gcd_end_tick: self.gcd.active.as_ref().and_then(|a| a.end_tick),
            gcd_ends_at_unix_us: self.gcd.active.as_ref().map(|a| a.ends_at_unix),
        };
        let snap = Snapshot {
            src: self.em.src,
            state: self.state,
            build: self.build.clone(),
            label: self.label.clone(),
            tick: self.tick.current(Instant::now()),
            player,
            npcs: self.npcs.values().cloned().collect(),
            signals,
            unix_us: unix_us(),
        };
        self.shared.snapshots.write().unwrap().insert(self.em.src, snap);
    }

    fn cycle(&mut self, now: Instant) {
        if !self.anchors.failures.is_empty() && now.duration_since(self.last_anchor_try) > Duration::from_secs(10) {
            self.last_anchor_try = now;
            if let Ok(m) = self.proc.modules() {
                self.modules = m;
            }
            self.anchors = resolve_anchors(&self.cfg.offsets, &self.proc, &self.modules, &self.main, &mut self.code);
        }
        self.poll_locates();
        self.read_signals();
        self.read_keyed(now);
        self.update_state(now);
        if self.state == SourceState::Ok {
            // Phases compare cur vs prev, so they run before process_signals rolls prev.
            self.process_boss_phases(now);
            self.process_signals(now);
            self.process_npcs(now);
        }
        self.publish_snapshot();
    }
}

fn shared_origin() -> Instant {
    Instant::now()
}

pub(crate) fn run(shared: Arc<Shared>, cfg: Arc<ReaderConfig>, pid: u32, label: Option<String>) {
    let em = Emitter::new(shared.clone(), pid);
    let detached = |detail: String| {
        em.emit(
            Event::Status { state: SourceState::Detached, build: None, label: label.clone(), failing: vec![], detail: Some(detail) },
            (None, None),
        );
    };
    let proc = match Process::open(pid) {
        Ok(p) => p,
        Err(e) => {
            detached(e.to_string());
            return;
        }
    };
    let mut s = match Sampler::attach(proc, shared.clone(), cfg.clone(), label.clone()) {
        Ok(s) => s,
        Err(e) => {
            detached(format!("attach failed: {e}"));
            return;
        }
    };
    tracing::info!("[{pid}] attached (build {:?}, {} signals, {} anchors)", s.build, s.defs.len(), s.anchors.map.len());
    s.status(Some("attached".into()));
    Ticker::boost_current_thread();
    let mut ticker = Ticker::new(Duration::from_micros(cfg.offsets.sampler.period_us.max(100)));
    let mut last_alive_check = Instant::now();
    let mut exit_detail = "reader stopped".to_string();
    while !shared.stop.load(Ordering::Relaxed) {
        let now = Instant::now();
        if now.duration_since(last_alive_check) > Duration::from_millis(250) {
            last_alive_check = now;
            if !s.proc.is_alive() {
                exit_detail = "process exited".into();
                break;
            }
        }
        s.cycle(now);
        ticker.wait();
    }
    s.state = SourceState::Detached;
    s.status(Some(exit_detail));
    shared.snapshots.write().unwrap().remove(&pid);
    tracing::info!("[{pid}] detached");
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn groups_and_torn_guard() {
        let cfg = OffsetsFile::parse(
            r#"
[bases.v]
module_offset = 0
[vars.player]
base = "v"
[signals.lifepoints]
var = 10
[signals.prayer]
var = 12
[signals.far]
var = 100000
[signals.f]
base = "v"
offset = 4
type = "f32"
"#,
        )
        .unwrap();
        let defs: Vec<(String, SignalDef)> = cfg.signals.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        let groups = build_groups(&cfg, &defs);
        // var group splits into {10,12} and {100000}; base group separate
        assert_eq!(groups.len(), 3);
        let near = groups.iter().find(|g| g.slots.len() == 2).unwrap();
        assert_eq!(near.lo, 40);
        assert_eq!(near.a.len(), 12);

        let mut mem = FakeMem::new();
        mem.add(0x1000, vec![0; 0x100]);
        mem.write(0x1000u64 + 40, 9900i32);
        let mut g = groups.into_iter().find(|g| g.slots.len() == 2).unwrap();
        assert!(read_group(&mem, &mut g, 0x1000, 3));
        assert_eq!(ValType::I32.decode(&g.a[0..]), Num::I(9900));
    }
}
