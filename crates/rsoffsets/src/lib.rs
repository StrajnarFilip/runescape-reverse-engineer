//! Declarative description of where game state lives in client memory.
//!
//! Everything that changes when Jagex ships a client update lives in
//! `offsets.toml` (loaded at runtime), so a patch-day fix never needs a rebuild.
//!
//! Address model:
//! * A **base** has a static *anchor* resolved once per attach: either
//!   `module + module_offset`, or a code signature (`pattern`) whose
//!   RIP-relative operand points at a global. Then `add` is applied.
//! * It may have a **chain**, applied every sample (pointers move):
//!   for each offset, `addr = read_ptr(addr) + offset` (Cheat Engine semantics).
//! * A **var array** is a base whose final address is the start of an
//!   `i32` array indexed by var id.
//! * A **signal** is a typed value at `base/var + offset`.

mod bosses;
mod resolve;
mod valtype;

pub use bosses::{load_bosses, BossDef};
pub use resolve::{resolve_anchors, Anchors, CodeCache};
pub use valtype::ValType;

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct OffsetsFile {
    #[serde(default)]
    pub client: ClientCfg,
    #[serde(default)]
    pub sampler: SamplerCfg,
    #[serde(default)]
    pub bases: BTreeMap<String, BaseDef>,
    #[serde(default)]
    pub vars: BTreeMap<String, VarArrayDef>,
    /// Containers found by scanning memory at attach (survive client restarts).
    #[serde(default)]
    pub locators: BTreeMap<String, LocatorDef>,
    #[serde(default)]
    pub signals: BTreeMap<String, SignalDef>,
    pub npcs: Option<NpcTableDef>,
    #[serde(default)]
    pub gcd: GcdCfg,
    #[serde(default)]
    pub checks: Vec<CheckDef>,
}

