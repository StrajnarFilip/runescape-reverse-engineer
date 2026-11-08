//! `rsscan`: guided, read-only memory discovery for the RS3 client.
//!
//! Every command is one non-interactive step; state lives in `.rsscan/`.
//! Typical flow (lifepoints):
//!   rsscan clients
//!   rsscan attach --pid 20392
//!   rsscan first i32 9900        # value shown on the HP orb
//!   (take damage in game)
//!   rsscan next 9640
//!   rsscan list
//!   rsscan save lifepoints 0x1f2e...  --type i32

use anyhow::{anyhow, bail, Context, Result};
use clap::{Parser, Subcommand};
use rsmem::{fmt_addr, pe, MemRead, ModuleInfo, Process};
use rsoffsets::ValType;
use rsscan::scan::{self, Cond, RegionFilter};
use rsscan::session::{Found, Session, State};
use rsscan::{dump, parse_addr, ptrscan, siggen, tickfind, watch};
use std::time::Duration;

#[derive(Parser)]
#[command(version, about = "Read-only memory discovery toolkit for the RS3 event reader")]
struct Cli {
    /// Session directory.
    #[arg(long, global = true, default_value = ".rsscan")]
    dir: std::path::PathBuf,
    /// Process id (overrides the session's attached pid).
    #[arg(long, global = true)]
    pid: Option<u32>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Clone, clap::Args)]
struct Filter {
    /// Also scan read-only memory.
    #[arg(long)]
    include_readonly: bool,
    /// Also scan mapped (shared/file) memory.
    #[arg(long)]
    include_mapped: bool,
    /// Only the main module's static data.
    #[arg(long)]
    module_only: bool,
}

