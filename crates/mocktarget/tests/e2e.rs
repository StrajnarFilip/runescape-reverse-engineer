//! End-to-end: spawn the mock "game", discover its layout with rsscan, stream it through
//! rs3events + the WebSocket server, and measure write -> client-receive latency.

use futures_util::{SinkExt, StreamExt};
use rs3events::{Envelope, Event, OffsetsFile, Reader, ReaderConfig, Target};
use rsmem::{pe, MemRead, Process};
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio_tungstenite::tungstenite::Message;

const TICK_MS: u64 = 100;
const RUN_TICKS: u64 = 90;

struct Mock {
    child: Child,
    pid: u32,
    rva: u64,
    varps: u64,
    writes: Arc<Mutex<Vec<(u64, String, i64)>>>,
}

impl Drop for Mock {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

fn spawn_mock() -> Mock {
    let mut child = Command::new(env!("CARGO_BIN_EXE_mocktarget"))
        .args(["--tick-ms", &TICK_MS.to_string(), "--ticks", &RUN_TICKS.to_string()])
        .stdout(Stdio::piped())
        .spawn()
        .expect("spawn mocktarget");
    let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
    let ready: serde_json::Value = serde_json::from_str(&lines.next().unwrap().unwrap()).unwrap();
    assert_eq!(ready["ready"], true);
    let writes = Arc::new(Mutex::new(Vec::new()));
    let w = writes.clone();
    std::thread::spawn(move || {
        for l in lines.map_while(Result::ok) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&l) {
                w.lock().unwrap().push((v["t"].as_u64().unwrap(), v["k"].as_str().unwrap().to_string(), v["v"].as_i64().unwrap()));
            }
        }
    });
    Mock {
        pid: ready["pid"].as_u64().unwrap() as u32,
        rva: ready["client_global_rva"].as_u64().unwrap(),
        varps: ready["varps_ptr"].as_u64().unwrap(),
        child,
        writes,
    }
}

fn unix_us() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_micros() as u64
}

/// rsscan against the live mock: value scan, pointer scan, signature generation.
fn discover(mock: &Mock) -> (String, u64) {
    let proc = Process::open(mock.pid).unwrap();
    let modules = proc.modules().unwrap();
    let main = proc.main_module().unwrap();
    let lp_addr = mock.varps + 1000 * 4;

    // 1. Value scan finds the lifepoints var.
    let regions = rsscan::scan::select_regions(&proc.regions(), &rsscan::RegionFilter::default(), &main);
    let cur: i32 = proc.read(lp_addr).unwrap();
    let cands = rsscan::scan::first_scan(&proc, &regions, rsoffsets::ValType::I32, 4, &rsscan::Cond::Eq(vec![cur as f64]), 0.0).unwrap();
    assert!(cands.iter().any(|c| c.0 == lp_addr), "first scan missed lifepoints ({} cands)", cands.len());

    // 2. Pointer scan from the var array base finds CLIENT -> +0x40 -> array.
    let paths = rsscan::ptrscan(&proc, &regions, std::slice::from_ref(&main), mock.varps, &rsscan::PtrScanOpts::default(), &mut |_| {});
    let want = rsscan::PtrPath { module: main.name.clone(), module_offset: mock.rva, chain: vec![0x40, 0] };
    assert!(paths.contains(&want), "ptrscan did not find {want:?}; got {:?}", &paths[..paths.len().min(10)]);
    assert_eq!(want.resolve(&proc, &modules), Some(mock.varps));

    // 3. Signature for the CLIENT global resolves back to it.
    let hdr = pe::read_headers(&proc, main.base).unwrap();
    let code = pe::read_code(&proc, main.base, &hdr);
    let sigs = rsscan::siggen(&code, main.base + mock.rva, 3);
    assert!(!sigs.is_empty(), "no signature for CLIENT");
    let s = &sigs[0];
    let toml = format!(
        "[bases.client]\npattern = \"{}\"\ndisp_offset = {}\ninstr_len = {}\n",
        s.pattern, s.disp_offset, s.instr_len
    );
    let cfg = OffsetsFile::parse(&toml).unwrap();
    let mut cache = rsoffsets::CodeCache::default();
    let anchors = rsoffsets::resolve_anchors(&cfg, &proc, &modules, &main, &mut cache);
    assert_eq!(anchors.map.get("client"), Some(&(main.base + mock.rva)), "{anchors:?}");
    (s.pattern.clone(), s.disp_offset as u64 * 1000 + s.instr_len as u64)
}