impl OffsetsFile {
    pub fn load(path: impl AsRef<Path>) -> anyhow::Result<Self> {
        let path = path.as_ref();
        let text = std::fs::read_to_string(path).map_err(|e| anyhow::anyhow!("reading {}: {e}", path.display()))?;
        Self::parse(&text).map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))
    }

    pub fn parse(text: &str) -> anyhow::Result<Self> {
        let f: OffsetsFile = toml::from_str(text)?;
        f.validate()?;
        Ok(f)
    }

    /// Check cross-references (signals -> bases/vars, etc.).
    pub fn validate(&self) -> anyhow::Result<()> {
        for (name, s) in &self.signals {
            match (&s.base, s.var, &s.locator) {
                (Some(b), None, None) => {
                    if !self.bases.contains_key(b) {
                        anyhow::bail!("signal {name}: unknown base {b:?}");
                    }
                }
                (None, Some(_), None) => {
                    if !self.vars.contains_key(&s.var_array) {
                        anyhow::bail!("signal {name}: unknown var array {:?}", s.var_array);
                    }
                }
                (None, None, Some(l)) => {
                    if !self.locators.contains_key(l) {
                        anyhow::bail!("signal {name}: unknown locator {l:?}");
                    }
                    if s.key.is_none() {
                        anyhow::bail!("signal {name}: locator signals need `key`");
                    }
                }
                _ => anyhow::bail!("signal {name}: set exactly one of `base`, `var` or `locator`"),
            }
            if let Some((lo, hi)) = s.bits {
                if lo > hi || hi > 63 {
                    anyhow::bail!("signal {name}: bad bits [{lo}, {hi}]");
                }
            }
        }
        for (name, v) in &self.vars {
            if !self.bases.contains_key(&v.base) {
                anyhow::bail!("var array {name}: unknown base {:?}", v.base);
            }
        }
        for (name, l) in &self.locators {
            if let LocatorDef::VarIndex { via, probe_keys, .. } = l {
                if !matches!(self.locators.get(via), Some(LocatorDef::SortedTable { .. })) {
                    anyhow::bail!("locator {name}: `via` must name a sorted_table locator, got {via:?}");
                }
                if probe_keys.is_empty() {
                    anyhow::bail!("locator {name}: needs at least one probe key");
                }
            }
        }
        if let Some(n) = &self.npcs {
            if !self.bases.contains_key(&n.base) {
                anyhow::bail!("npcs: unknown base {:?}", n.base);
            }
        }
        for (name, b) in &self.bases {
            if b.module_offset.is_none() && b.pattern.is_none() {
                anyhow::bail!("base {name}: needs `module_offset` and/or `pattern`");
            }
            if let Some(p) = &b.pattern {
                rsmem::Pattern::parse(p).map_err(|e| anyhow::anyhow!("base {name}: {e}"))?;
                if b.pattern_kind == PatternKind::Rip && (b.disp_offset.is_none() || b.instr_len.is_none()) {
                    anyhow::bail!("base {name}: rip pattern needs `disp_offset` and `instr_len`");
                }
            }
        }
        for s in [&self.gcd.ability_signal, &self.gcd.remaining_signal].into_iter().flatten() {
            if !self.signals.contains_key(s) {
                anyhow::bail!("gcd: unknown signal {s:?}");
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClientCfg {
    #[serde(default = "default_exe")]
    pub exe: String,
    #[serde(default = "default_title")]
    pub window_title_prefix: String,
    /// Build id these offsets were made for (`<pe timestamp>-<image size>`).
    pub build: Option<String>,
}

fn default_exe() -> String {
    "rs2client.exe".into()
}
fn default_title() -> String {
    "RuneScape".into()
}

impl Default for ClientCfg {
    fn default() -> Self {
        Self { exe: default_exe(), window_title_prefix: default_title(), build: None }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SamplerCfg {
    /// Player/signal sampling period in microseconds.
    #[serde(default = "d_period")]
    pub period_us: u64,
    /// NPC table sampling period in microseconds.
    #[serde(default = "d_npc_period")]
    pub npc_period_us: u64,
    /// How many times to re-read a block whose two reads disagree.
    #[serde(default = "d_retries")]
    pub torn_read_retries: u32,
    /// How long checks must fail before leaving the `ok` state.
    #[serde(default = "d_grace")]
    pub check_grace_ms: u64,
}

fn d_period() -> u64 {
    1000
}
fn d_npc_period() -> u64 {
    5000
}
fn d_retries() -> u32 {
    3
}
fn d_grace() -> u64 {
    500
}

impl Default for SamplerCfg {
    fn default() -> Self {
        Self { period_us: d_period(), npc_period_us: d_npc_period(), torn_read_retries: d_retries(), check_grace_ms: d_grace() }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PatternKind {
    /// The match is an instruction with a RIP-relative operand; the anchor is its target.
    #[default]
    Rip,
    /// The anchor is the match address itself.
    Match,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct BaseDef {
    /// Module the anchor lives in (default: the main executable).
    pub module: Option<String>,
    pub module_offset: Option<u64>,
    pub pattern: Option<String>,
    #[serde(default)]
    pub pattern_kind: PatternKind,
    /// Offset of the 4-byte displacement inside the matched instruction.
    pub disp_offset: Option<u32>,
    /// Length of the matched instruction.
    pub instr_len: Option<u32>,
    /// Added to the anchor after resolution.
    #[serde(default)]
    pub add: i64,
    /// Pointer chain applied every sample.
    #[serde(default)]
    pub chain: Vec<i64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VarArrayDef {
    pub base: String,
    /// Extra chain after the base's own chain.
    #[serde(default)]
    pub chain: Vec<i64>,
    #[serde(default = "d_stride")]
    pub stride: u32,
}

fn d_stride() -> u32 {
    4
}

fn d_var_array() -> String {
    "player".into()
}

/// How to find a keyed container by scanning memory.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum LocatorDef {
    /// A contiguous array of fixed-size entries sorted by an i32 key (e.g. the varp table).
    /// Found by looking for `probe_keys[0]` and walking a strictly increasing run of keys.
    SortedTable {
        stride: u32,
        key_off: u32,
        value_off: u32,
        probe_keys: Vec<i64>,
        #[serde(default = "d_min_entries")]
        min_entries: u32,
        /// Reject longer runs (e.g. definition tables with every id).
        max_entries: Option<u32>,
    },
    /// Hash-table nodes recognised by a self-link (`[node + self_off] == node + link_off`),
    /// a tag qword at +0 and an i32 key.
    HashNodes {
        /// Optional qword at +0 (not reliable for RS3 var nodes: it's leftover allocator data).
        tag: Option<u64>,
        key_off: u32,
        value_off: u32,
        link_off: u32,
        self_off: u32,
        /// Only accept nodes that some other memory points to at `+key_off` (live, not freed).
        #[serde(default = "d_true")]
        require_ref: bool,
    },
    /// The client's var hash index: a chained hash table of `buckets` slots. Slot `key % buckets`
    /// points at the first `{i32 key @0, value @8, next @0x28}` entry of its chain (varp table
    /// rows and var nodes alike). Found through `via` (a sorted_table locator): every pointer into
    /// that table votes for an index start, and the winner is verified with chained lookups.
    VarIndex {
        via: String,
        probe_keys: Vec<i64>,
        buckets: u64,
        #[serde(default)]
        entry_key_off: u32,
        #[serde(default = "d_entry_value_off")]
        entry_value_off: u32,
        #[serde(default = "d_entry_next_off")]
        entry_next_off: u32,
    },
    /// Heap objects whose first qword is a vtable at `module + vtable_rva`.
    /// Signals use `key = 0` and read at the signal's `offset` inside the object.
    VtableObject {
        vtable_rva: u64,
        module: Option<String>,
        /// When many objects share the vtable, pick one whose u32 at this offset equals the
        /// most common non-zero value among them (in-sync copies, e.g. the tick counter).
        pick_by_mode_at: Option<u32>,
    },
}

fn d_min_entries() -> u32 {
    256
}
fn d_entry_value_off() -> u32 {
    8
}
fn d_entry_next_off() -> u32 {
    0x28
}

impl LocatorDef {
    pub fn key_off(&self) -> u32 {
        match self {
            LocatorDef::SortedTable { key_off, .. } | LocatorDef::HashNodes { key_off, .. } => *key_off,
            LocatorDef::VarIndex { entry_key_off, .. } => *entry_key_off,
            LocatorDef::VtableObject { .. } => 0,
        }
    }
    pub fn value_off(&self) -> u32 {
        match self {
            LocatorDef::SortedTable { value_off, .. } | LocatorDef::HashNodes { value_off, .. } => *value_off,
            LocatorDef::VarIndex { entry_value_off, .. } => *entry_value_off,
            LocatorDef::VtableObject { .. } => 0,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SignalDef {
    pub base: Option<String>,
    /// Keyed lookup in a located container (with `key`).
    pub locator: Option<String>,
    pub key: Option<i64>,
    /// Var id inside `var_array` (mutually exclusive with `base`).
    pub var: Option<i64>,
    #[serde(default = "d_var_array")]
    pub var_array: String,
    /// Extra chain after the base's chain (base signals only).
    #[serde(default)]
    pub chain: Vec<i64>,
    #[serde(default)]
    pub offset: i64,
    #[serde(rename = "type", default = "d_type")]
    pub ty: ValType,
    /// Inclusive bit range `[lo, hi]` for varbit-style packed values.
    pub bits: Option<(u8, u8)>,
    /// Constant maximum (used for `*_max` / percentages when no `<name>_max` signal exists).
    pub max: Option<i64>,
    /// Multiply the raw value by this (e.g. 0.1). Integers stay integers when absent.
    pub scale: Option<f64>,
    #[serde(default)]
    pub note: String,
}

fn d_type() -> ValType {
    ValType::I32
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NpcLayout {
    /// Array of pointers to NPC structs (null = empty slot).
    #[default]
    PtrArray,
    /// Array of NPC structs stored inline.
    Inline,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FieldDef {
    pub offset: u32,
    #[serde(rename = "type", default = "d_type")]
    pub ty: ValType,
    pub bits: Option<(u8, u8)>,
    /// Emit `npc.field` events when this (non-standard) field changes.
    #[serde(default = "d_true")]
    pub emit: bool,
}

fn d_true() -> bool {
    true
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NpcTableDef {
    pub base: String,
    #[serde(default)]
    pub chain: Vec<i64>,
    #[serde(default)]
    pub layout: NpcLayout,
    pub capacity: u32,
    /// Bytes between entries (8 for a pointer array).
    #[serde(default = "d_ptr_stride")]
    pub stride: u32,
    /// Added to each entry pointer before reading the struct (ptr_array).
    #[serde(default)]
    pub entry_offset: i64,
    /// Bytes to read per NPC struct.
    pub struct_size: u32,
    /// Accept only entries whose `id` lies in this range.
    #[serde(default = "d_id_range")]
    pub id_range: (i64, i64),
    /// Max value of the `headbar` field (for percentage).
    pub headbar_max: Option<i64>,
    /// Standard names: id, uid, valid, x, y, plane, anim, spotanim, hp, hp_max, headbar.
    /// Anything else is reported as `npc.field`.
    #[serde(default)]
    pub fields: BTreeMap<String, FieldDef>,
}

fn d_ptr_stride() -> u32 {
    8
}
fn d_id_range() -> (i64, i64) {
    (0, 0x7FFF_FFFF)
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GcdUnit {
    /// Remaining game ticks.
    #[default]
    Ticks,
    /// Remaining milliseconds.
    Ms,
    /// Absolute tick number at which the GCD ends (compared to the `tick` signal).
    EndTick,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GcdCfg {
    /// GCD length in game ticks (RS3: 3 ticks = 1.8 s).
    #[serde(default = "d_gcd_ticks")]
    pub ticks: u32,
    /// A signal that changes whenever an ability is used (e.g. "last ability tick").
    pub ability_signal: Option<String>,
    /// A signal that directly holds the remaining GCD (wins over derivation).
    pub remaining_signal: Option<String>,
    #[serde(default)]
    pub remaining_unit: GcdUnit,
    /// Treat any adrenaline change outside an active GCD as ability use (rough fallback).
    #[serde(default)]
    pub derive_from_adrenaline: bool,
}

fn d_gcd_ticks() -> u32 {
    3
}

impl Default for GcdCfg {
    fn default() -> Self {
        Self { ticks: d_gcd_ticks(), ability_signal: None, remaining_signal: None, remaining_unit: GcdUnit::Ticks, derive_from_adrenaline: false }
    }
}

/// A sanity check that must pass before events are emitted.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CheckDef {
    pub signal: String,
    pub min: Option<f64>,
    pub max: Option<f64>,
    /// Must be <= this other signal's value.
    pub le_signal: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXAMPLE: &str = r#"
[client]
build = "65432100-5000"

[bases.client]
pattern = "48 8B 05 ?? ?? ?? ?? 48 85 C0"
disp_offset = 3
instr_len = 7
module_offset = 0x1234

[bases.varps]
module_offset = 0x2000
chain = [0x40, 0]

[vars.player]
base = "varps"

[signals.lifepoints]
var = 1000
max = 9900

[signals.prayer]
var = 1002
bits = [0, 9]

[signals.tick]
base = "client"
chain = [0x58]
type = "u32"

[npcs]
base = "client"
chain = [0x48, 0]
capacity = 64
struct_size = 0x60
[npcs.fields]
id = { offset = 0x10 }
anim = { offset = 0x20 }

[gcd]
ability_signal = "tick"

[[checks]]
signal = "lifepoints"
min = 0
max = 50000
"#;

    #[test]
    fn parses_example() {
        let f = OffsetsFile::parse(EXAMPLE).unwrap();
        assert_eq!(f.client.exe, "rs2client.exe");
        assert_eq!(f.bases["varps"].chain, vec![0x40, 0]);
        assert_eq!(f.signals["prayer"].bits, Some((0, 9)));
        assert_eq!(f.signals["tick"].ty, ValType::U32);
        assert_eq!(f.npcs.as_ref().unwrap().fields["anim"].offset, 0x20);
        assert_eq!(f.sampler.period_us, 1000);
        assert_eq!(f.gcd.ticks, 3);
    }

    #[test]
    fn rejects_bad_refs() {
        let bad = "[signals.x]\nbase = \"nope\"\n";
        assert!(OffsetsFile::parse(bad).is_err());
        let both = "[bases.b]\nmodule_offset = 1\n[vars.player]\nbase=\"b\"\n[signals.x]\nbase = \"b\"\nvar = 1\n";
        assert!(OffsetsFile::parse(both).is_err());
        let empty = OffsetsFile::parse("").unwrap();
        assert!(empty.signals.is_empty());
    }
}
