//! Event, snapshot and WebSocket protocol types.
//!
//! Every event travels as an [`Envelope`]: timing metadata plus a flattened,
//! `type`-tagged [`Event`]. JSON example:
//!
//! ```json
//! {"seq":42,"t_us":1234567,"unix_us":1762600000000000,"tick":815,"tick_phase_ms":3.2,
//!  "src":20392,"type":"player.adrenaline","old":40,"new":48,"pct":48.0}
//! ```

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const PROTOCOL_VERSION: u32 = 1;

/// A number read from memory: integer or float.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Num {
    I(i64),
    F(f64),
}

impl Num {
    pub fn as_f64(&self) -> f64 {
        match *self {
            Num::I(v) => v as f64,
            Num::F(v) => v,
        }
    }
    pub fn as_i64(&self) -> i64 {
        match *self {
            Num::I(v) => v,
            Num::F(v) => v as i64,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceState {
    /// Process found, resolving offsets.
    Attaching,
    /// Offsets resolved but values unreadable (e.g. lobby / loading screen).
    Waiting,
    /// Streaming game events.
    Ok,
    /// Offsets failed to resolve or failed sanity checks; no game events are emitted.
    OffsetsStale,
    /// The process exited.
    Detached,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TickBasis {
    /// From the client's own server-tick counter in memory.
    Counter,
    /// Phase-locked estimate from the timing of server-driven value changes.
    Estimated,
}

/// One NPC as currently observed.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct NpcInfo {
    pub uid: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub npc_id: Option<i32>,
    /// Display name when the NPC matches a boss definition.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub x: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub y: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub plane: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hp: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hp_max: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hp_pct: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub anim: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spotanim: Option<i32>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub extra: BTreeMap<String, Num>,
}

/// Game events. Field names must not collide with [`Envelope`] fields
/// (`seq`, `t_us`, `unix_us`, `tick`, `tick_phase_ms`, `src`, `type`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum Event {
    #[serde(rename = "status")]
    Status {
        state: SourceState,
        #[serde(skip_serializing_if = "Option::is_none")]
        build: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        label: Option<String>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        failing: Vec<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        detail: Option<String>,
    },
    #[serde(rename = "tick")]
    Tick {
        n: u64,
        basis: TickBasis,
        #[serde(skip_serializing_if = "Option::is_none")]
        interval_ms: Option<f64>,
    },
    #[serde(rename = "player.lifepoints")]
    Lifepoints { old: Option<i64>, new: i64, max: Option<i64> },
    #[serde(rename = "player.prayer")]
    Prayer { old: Option<i64>, new: i64, max: Option<i64> },
    #[serde(rename = "player.adrenaline")]
    Adrenaline { old: Option<i64>, new: i64, pct: Option<f64> },
    #[serde(rename = "player.ability_used")]
    AbilityUsed {
        /// What detected it: `signal:<name>` or `adrenaline_heuristic`.
        basis: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        value: Option<Num>,
    },
    #[serde(rename = "player.gcd")]
    Gcd {
        basis: String,
        start_tick: Option<u64>,
        end_tick: Option<u64>,
        duration_ms: f64,
        /// Wall-clock estimate of when the GCD ends (same clock as `unix_us`).
        ends_at_unix_us: u64,
    },
    #[serde(rename = "player.gcd_ready")]
    GcdReady { basis: String },
    /// A configured signal that has no dedicated event type.
    #[serde(rename = "signal")]
    Signal { name: String, old: Option<Num>, new: Num },
    #[serde(rename = "npc.spawn")]
    NpcSpawn { npc: NpcInfo },
    #[serde(rename = "npc.despawn")]
    NpcDespawn { uid: u64, npc_id: Option<i32> },
    #[serde(rename = "npc.health")]
    NpcHealth { uid: u64, npc_id: Option<i32>, old: Option<i64>, new: i64, max: Option<i64>, pct: Option<f64> },
    #[serde(rename = "npc.animation")]
    NpcAnimation { uid: u64, npc_id: Option<i32>, old: Option<i32>, new: i32 },
    #[serde(rename = "npc.spotanim")]
    NpcSpotanim { uid: u64, npc_id: Option<i32>, old: Option<i32>, new: i32 },
    /// Any other configured NPC field.
    #[serde(rename = "npc.field")]
    NpcField { uid: u64, npc_id: Option<i32>, name: String, old: Option<Num>, new: Num },
    #[serde(rename = "boss.action")]
    BossAction {
        boss: String,
        uid: u64,
        action: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        anim_id: Option<i32>,
        #[serde(skip_serializing_if = "Option::is_none")]
        spotanim_id: Option<i32>,
    },
    #[serde(rename = "boss.hp")]
    BossHp { boss: String, uid: u64, hp: i64, max: Option<i64>, pct: Option<f64> },
    #[serde(rename = "boss.phase")]
    BossPhase { boss: String, uid: u64, phase: i64 },
}

impl Event {
    /// The `type` tag as it appears in JSON.
    pub fn kind(&self) -> &'static str {
        match self {
            Event::Status { .. } => "status",
            Event::Tick { .. } => "tick",
            Event::Lifepoints { .. } => "player.lifepoints",
            Event::Prayer { .. } => "player.prayer",
            Event::Adrenaline { .. } => "player.adrenaline",
            Event::AbilityUsed { .. } => "player.ability_used",
            Event::Gcd { .. } => "player.gcd",
            Event::GcdReady { .. } => "player.gcd_ready",
            Event::Signal { .. } => "signal",
            Event::NpcSpawn { .. } => "npc.spawn",
            Event::NpcDespawn { .. } => "npc.despawn",
            Event::NpcHealth { .. } => "npc.health",
            Event::NpcAnimation { .. } => "npc.animation",
            Event::NpcSpotanim { .. } => "npc.spotanim",
            Event::NpcField { .. } => "npc.field",
            Event::BossAction { .. } => "boss.action",
            Event::BossHp { .. } => "boss.hp",
            Event::BossPhase { .. } => "boss.phase",
        }
    }

    /// Subscription topic: the kind, except boss events become `boss.<name>.<what>`
    /// and signals become `signal.<name>`.
    pub fn topic(&self) -> String {
        match self {
            Event::BossAction { boss, .. } => format!("boss.{boss}.action"),
            Event::BossHp { boss, .. } => format!("boss.{boss}.hp"),
            Event::BossPhase { boss, .. } => format!("boss.{boss}.phase"),
            Event::Signal { name, .. } => format!("signal.{name}"),
            other => other.kind().to_string(),
        }
    }
}

/// An event plus timing metadata.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Envelope {
    /// Monotonic sequence number across all sources.
    pub seq: u64,
    /// Microseconds since the reader started (monotonic clock).
    pub t_us: u64,
    /// Wall clock (microseconds since Unix epoch) when the change was observed.
    pub unix_us: u64,
    /// Game tick when observed, if known.
    pub tick: Option<u64>,
    /// Milliseconds since the start of that tick, if known.
    pub tick_phase_ms: Option<f64>,
    /// Source process id.
    pub src: u32,
    #[serde(flatten)]
    pub ev: Event,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct PlayerState {
    pub lifepoints: Option<i64>,
    pub lifepoints_max: Option<i64>,
    pub prayer: Option<i64>,
    pub prayer_max: Option<i64>,
    pub adrenaline: Option<i64>,
    pub adrenaline_pct: Option<f64>,
    pub gcd_end_tick: Option<u64>,
    pub gcd_ends_at_unix_us: Option<u64>,
}

/// Full current state of one source.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Snapshot {
    pub src: u32,
    pub state: SourceState,
    pub build: Option<String>,
    pub label: Option<String>,
    pub tick: Option<u64>,
    pub player: PlayerState,
    pub npcs: Vec<NpcInfo>,
    pub signals: BTreeMap<String, Num>,
    pub unix_us: u64,
}