#[derive(Subcommand)]
enum Cmd {
    /// List running game clients.
    Clients,
    /// Pick the process for this session (auto if exactly one client).
    Attach,
    /// List modules of the target.
    Modules,
    /// Summarize memory regions.
    Regions,
    /// First scan: `first i32 9900`, `first i32 eq 100,1000`, `first f32 between 0 1`, `first i32 unknown`.
    First {
        #[arg(value_name = "TYPE")]
        ty: ValType,
        /// Condition words; for negative numbers put them after `--`.
        #[arg(required = true, num_args = 1..)]
        cond: Vec<String>,
        /// Alignment in bytes (default: type size).
        #[arg(long)]
        align: Option<usize>,
        /// Float comparison tolerance.
        #[arg(long, default_value_t = 0.01)]
        tol: f64,
        #[command(flatten)]
        filter: Filter,
    },
    /// Narrow candidates: `next 9640`, `next eq 48,480`, `next changed|unchanged|inc|dec`, `next decby 10`.
    Next {
        #[arg(required = true, num_args = 1..)]
        cond: Vec<String>,
        #[arg(long, default_value_t = 0.01)]
        tol: f64,
    },
    /// Show current candidates with live values.
    List {
        #[arg(long, default_value_t = 40)]
        limit: usize,
    },
    /// Read one value: `read rs2client.exe+0x1234 --type u64`.
    Read {
        addr: String,
        #[arg(long, default_value = "i32")]
        r#type: ValType,
    },
    /// Hex/typed dump.
    Dump {
        addr: String,
        #[arg(long, default_value_t = 256)]
        len: usize,
    },
    /// Sample addresses at high rate; press mark keys (default F8) while playing.
    Watch {
        /// Addresses to watch (default: current candidates, if <= 5000).
        #[arg(long = "addr")]
        addrs: Vec<String>,
        /// Watch saved found-entries by name.
        #[arg(long = "found")]
        found: Vec<String>,
        /// Watch every cached var-table node value (from `live --nodes`), optionally by tag.
        #[arg(long)]
        nodes: bool,
        /// Use the loose node cache (`live --nodes --loose`).
        #[arg(long)]
        loose: bool,
        #[arg(long)]
        tag: Option<u64>,
        /// Only report addresses that changed at least this many times, and print their direction.
        #[arg(long, default_value_t = 0)]
        min_changes: usize,
        #[arg(long)]
        r#type: Option<ValType>,
        #[arg(long, default_value_t = 1000.0)]
        hz: f64,
        #[arg(long, default_value_t = 20.0)]
        secs: f64,
        /// Comma-separated mark keys, e.g. `F8` or `F8,1,2,3` (your ability keybinds).
        #[arg(long, default_value = "F8")]
        mark_keys: String,
        /// A change within this many ms after a mark counts as a hit.
        #[arg(long, default_value_t = 700.0)]
        window_ms: f64,
        /// Keep the top N ranked addresses as the new candidate set.
        #[arg(long)]
        keep_top: Option<usize>,
    },
    /// Track every 4-byte field of a struct over time.
    Structdiff {
        addr: String,
        #[arg(long, default_value_t = 512)]
        len: usize,
        #[arg(long, default_value_t = 200.0)]
        hz: f64,
        #[arg(long, default_value_t = 15.0)]
        secs: f64,
        #[arg(long, default_value = "F8")]
        mark_keys: String,
        #[arg(long, default_value_t = 700.0)]
        window_ms: f64,
    },
    /// Find the server-tick counter (u32, +1 every ~600 ms).
    Tickfind {
        #[arg(long, default_value_t = 3.0)]
        wait_secs: f64,
        #[arg(long, default_value_t = 6.0)]
        verify_secs: f64,
        #[command(flatten)]
        filter: Filter,
    },
    /// Reverse pointer scan to one or more dynamic addresses (results merged).
    Ptrscan {
        #[arg(required = true, num_args = 1..)]
        addrs: Vec<String>,
        /// Save paths under this name (ptrs_<name>.json).
        #[arg(long, default_value = "last")]
        name: String,
        #[arg(long, default_value_t = 4)]
        depth: usize,
        #[arg(long, default_value = "0x1000")]
        max_offset: String,
        #[arg(long, default_value_t = 20000)]
        frontier: usize,
        #[arg(long, default_value_t = 200)]
        results: usize,
        /// Only accept static bases in the main module.
        #[arg(long)]
        main_only: bool,
    },
    /// Live web page of values for eyeball matching (http://127.0.0.1:7879).
    /// `--nodes` shows every var hash-table node (key -> value).
    Live {
        /// Include every var-table node found in memory (cached in .rsscan/nodes.json).
        #[arg(long)]
        nodes: bool,
        /// Re-scan memory for nodes instead of using the cache.
        #[arg(long)]
        rescan: bool,
        /// Looser node signature (tag 7 + key + two pointer links); cached separately.
        #[arg(long)]
        loose: bool,
        /// Include the current scan candidates.
        #[arg(long)]
        cands: bool,
        /// Extra addresses (i32).
        #[arg(long = "addr")]
        addrs: Vec<String>,
        /// Name known keys, e.g. `--label 13537=lifepoints`.
        #[arg(long = "label")]
        labels: Vec<String>,
        #[arg(long, default_value = "127.0.0.1:7879")]
        bind: String,
        #[arg(long, default_value_t = 20.0)]
        hz: f64,
        #[arg(long, default_value_t = 200000)]
        max_key: i32,
        /// Only nodes with this tag (lifepoints node has 7).
        #[arg(long)]
        tag: Option<u64>,
        /// Only nodes within --span bytes of this address (e.g. a known var node).
        #[arg(long)]
        near: Option<String>,
        #[arg(long, default_value = "0x100000")]
        span: String,
        /// Only nodes whose +0x08 qword equals this (lifepoints node: 1).
        #[arg(long)]
        field8: Option<u64>,
    },
    /// Match candidates whose bits encode the displayed value: `was` at the last scan, `now` currently.
    /// Tries bit fields, x10/x100, max-v complements and f32 forms. Does not modify the candidate set.
    Fieldmatch {
        #[arg(long)]
        was: i64,
        #[arg(long)]
        now: i64,
        /// Display maximum (enables complement / fraction forms).
        #[arg(long)]
        max: Option<i64>,
    },
    /// Walk a table of fixed-size entries and print (key, value) pairs.
    /// Defaults match the sorted varp table: stride 0x30, key i32 at +0, value i32 at +8.
    Table {
        /// Any entry address in the table.
        addr: String,
        #[arg(long, default_value = "0x30")]
        stride: String,
        #[arg(long, default_value = "0")]
        key_off: String,
        #[arg(long, default_value = "8")]
        val_off: String,
        /// Entries to walk backwards and forwards.
        #[arg(long, default_value_t = 20)]
        before: usize,
        #[arg(long, default_value_t = 20)]
        after: usize,
        /// Only print entries with these keys (comma-separated); walks up to 100k entries each way.
        #[arg(long)]
        keys: Option<String>,
    },
    /// Who points at (or just before) an address: qwords in [addr - max_offset, addr].
    Refs {
        addr: String,
        #[arg(long, default_value = "0x80")]
        max_offset: String,
        #[arg(long, default_value_t = 40)]
        limit: usize,
    },
    /// Re-check saved pointer paths (after a client restart); keeps the survivors.
    Ptrverify {
        #[arg(long, default_value = "last")]
        name: String,
        /// Expected final address.
        #[arg(long)]
        expect_addr: Option<String>,
        /// Expected value at the final address, e.g. `i32:9900`.
        #[arg(long)]
        expect_value: Option<String>,
        /// Allowed difference for --expect-value.
        #[arg(long, default_value_t = 0.0)]
        tol: f64,
        /// The final address must be one of the current scan candidates.
        #[arg(long)]
        expect_cands: bool,
    },
    /// Generate code signatures for a static address (e.g. the module+off from ptrscan).
    Siggen {
        addr: String,
        #[arg(long, default_value = "NAME")]
        name: String,
        #[arg(long, default_value_t = 5)]
        max: usize,
    },
    /// Var array base from (address, var id) pairs: `varp --pair 0x1234:1000 --pair 0x1240:1003`.
    Varp {
        #[arg(long = "pair", required = true)]
        pairs: Vec<String>,
        #[arg(long, default_value_t = 4)]
        stride: u64,
    },
    /// Save an address under a name.
    Save {
        name: String,
        addr: String,
        #[arg(long, default_value = "i32")]
        r#type: ValType,
        #[arg(long, default_value = "")]
        note: String,
    },
    /// Show saved addresses with live values.
    Found,
    /// Resolve an offsets.toml against the live client and print every signal.
    Resolve {
        #[arg(long, default_value = "offsets.toml")]
        offsets: std::path::PathBuf,
    },
}

struct Ctx {
    proc: Process,
    modules: Vec<ModuleInfo>,
    main: ModuleInfo,
}