fn offsets_for(pattern: &str, disp_offset: u64, instr_len: u64) -> OffsetsFile {
    OffsetsFile::parse(&format!(
        r#"
[sampler]
period_us = 1000
npc_period_us = 1000

# Anchor = address of the CLIENT global (a pointer). Chains deref first, then add.
[bases.client]
pattern = "{pattern}"
disp_offset = {disp_offset}
instr_len = {instr_len}

[bases.varps]
pattern = "{pattern}"
disp_offset = {disp_offset}
instr_len = {instr_len}
chain = [0x40, 0]

[vars.player]
base = "varps"

[signals.lifepoints]
var = 1000
[signals.lifepoints_max]
var = 1001
[signals.prayer]
var = 1002
[signals.prayer_max]
var = 1003
[signals.adrenaline]
var = 1004
max = 1000
[signals.last_ability_tick]
var = 1005
[signals.tick]
base = "client"
chain = [0x58]
type = "u32"

[gcd]
ability_signal = "last_ability_tick"

[npcs]
base = "client"
chain = [0x48, 0]
capacity = 16
struct_size = 0x30
[npcs.fields]
id = {{ offset = 0x10 }}
x = {{ offset = 0x14 }}
y = {{ offset = 0x18 }}
anim = {{ offset = 0x20 }}
spotanim = {{ offset = 0x24 }}
hp = {{ offset = 0x28 }}
hp_max = {{ offset = 0x2C }}

[[checks]]
signal = "lifepoints"
min = 0
max = 100000
le_signal = "lifepoints_max"
"#
    ))
    .unwrap()
}

fn pct(v: &mut [u64], p: f64) -> u64 {
    v.sort_unstable();
    v[((v.len() as f64 - 1.0) * p).round() as usize]
}