/// Server -> client control messages (events are sent as bare [`Envelope`]s).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMsg {
    Hello { protocol: u32, server: String, sources: Vec<u32> },
    Snapshot { snapshot: Snapshot },
    /// The client fell behind and missed events; a fresh snapshot follows.
    Resync { missed: u64 },
    Error { message: String },
}

/// Client -> server messages.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMsg {
    /// Topic patterns: exact (`player.prayer`), prefix (`player.*`, `boss.telos.*`) or `*`.
    Subscribe { topics: Vec<String> },
    /// Restrict to one source pid (`null` = all).
    Source { src: Option<u32> },
    /// Ask for fresh snapshots.
    Snapshot,
}

/// Does `topic` match any of the subscription `patterns`?
pub fn topic_matches(patterns: &[String], topic: &str) -> bool {
    patterns.iter().any(|p| {
        if p == "*" {
            true
        } else if let Some(prefix) = p.strip_suffix(".*") {
            topic == prefix || (topic.starts_with(prefix) && topic[prefix.len()..].starts_with('.'))
        } else {
            p == topic
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_roundtrip_flattened() {
        let e = Envelope {
            seq: 7,
            t_us: 100,
            unix_us: 1_762_600_000_000_000,
            tick: Some(9),
            tick_phase_ms: Some(1.5),
            src: 4242,
            ev: Event::Adrenaline { old: Some(40), new: 48, pct: Some(48.0) },
        };
        let s = serde_json::to_string(&e).unwrap();
        assert!(s.contains(r#""type":"player.adrenaline""#), "{s}");
        assert!(s.contains(r#""new":48"#), "{s}");
        let back: Envelope = serde_json::from_str(&s).unwrap();
        assert_eq!(back, e);
    }

    #[test]
    fn boss_and_npc_roundtrip() {
        for ev in [
            Event::BossAction { boss: "telos".into(), uid: 1, action: "Tendrils".into(), anim_id: Some(5), spotanim_id: None },
            Event::NpcSpawn { npc: NpcInfo { uid: 3, npc_id: Some(9001), anim: Some(-1), ..Default::default() } },
            Event::Signal { name: "x".into(), old: None, new: Num::F(1.5) },
            Event::Tick { n: 5, basis: TickBasis::Counter, interval_ms: Some(600.0) },
        ] {
            let e = Envelope { seq: 1, t_us: 1, unix_us: 1, tick: None, tick_phase_ms: None, src: 1, ev };
            let s = serde_json::to_string(&e).unwrap();
            let back: Envelope = serde_json::from_str(&s).unwrap();
            assert_eq!(back, e, "{s}");
        }
    }

    #[test]
    fn topics() {
        let ev = Event::BossHp { boss: "telos".into(), uid: 1, hp: 1, max: None, pct: None };
        assert_eq!(ev.topic(), "boss.telos.hp");
        let pats = vec!["player.*".to_string(), "boss.telos.*".to_string()];
        assert!(topic_matches(&pats, "player.prayer"));
        assert!(topic_matches(&pats, "boss.telos.hp"));
        assert!(!topic_matches(&pats, "boss.other.hp"));
        assert!(!topic_matches(&pats, "playerx.foo"));
        assert!(topic_matches(&["*".to_string()], "tick"));
        assert!(topic_matches(&["tick".to_string()], "tick"));
    }

    #[test]
    fn client_msgs_parse() {
        let m: ClientMsg = serde_json::from_str(r#"{"type":"subscribe","topics":["player.*"]}"#).unwrap();
        assert_eq!(m, ClientMsg::Subscribe { topics: vec!["player.*".into()] });
        let m: ClientMsg = serde_json::from_str(r#"{"type":"source","src":null}"#).unwrap();
        assert_eq!(m, ClientMsg::Source { src: None });
    }
}