fn pick_pid(cli_pid: Option<u32>, st: &State) -> Result<u32> {
    if let Some(p) = cli_pid.or(st.pid) {
        return Ok(p);
    }
    let clients = rsmem::find_clients("rs2client.exe", "RuneScape");
    match clients.as_slice() {
        [one] => Ok(one.pid),
        [] => bail!("no RuneScape client window found; pass --pid"),
        many => bail!(
            "{} clients running; pick one with `rsscan --pid <pid> attach`:\n{}",
            many.len(),
            many.iter().map(|c| format!("  {} {} {:?}", c.pid, c.title, c.launch_world)).collect::<Vec<_>>().join("\n")
        ),
    }
}

fn ctx(pid: u32) -> Result<Ctx> {
    let proc = Process::open(pid)?;
    let modules = proc.modules()?;
    let main = proc.main_module()?;
    Ok(Ctx { proc, modules, main })
}

fn parse_keys(s: &str) -> Result<Vec<(i32, String)>> {
    s.split(',')
        .filter(|k| !k.trim().is_empty())
        .map(|k| rsmem::vk_from_name(k).map(|vk| (vk, k.trim().to_string())).ok_or_else(|| anyhow!("unknown key {k:?}")))
        .collect()
}

fn mib(bytes: u64) -> String {
    format!("{:.1}", bytes as f64 / (1u64 << 20) as f64)
}