#[test]
fn end_to_end_against_mock() {
    let mock = spawn_mock();
    let (pattern, packed) = discover(&mock);
    let offsets = offsets_for(&pattern, packed / 1000, packed % 1000);
    let bosses = vec![rs3events::BossDef {
        name: "mockboss".into(),
        display_name: "Mock Boss".into(),
        npc_ids: vec![9001],
        anims: BTreeMap::from([("101".to_string(), "Auto attack".to_string()), ("202".to_string(), "Special".to_string())]),
        spotanims: BTreeMap::from([("303".to_string(), "Glow".to_string())]),
        idle_anims: vec![-1],
        phase_signal: None,
    }];

    let rt = tokio::runtime::Runtime::new().unwrap();
    let received: Vec<(u64, Envelope)> = rt.block_on(async {
        let reader = Arc::new(Reader::start(ReaderConfig { target: Target::Pid(mock.pid), offsets, bosses }));
        let hub = rs3_eventd::Hub::start(reader.clone(), None).unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(rs3_eventd::serve(listener, hub));
        let (mut ws, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/events")).await.unwrap();
        // Hello first.
        let hello = ws.next().await.unwrap().unwrap();
        assert!(hello.to_text().unwrap().contains("\"hello\""));
        ws.send(Message::Text(r#"{"type":"subscribe","topics":["*"]}"#.into())).await.unwrap();
        let mut got = Vec::new();
        let deadline = Instant::now() + Duration::from_millis(TICK_MS * RUN_TICKS + 1500);
        while Instant::now() < deadline {
            match tokio::time::timeout(Duration::from_millis(200), ws.next()).await {
                Ok(Some(Ok(Message::Text(t)))) => {
                    let at = unix_us();
                    if let Ok(env) = serde_json::from_str::<Envelope>(&t) {
                        got.push((at, env));
                    }
                }
                Ok(Some(Ok(_))) => {}
                Ok(_) => break,
                Err(_) => {}
            }
        }
        reader.stop();
        got
    });

    let mut writes = mock.writes.lock().unwrap().clone();
    // The mock exits right after its final tick; writes in that last tick may never be sampled.
    let last_t = writes.iter().map(|w| w.0).max().unwrap();
    writes.retain(|w| w.0 + TICK_MS * 1000 / 2 < last_t);
    let kinds: BTreeMap<&str, usize> = received.iter().fold(BTreeMap::new(), |mut m, (_, e)| {
        *m.entry(e.ev.kind()).or_default() += 1;
        m
    });
    println!("received event counts: {kinds:?}");
    let statuses: Vec<String> = received
        .iter()
        .filter_map(|(_, e)| match &e.ev {
            Event::Status { state, failing, .. } => Some(format!("{state:?} {failing:?}")),
            _ => None,
        })
        .collect();
    println!("statuses: {statuses:?}");
    assert!(statuses.iter().any(|s| s.starts_with("Ok")), "never reached ok: {statuses:?}");

    // Every player or enemy HP change after the stream starts must arrive promptly.
    let first_ok = received.iter().find(|(_, e)| matches!(e.ev, Event::Lifepoints { .. })).map(|(_, e)| e.unix_us).unwrap();
    let mut latencies = Vec::new();
    for (key, pick) in [
        ("lp", "player.lifepoints"),
        ("prayer", "player.prayer"),
        ("adren", "player.adrenaline"),
        ("hp", "npc.health"),
        ("hp", "boss.hp"),
    ] {
        // Only writes that actually change the value are observable.
        let mut prev: Option<i64> = None;
        let ws_vals: Vec<(u64, i64)> = writes
            .iter()
            .filter(|w| w.1 == key)
            .filter(|w| {
                let changed = prev != Some(w.2);
                prev = Some(w.2);
                changed
            })
            .filter(|w| w.0 > first_ok + 5_000)
            .map(|w| (w.0, w.2))
            .collect();
        let evs: Vec<(u64, i64)> = received
            .iter()
            .filter(|(_, e)| e.ev.kind() == pick)
            .map(|(at, e)| {
                let v = match &e.ev {
                    Event::Lifepoints { new, .. } | Event::Prayer { new, .. } | Event::Adrenaline { new, .. }
                    | Event::NpcHealth { new, .. } => *new,
                    Event::BossHp { hp, .. } => *hp,
                    _ => unreachable!(),
                };
                (*at, v)
            })
            .collect();
        let mut topic_latencies = Vec::new();
        for (t, v) in &ws_vals {
            let hit = evs.iter().find(|(at, ev)| ev == v && *at + 200 >= *t);
            let (at, _) = hit.unwrap_or_else(|| panic!("{key}={v} written at {t} never received"));
            topic_latencies.push(at.saturating_sub(*t));
        }
        assert!(!ws_vals.is_empty(), "no {key} writes observed");
        let p99 = pct(&mut topic_latencies, 0.99);
        println!("{pick} write->websocket latency: p50 {} us, p99 {p99} us", pct(&mut topic_latencies, 0.5));
        assert!(p99 < 5_000, "{pick} p99 latency {p99} us too high");
        latencies.extend(topic_latencies);
    }
    let n = latencies.len();
    let (p50, p99, max) = (pct(&mut latencies, 0.5), pct(&mut latencies, 0.99), *latencies.iter().max().unwrap());
    println!("write->websocket latency over {n} value changes: p50 {p50} us, p99 {p99} us, max {max} us");
    assert!(p99 < 5_000, "p99 latency {p99} us too high");

    // Ticks come from the counter, GCD lasts 3 ticks from each ability.
    let ticks = received.iter().filter(|(_, e)| matches!(e.ev, Event::Tick { basis: rs3events::TickBasis::Counter, .. })).count();
    assert!(ticks as u64 >= RUN_TICKS - 20, "only {ticks} counter ticks");
    let gcds: Vec<(Option<u64>, Option<u64>)> = received
        .iter()
        .filter_map(|(_, e)| match e.ev {
            Event::Gcd { start_tick, end_tick, .. } => Some((start_tick, end_tick)),
            _ => None,
        })
        .collect();
    assert!(gcds.len() >= 10, "only {} gcd events", gcds.len());
    for (s, e) in &gcds {
        assert_eq!(e.unwrap() - s.unwrap(), 3);
    }
    let readies = received.iter().filter(|(_, e)| matches!(e.ev, Event::GcdReady { .. })).count();
    assert!(readies + 1 >= gcds.len(), "{readies} ready vs {} gcd", gcds.len());
    // GCD ready lands on the end tick.
    for (i, (_, e)) in received.iter().enumerate() {
        if let Event::GcdReady { .. } = e.ev {
            let prev_gcd = received[..i].iter().rev().find_map(|(_, x)| match x.ev {
                Event::Gcd { end_tick, .. } => end_tick,
                _ => None,
            });
            assert_eq!(e.tick, prev_gcd, "gcd_ready at tick {:?}, expected {:?}", e.tick, prev_gcd);
        }
    }

    // NPCs, boss actions, boss HP, despawn/respawn.
    assert!(kinds.get("npc.spawn").copied().unwrap_or(0) >= 2, "spawns: {kinds:?}");
    let actions: Vec<String> = received
        .iter()
        .filter_map(|(_, e)| match &e.ev {
            Event::BossAction { action, .. } => Some(action.clone()),
            _ => None,
        })
        .collect();
    assert!(actions.iter().any(|a| a == "Auto attack"), "{actions:?}");
    assert!(actions.iter().any(|a| a == "Special"), "{actions:?}");
    assert!(actions.iter().any(|a| a == "Glow"), "{actions:?}");
    let boss_hp = received.iter().filter(|(_, e)| matches!(e.ev, Event::BossHp { .. })).count();
    let hp_writes = writes.iter().filter(|w| w.1 == "hp" && w.0 > first_ok + 5_000).count();
    assert!(boss_hp >= hp_writes, "boss hp events {boss_hp} < writes {hp_writes}");
    assert!(kinds.get("npc.despawn").copied().unwrap_or(0) >= 1, "{kinds:?}");
}