fn fmt_val(ty: ValType, raw: u64) -> String {
    match ty.from_raw(raw) {
        rsmodel::Num::I(v) => v.to_string(),
        rsmodel::Num::F(v) => format!("{v:.4}"),
    }
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let sess = Session::open(&cli.dir)?;
    let mut st = sess.state();

    if let Cmd::Clients = cli.cmd {
        let c = rsmem::find_clients("rs2client.exe", "RuneScape");
        if c.is_empty() {
            println!("no RuneScape client windows found");
        }
        for c in c {
            println!("pid {:>6}  {:<32} launch world: {}", c.pid, c.title, c.launch_world.as_deref().unwrap_or("?"));
        }
        return Ok(());
    }
    if let Cmd::Varp { pairs, stride } = &cli.cmd {
        let mut bases = Vec::new();
        for p in pairs {
            let (a, id) = p.split_once(':').ok_or_else(|| anyhow!("pair must be ADDR:ID, got {p:?}"))?;
            let a = rsscan::addr::parse_num(a)? as u64;
            let id = rsscan::addr::parse_num(id)? as u64;
            let base = a.wrapping_sub(id * stride);
            println!("{p}: array base {base:#x}");
            bases.push(base);
        }
        bases.dedup();
        if bases.len() == 1 {
            println!("consistent: var array base = {:#x}. Next: `rsscan ptrscan {:#x}`", bases[0], bases[0]);
        } else {
            println!("inconsistent bases: the addresses are not in the same var array (or the ids are wrong)");
        }
        return Ok(());
    }

    let pid = pick_pid(cli.pid, &st)?;
    let c = ctx(pid).with_context(|| format!("attaching to {pid}"))?;
    if st.pid != Some(pid) {
        if st.pid.is_some() {
            println!("note: switching session from pid {:?} to {pid}; candidates from the old process are discarded", st.pid);
            st = State::default();
        }
        st.pid = Some(pid);
        sess.save_state(&st)?;
    }

    match cli.cmd {
        Cmd::Clients | Cmd::Varp { .. } => unreachable!(),
        Cmd::Attach => {
            let build = pe::read_headers(&c.proc, c.main.base).map(|h| h.build_id()).unwrap_or_else(|e| e);
            println!("attached to pid {pid}: {} base {:#x} size {:#x} build {build}", c.main.name, c.main.base, c.main.size);
        }
        Cmd::Modules => {
            for m in &c.modules {
                println!("{:#018x} {:>10x}  {}", m.base, m.size, m.name);
            }
        }
        Cmd::Regions => {
            let regions = c.proc.regions();
            let mut by: std::collections::BTreeMap<String, (usize, u64)> = Default::default();
            for r in &regions {
                let key = format!("{:?} {}{}{}", r.kind, if r.readable { "r" } else { "-" }, if r.writable { "w" } else { "-" }, if r.executable { "x" } else { "-" });
                let e = by.entry(key).or_default();
                e.0 += 1;
                e.1 += r.size;
            }
            for (k, (n, sz)) in by {
                println!("{k:<16} {n:>6} regions {:>8} MiB", sz >> 20);
            }
        }
        Cmd::First { ty, cond, align, tol, filter } => {
            let cond = Cond::parse(&cond)?;
            let align = align.unwrap_or(ty.size());
            let regions = scan::select_regions(
                &c.proc.regions(),
                &RegionFilter { include_readonly: filter.include_readonly, include_mapped: filter.include_mapped, module_only: filter.module_only },
                &c.main,
            );
            println!("scanning {} MiB in {} regions...", mib(scan::total_bytes(&regions)), regions.len());
            let t = std::time::Instant::now();
            if cond == Cond::Unknown {
                let bytes = scan::snapshot_to_file(&c.proc, &regions, &sess.snapshot_path())?;
                st.has_snapshot = true;
                st.count = 0;
                println!("snapshot of {} MiB saved in {:.1?}. Now change the value in game and run `rsscan next changed|inc|dec`.", mib(bytes), t.elapsed());
            } else {
                let cands = scan::first_scan(&c.proc, &regions, ty, align, &cond, tol)?;
                sess.save_cands(&cands)?;
                st.has_snapshot = false;
                st.count = cands.len();
                println!("{} candidates in {:.1?}", cands.len(), t.elapsed());
            }
            st.ty = Some(ty);
            st.align = Some(align);
            st.history = vec![format!("first {ty} {cond:?}")];
            sess.save_state(&st)?;
        }
        Cmd::Next { cond, tol } => {
            let cond = Cond::parse(&cond)?;
            let ty = st.ty.ok_or_else(|| anyhow!("no scan in progress; run `rsscan first` first"))?;
            let t = std::time::Instant::now();
            let cands = if st.has_snapshot {
                let c2 = scan::next_from_snapshot(&c.proc, ty, st.align.unwrap_or(ty.size()), &sess.snapshot_path(), &cond, tol)?;
                let _ = std::fs::remove_file(sess.snapshot_path());
                st.has_snapshot = false;
                c2
            } else {
                let old = sess.load_cands()?;
                scan::next_scan(&c.proc, ty, &old, &cond, tol)
            };
            sess.save_cands(&cands)?;
            st.count = cands.len();
            st.history.push(format!("next {cond:?} -> {}", cands.len()));
            sess.save_state(&st)?;
            println!("{} candidates in {:.1?}", cands.len(), t.elapsed());
            if cands.len() <= 20 {
                for (a, v) in &cands {
                    println!("  {:<40} {}", fmt_addr(*a, &c.modules), fmt_val(ty, *v));
                }
            }
        }
        Cmd::List { limit } => {
            let ty = st.ty.ok_or_else(|| anyhow!("no scan in progress"))?;
            let cands = sess.load_cands().unwrap_or_default();
            println!("{} candidates ({})", cands.len(), st.history.join(" | "));
            for (a, old) in cands.iter().take(limit) {
                let now = c.proc.read_vec(*a, ty.size()).map(|b| fmt_val(ty, ty.raw(&b))).unwrap_or_else(|_| "unreadable".into());
                println!("  {:#018x}  {:<36} last {:<12} now {}", a, fmt_addr(*a, &c.modules), fmt_val(ty, *old), now);
            }
        }
        Cmd::Read { addr, r#type } => {
            let a = parse_addr(&addr, &c.proc, &c.modules)?;
            let b = c.proc.read_vec(a, r#type.size())?;
            println!("{} ({:#x}) = {}", fmt_addr(a, &c.modules), a, fmt_val(r#type, r#type.raw(&b)));
        }
        Cmd::Dump { addr, len } => {
            let a = parse_addr(&addr, &c.proc, &c.modules)?;
            let b = c.proc.read_vec(a, len)?;
            let mut regions = c.proc.regions();
            regions.sort_by_key(|r| r.base);
            for l in dump::dump(&b, a, &regions) {
                println!("{l}");
            }
        }
        Cmd::Watch { addrs, found, nodes, loose, tag, min_changes, r#type, hz, secs, mark_keys, window_ms, keep_top } => {
            let saved = sess.found();
            let mut list: Vec<u64> = Vec::new();
            let mut ty = r#type;
            let mut node_keys: std::collections::HashMap<u64, i32> = Default::default();
            if nodes {
                let cached: Vec<(u64, u64, i32)> = serde_json::from_str(
                    &std::fs::read_to_string(cli.dir.join(if loose { "nodes_loose.json" } else { "nodes.json" }))
                        .context("no nodes cache; run `rsscan live --nodes [--loose]` once")?,
                )?;
                for (node, _tag, key) in cached.into_iter().filter(|n| tag.is_none_or(|t| n.1 == t)) {
                    let a = node + rsscan::nodes::VALUE_OFF;
                    node_keys.insert(a, key);
                    list.push(a);
                }
                ty = ty.or(Some(ValType::I32));
            }
            for a in &addrs {
                list.push(parse_addr(a, &c.proc, &c.modules)?);
            }
            for f in &found {
                let e = saved.get(f).ok_or_else(|| anyhow!("no saved entry {f:?}"))?;
                list.push(rsscan::addr::parse_num(&e.addr)? as u64);
                ty = ty.or(Some(e.ty));
            }
            if list.is_empty() {
                let cands = sess.load_cands().unwrap_or_default();
                if cands.is_empty() || cands.len() > 5000 {
                    bail!("{} candidates: narrow to <= 5000 first, or pass --addr/--found", cands.len());
                }
                list = cands.iter().map(|c| c.0).collect();
            }
            let ty = ty.or(st.ty).unwrap_or(ValType::I32);
            let keys = parse_keys(&mark_keys)?;
            println!(
                "watching {} addresses as {ty} at {hz} Hz for {secs}s. Mark keys: {} (or press Enter here). Go!",
                list.len(),
                keys.iter().map(|k| k.1.clone()).collect::<Vec<_>>().join(",")
            );
            let opts = watch::WatchOpts {
                hz,
                duration: Duration::from_secs_f64(secs),
                mark_keys: keys,
                stdin_marks: true,
                live_limit: 50,
                log: Some(sess.watch_log_path()),
            };
            let res = watch::watch(&c.proc, ty, &list, &opts, &rsmem::key_down, |l| println!("{l}"));
            println!(
                "{} passes in {:.1?} ({:.0} Hz), {} changes, {} marks. Log: {}",
                res.passes,
                res.elapsed,
                res.passes as f64 / res.elapsed.as_secs_f64(),
                res.changes.len(),
                res.marks.len(),
                sess.watch_log_path().display()
            );
            if min_changes > 0 {
                let mut per: Vec<Vec<(f64, f64, f64)>> = vec![Vec::new(); list.len()];
                for ch in &res.changes {
                    per[ch.idx].push((ch.t_ms, ch.old, ch.new));
                }
                let mut rows: Vec<(usize, usize, usize, usize)> = per
                    .iter()
                    .enumerate()
                    .filter(|(_, v)| v.len() >= min_changes)
                    .map(|(i, v)| (i, v.len(), v.iter().filter(|c| c.2 < c.1).count(), v.iter().filter(|c| c.2 > c.1).count()))
                    .collect();
                rows.sort_by_key(|r| std::cmp::Reverse(r.2.max(r.3) * 1000 / r.1.max(1) + r.1));
                println!("addresses with >= {min_changes} changes: {}", rows.len());
                println!("key      address          changes  down  up   first -> last");
                for (i, n, down, up) in rows.iter().take(40) {
                    let v = &per[*i];
                    println!(
                        "{:<8} {:<16x} {:>7} {:>5} {:>3}   {} -> {}",
                        node_keys.get(&list[*i]).map(|k| k.to_string()).unwrap_or_default(),
                        list[*i],
                        n,
                        down,
                        up,
                        v.first().map(|c| c.1).unwrap_or_default(),
                        v.last().map(|c| c.2).unwrap_or_default()
                    );
                }
            }
            let ranked = watch::rank(&res, list.len(), window_ms);
            println!("rank  address                                  changes  hits/marks  extra  score  last");
            for (i, r) in ranked.iter().take(25).enumerate() {
                println!(
                    "{:>4}  {:<40} {:>7}  {:>4}/{:<5}  {:>5}  {:>5.2}  {}",
                    i + 1,
                    fmt_addr(list[r.idx], &c.modules),
                    r.changes,
                    r.hits,
                    res.marks.len(),
                    r.extra,
                    r.score,
                    res.last[r.idx].map(|v| v.to_string()).unwrap_or_default()
                );
            }
            if let Some(n) = keep_top {
                let keep: Vec<(u64, u64)> = ranked
                    .iter()
                    .take(n)
                    .map(|r| (list[r.idx], res.last[r.idx].map(|v| ty.to_raw(v)).unwrap_or(0)))
                    .collect::<std::collections::BTreeMap<_, _>>()
                    .into_iter()
                    .collect();
                sess.save_cands(&keep)?;
                st.ty = Some(ty);
                st.count = keep.len();
                st.history.push(format!("watch keep_top {n}"));
                sess.save_state(&st)?;
                println!("kept top {} as candidates", keep.len());
            }
        }
        Cmd::Structdiff { addr, len, hz, secs, mark_keys, window_ms } => {
            let a = parse_addr(&addr, &c.proc, &c.modules)?;
            let keys = parse_keys(&mark_keys)?;
            let mut was = vec![false; keys.len()];
            println!("sampling {len} bytes at {} for {secs}s at {hz} Hz; mark keys {mark_keys}", fmt_addr(a, &c.modules));
            let (stats, marks) = dump::structdiff(&c.proc, a, len, hz, Duration::from_secs_f64(secs), || {
                let mut hit = None;
                for (k, (vk, label)) in keys.iter().enumerate() {
                    let d = rsmem::key_down(*vk);
                    if d && !was[k] {
                        hit = Some(label.clone());
                    }
                    was[k] = d;
                }
                hit
            });
            println!("{} marks", marks.len());
            println!("offset   changes  hits  first        last         min          max          distinct");
            for s in stats.iter().filter(|s| s.changes > 0) {
                let hits = marks.iter().filter(|(m, _)| s.change_times.iter().any(|t| *t >= *m && *t <= m + window_ms)).count();
                let d: Vec<String> = s.distinct.iter().take(8).map(|v| v.to_string()).collect();
                println!(
                    "{:#06x} {:>8} {:>5}  {:<12} {:<12} {:<12} {:<12} {}{}",
                    s.offset,
                    s.changes,
                    hits,
                    s.first,
                    s.last,
                    s.min,
                    s.max,
                    d.join(","),
                    if s.distinct.len() > 8 { ",..." } else { "" }
                );
            }
        }
        Cmd::Tickfind { wait_secs, verify_secs, filter } => {
            let regions = scan::select_regions(
                &c.proc.regions(),
                &RegionFilter { include_readonly: filter.include_readonly, include_mapped: filter.include_mapped, module_only: filter.module_only },
                &c.main,
            );
            let opts = tickfind::TickFindOpts { wait: Duration::from_secs_f64(wait_secs), verify: Duration::from_secs_f64(verify_secs), ..Default::default() };
            println!("coarse pass over {} MiB (stay logged in; ticks must be running)...", mib(scan::total_bytes(&regions)));
            let coarse = tickfind::coarse(&c.proc, &regions, &opts, &mut |m| println!("  {m}"));
            println!("{} coarse candidates; verifying for {verify_secs}s...", coarse.len());
            if coarse.len() > 2_000_000 {
                bail!("too many coarse candidates; try --module-only first");
            }
            if coarse.len() > 2000 {
                println!("(large set: a {verify_secs}s rate check runs first, then a {verify_secs}s precise pass on survivors)");
            }
            let found = tickfind::verify(&c.proc, &coarse, &opts);
            for t in found.iter().take(20) {
                println!(
                    "  {:<40} value {:<10} steps {:<3} mean {:.1} ms  std {:.1} ms",
                    fmt_addr(t.addr, &c.modules),
                    t.value,
                    t.increments,
                    t.mean_ms,
                    t.std_ms
                );
            }
            let cands: Vec<(u64, u64)> = found.iter().map(|t| (t.addr, t.value as u64)).collect();
            sess.save_cands(&cands)?;
            st.ty = Some(ValType::U32);
            st.align = Some(4);
            st.count = cands.len();
            st.history = vec!["tickfind".into()];
            sess.save_state(&st)?;
            println!("{} tick-counter candidates saved as the current candidate set", cands.len());
        }
        Cmd::Ptrscan { addrs, name, depth, max_offset, frontier, results, main_only } => {
            let regions = scan::select_regions(&c.proc.regions(), &RegionFilter::default(), &c.main);
            let statics: Vec<ModuleInfo> = if main_only { vec![c.main.clone()] } else { c.modules.clone() };
            let opts = ptrscan::PtrScanOpts {
                max_depth: depth,
                max_offset: rsscan::addr::parse_num(&max_offset)? as u64,
                max_frontier: frontier,
                max_results: results,
            };
            let mut paths: Vec<ptrscan::PtrPath> = Vec::new();
            for addr in &addrs {
                let target = parse_addr(addr, &c.proc, &c.modules)?;
                println!("pointer scan to {target:#x} over {} MiB, depth {depth}...", mib(scan::total_bytes(&regions)));
                for p in ptrscan::ptrscan(&c.proc, &regions, &statics, target, &opts, &mut |m| println!("  {m}")) {
                    if !paths.contains(&p) {
                        paths.push(p);
                    }
                }
            }
            paths.sort_by_key(|p| (p.chain.len(), p.chain.iter().map(|o| o.unsigned_abs()).sum::<u64>()));
            std::fs::write(sess.ptrs_path(&name), serde_json::to_string_pretty(&paths)?)?;
            println!("{} paths (saved to {}):", paths.len(), sess.ptrs_path(&name).display());
            for p in paths.iter().take(20) {
                println!("  {}", p.display());
            }
            println!("After a client restart: re-find the value, then `rsscan ptrverify --name {name} --expect-cands` (or --expect-addr / --expect-value).");
        }
        Cmd::Live { nodes, rescan, loose, cands, addrs, labels, bind, hz, max_key, tag, near, span, field8 } => {
            let mut names: std::collections::HashMap<i32, String> = Default::default();
            for l in &labels {
                let (k, v) = l.split_once('=').ok_or_else(|| anyhow!("--label KEY=NAME, got {l:?}"))?;
                names.insert(k.trim().parse()?, v.trim().to_string());
            }
            let mut rows: Vec<rsscan::live::Row> = Vec::new();
            if nodes {
                let cache = cli.dir.join(if loose { "nodes_loose.json" } else { "nodes.json" });
                let found: Vec<(u64, u64, i32)> = match (rescan, std::fs::read_to_string(&cache)) {
                    (false, Ok(s)) => serde_json::from_str(&s)?,
                    _ => {
                        let regions = scan::select_regions(&c.proc.regions(), &RegionFilter::default(), &c.main);
                        println!("scanning {} MiB for var-table nodes...", mib(scan::total_bytes(&regions)));
                        let t = std::time::Instant::now();
                        let raw = if loose {
                            rsscan::nodes::find_var_nodes_loose(&c.proc, &regions, max_key, tag.unwrap_or(7))
                        } else {
                            rsscan::nodes::find_var_nodes(&c.proc, &regions, max_key, 64)
                        };
                        let n: Vec<(u64, u64, i32)> = raw.into_iter().map(|n| (n.node, n.tag, n.key)).collect();
                        println!("found {} nodes in {:.1?}", n.len(), t.elapsed());
                        std::fs::write(&cache, serde_json::to_string(&n)?)?;
                        n
                    }
                };
                let total = found.len();
                let near = near.map(|a| parse_addr(&a, &c.proc, &c.modules)).transpose()?;
                let span = rsscan::addr::parse_num(&span)? as u64;
                let found: Vec<(u64, u64, i32)> = found
                    .into_iter()
                    .filter(|n| tag.is_none_or(|t| n.1 == t))
                    .filter(|n| near.is_none_or(|a| n.0.abs_diff(a) <= span))
                    .filter(|n| field8.is_none_or(|f| c.proc.read::<u64>(n.0 + 8).is_ok_and(|v| v == f)))
                    .collect();
                let mut by_tag: std::collections::BTreeMap<u64, usize> = Default::default();
                for n in &found {
                    *by_tag.entry(n.1).or_default() += 1;
                }
                println!("{} of {total} nodes pass the filters; by tag: {by_tag:?}", found.len());
                if found.len() > 20_000 {
                    bail!("too many rows for a live view; add --tag 7, --near <addr>, --field8 1");
                }
                for (node, tag, key) in found {
                    let label = names.get(&key).cloned().unwrap_or_else(|| if tag == 7 { String::new() } else { format!("tag {tag}") });
                    rows.push(rsscan::live::Row { label, key: Some(key), addr: node + rsscan::nodes::VALUE_OFF });
                }
            }
            if cands {
                for (a, _) in sess.load_cands().unwrap_or_default().into_iter().take(5000) {
                    rows.push(rsscan::live::Row { label: "cand".into(), key: None, addr: a });
                }
            }
            for a in &addrs {
                rows.push(rsscan::live::Row { label: a.clone(), key: None, addr: parse_addr(a, &c.proc, &c.modules)? });
            }
            if rows.is_empty() {
                bail!("nothing to show: pass --nodes, --cands or --addr");
            }
            let listener = std::net::TcpListener::bind(&bind)?;
            let live = rsscan::live::Live::new(rows, format!("rsscan live · pid {pid}"));
            let n = live.clone();
            let proc = std::sync::Arc::new(c.proc);
            let p2 = proc.clone();
            std::thread::spawn(move || n.sample_loop(&*p2, hz));
            println!("live view on http://{bind}/ ({} rows, {hz} Hz). Ctrl+C to stop.", live.len());
            live.serve(listener);
        }
        Cmd::Fieldmatch { was, now, max } => {
            let cands = sess.load_cands()?;
            println!("testing {} candidates: was {was} -> now {now}...", cands.len());
            let t = std::time::Instant::now();
            let hits = rsscan::fieldmatch::fieldmatch(&c.proc, &cands, was, now, max);
            let mut by_addr: std::collections::BTreeMap<u64, Vec<String>> = Default::default();
            for h in &hits {
                by_addr.entry(h.addr).or_default().push(format!("{} ({:#010x} -> {:#010x})", h.encoding, h.before, h.after));
            }
            println!("{} addresses match in {:.1?}:", by_addr.len(), t.elapsed());
            for (a, encs) in by_addr.iter().take(40) {
                println!("  {:<40} {}", fmt_addr(*a, &c.modules), encs.iter().take(3).cloned().collect::<Vec<_>>().join("; "));
            }
        }
        Cmd::Table { addr, stride, key_off, val_off, before, after, keys } => {
            let start = parse_addr(&addr, &c.proc, &c.modules)?;
            let stride = rsscan::addr::parse_num(&stride)? as u64;
            let (ko, vo) = (rsscan::addr::parse_num(&key_off)? as u64, rsscan::addr::parse_num(&val_off)? as u64);
            let want: Option<Vec<i32>> = keys.map(|k| k.split(',').map(|x| x.trim().parse::<i32>()).collect()).transpose()?;
            let (b, a) = if want.is_some() { (100_000, 100_000) } else { (before, after) };
            let mut rows: Vec<(u64, i32, u32)> = Vec::new();
            for dir in [-1i64, 1] {
                let n = if dir < 0 { b } else { a };
                let mut prev_key: Option<i32> = None;
                for i in 0..=n as i64 {
                    if dir > 0 && i == 0 {
                        continue;
                    }
                    let e = (start as i64 + dir * i * stride as i64) as u64;
                    let (Ok(k), Ok(v)) = (c.proc.read::<i32>(e + ko), c.proc.read::<u32>(e + vo)) else { break };
                    // Stop when keys stop being monotonic (left the table).
                    if let Some(p) = prev_key {
                        if (dir > 0 && k <= p) || (dir < 0 && k >= p) {
                            break;
                        }
                    }
                    prev_key = Some(k);
                    if want.as_ref().is_none_or(|w| w.contains(&k)) {
                        rows.push((e, k, v));
                    }
                }
            }
            rows.sort_by_key(|r| r.1);
            rows.dedup();
            println!("{} entries (key ascending):", rows.len());
            for (e, k, v) in rows {
                println!("  {:<18} key {:>7}  value {:>11} ({:#010x})  lo16 {:>5}  hi16 {:>5}", format!("{e:#x}"), k, v as i32, v, v & 0xFFFF, v >> 16);
            }
        }
        Cmd::Refs { addr, max_offset, limit } => {
            let target = parse_addr(&addr, &c.proc, &c.modules)?;
            let max_off = rsscan::addr::parse_num(&max_offset)? as u64;
            let regions = scan::select_regions(&c.proc.regions(), &RegionFilter::default(), &c.main);
            let hits = ptrscan::refs(&c.proc, &regions, target, max_off, limit);
            println!("{} references to [{:#x} - {max_off:#x}, {target:#x}]:", hits.len(), target);
            for (a, v) in hits {
                println!("  {:<40} holds {v:#x}  (target - {:#x})", fmt_addr(a, &c.modules), target - v);
            }
        }
        Cmd::Ptrverify { name, expect_addr, expect_value, tol, expect_cands } => {
            let paths: Vec<ptrscan::PtrPath> =
                serde_json::from_str(&std::fs::read_to_string(sess.ptrs_path(&name)).with_context(|| format!("no saved paths named {name:?}; run ptrscan --name {name}"))?)?;
            let cand_set: Option<std::collections::HashSet<u64>> =
                if expect_cands { Some(sess.load_cands()?.into_iter().map(|c| c.0).collect()) } else { None };
            let want_addr = expect_addr.map(|a| parse_addr(&a, &c.proc, &c.modules)).transpose()?;
            let want_val = match expect_value {
                Some(s) => {
                    let (t, v) = s.split_once(':').ok_or_else(|| anyhow!("--expect-value TYPE:VALUE"))?;
                    let t: ValType = t.parse().map_err(|e: String| anyhow!(e))?;
                    Some((t, v.parse::<f64>()?))
                }
                None => None,
            };
            if want_addr.is_none() && want_val.is_none() && cand_set.is_none() {
                bail!("pass --expect-addr, --expect-value or --expect-cands");
            }
            let total = paths.len();
            let keep: Vec<ptrscan::PtrPath> = paths
                .into_iter()
                .filter(|p| {
                    let Some(a) = p.resolve(&c.proc, &c.modules) else { return false };
                    if want_addr.is_some_and(|w| w != a) {
                        return false;
                    }
                    if cand_set.as_ref().is_some_and(|s| !s.contains(&a)) {
                        return false;
                    }
                    if let Some((t, v)) = want_val {
                        let Ok(b) = c.proc.read_vec(a, t.size()) else { return false };
                        if (t.from_raw(t.raw(&b)).as_f64() - v).abs() > tol + 1e-9 {
                            return false;
                        }
                    }
                    true
                })
                .collect();
            std::fs::write(sess.ptrs_path(&name), serde_json::to_string_pretty(&keep)?)?;
            println!("{} of {total} paths survived:", keep.len());
            for p in keep.iter().take(30) {
                println!("  {}", p.display());
            }
            if let Some(p) = keep.first() {
                println!("\noffsets.toml snippet:\n{}", p.toml_snippet("NAME"));
            }
        }
        Cmd::Siggen { addr, name, max } => {
            let target = parse_addr(&addr, &c.proc, &c.modules)?;
            let module = c.modules.iter().find(|m| m.contains(target)).cloned().unwrap_or_else(|| c.main.clone());
            let hdr = pe::read_headers(&c.proc, module.base).map_err(|e| anyhow!(e))?;
            let code = pe::read_code(&c.proc, module.base, &hdr);
            println!("disassembling {} MiB of code in {}...", mib(code.iter().map(|c| c.1.len() as u64).sum()), module.name);
            let sigs = siggen::siggen(&code, target, max);
            if sigs.is_empty() {
                println!("no unique RIP-relative reference to {target:#x} found; keep using module_offset");
            }
            for s in &sigs {
                println!("{} @ {} ({})", s.pattern, fmt_addr(s.instr_addr, &c.modules), s.mnemonic);
            }
            if let Some(s) = sigs.first() {
                println!("\noffsets.toml snippet:\n{}", s.toml_snippet(&name, &module.name, target - module.base));
            }
        }
        Cmd::Save { name, addr, r#type, note } => {
            let a = parse_addr(&addr, &c.proc, &c.modules)?;
            let mut f = sess.found();
            f.insert(name.clone(), Found { addr: format!("{a:#x}"), ty: r#type, note });
            sess.save_found(&f)?;
            println!("saved {name} = {a:#x} ({})", fmt_addr(a, &c.modules));
        }
        Cmd::Found => {
            for (name, e) in sess.found() {
                let a = rsscan::addr::parse_num(&e.addr)? as u64;
                let now = c.proc.read_vec(a, e.ty.size()).map(|b| fmt_val(e.ty, e.ty.raw(&b))).unwrap_or_else(|_| "unreadable".into());
                println!("{name:<20} {:<40} {:<4} now {:<12} {}", fmt_addr(a, &c.modules), e.ty.to_string(), now, e.note);
            }
        }
        Cmd::Resolve { offsets } => {
            let o = rsoffsets::OffsetsFile::load(&offsets)?;
            let build = pe::read_headers(&c.proc, c.main.base).map(|h| h.build_id()).ok();
            println!("client build {:?}, offsets made for {:?}", build, o.client.build);
            let mut cache = rsoffsets::CodeCache::default();
            let anchors = rsoffsets::resolve_anchors(&o, &c.proc, &c.modules, &c.main, &mut cache);
            for (k, v) in &anchors.map {
                println!("base {k:<16} anchor {}", fmt_addr(*v, &c.modules));
            }
            for n in &anchors.notes {
                println!("note: {n}");
            }
            for f in &anchors.failures {
                println!("FAIL: {f}");
            }
            let base_addr = |b: &str| -> Option<u64> { c.proc.follow_chain(*anchors.map.get(b)?, &o.bases[b].chain).ok() };
            for (name, s) in &o.signals {
                let addr = match (&s.base, s.var) {
                    (Some(b), _) => base_addr(b).and_then(|a| c.proc.follow_chain(a, &s.chain).ok()).map(|a| a.wrapping_add(s.offset as u64)),
                    (None, Some(id)) => o.vars.get(&s.var_array).and_then(|v| {
                        base_addr(&v.base)
                            .and_then(|a| c.proc.follow_chain(a, &v.chain).ok())
                            .map(|a| a.wrapping_add((id * v.stride as i64 + s.offset) as u64))
                    }),
                    _ => None,
                };
                let val = addr.and_then(|a| c.proc.read_vec(a, s.ty.size()).ok()).map(|b| s.ty.decode_bits(&b, s.bits));
                println!(
                    "signal {name:<20} {:<40} = {}",
                    addr.map(|a| fmt_addr(a, &c.modules)).unwrap_or_else(|| "unresolved".into()),
                    val.map(|v| format!("{v:?}")).unwrap_or_else(|| "unreadable".into())
                );
            }
        }
    }
    Ok(())
}
