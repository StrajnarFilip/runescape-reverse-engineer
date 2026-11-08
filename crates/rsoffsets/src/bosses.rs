use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

/// A boss definition loaded from `data/bosses/<name>.toml`.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct BossDef {
    /// Short id used in topics, e.g. `telos` -> `boss.telos.action`.
    pub name: String,
    #[serde(default)]
    pub display_name: String,
    /// NPC config ids that belong to this boss (all phases / transforms).
    #[serde(default)]
    pub npc_ids: Vec<i32>,
    /// Animation id -> action name. Keys are strings because TOML keys are strings.
    #[serde(default)]
    pub anims: BTreeMap<String, String>,
    /// Spotanim (graphic) id -> action name.
    #[serde(default)]
    pub spotanims: BTreeMap<String, String>,
    /// Animation ids that mean "doing nothing".
    #[serde(default = "d_idle")]
    pub idle_anims: Vec<i32>,
    /// Optional signal whose value is the boss phase.
    pub phase_signal: Option<String>,
}

fn d_idle() -> Vec<i32> {
    vec![-1, 0xFFFF]
}

impl BossDef {
    pub fn matches(&self, npc_id: i32) -> bool {
        self.npc_ids.contains(&npc_id)
    }

    pub fn anim_action(&self, anim: i32) -> Option<&str> {
        self.anims.get(&anim.to_string()).map(String::as_str)
    }

    pub fn spotanim_action(&self, spot: i32) -> Option<&str> {
        self.spotanims.get(&spot.to_string()).map(String::as_str)
    }

    pub fn is_idle(&self, anim: i32) -> bool {
        self.idle_anims.contains(&anim)
    }

    pub fn display(&self) -> &str {
        if self.display_name.is_empty() {
            &self.name
        } else {
            &self.display_name
        }
    }
}

/// Load every `*.toml` in `dir` as a [`BossDef`]. A missing directory yields no bosses.
pub fn load_bosses(dir: impl AsRef<Path>) -> anyhow::Result<Vec<BossDef>> {
    let dir = dir.as_ref();
    let mut out = Vec::new();
    let Ok(rd) = std::fs::read_dir(dir) else {
        return Ok(out);
    };
    let mut paths: Vec<_> = rd.filter_map(|e| e.ok().map(|e| e.path())).collect();
    paths.sort();
    for p in paths {
        if p.extension().and_then(|e| e.to_str()) != Some("toml") {
            continue;
        }
        let text = std::fs::read_to_string(&p)?;
        let mut def: BossDef = toml::from_str(&text).map_err(|e| anyhow::anyhow!("{}: {e}", p.display()))?;
        if def.name.is_empty() {
            def.name = p.file_stem().and_then(|s| s.to_str()).unwrap_or("boss").to_string();
        }
        for k in def.anims.keys().chain(def.spotanims.keys()) {
            k.parse::<i32>().map_err(|_| anyhow::anyhow!("{}: key {k:?} is not an integer id", p.display()))?;
        }
        out.push(def);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_boss() {
        let d: BossDef = toml::from_str(
            r#"
name = "telos"
npc_ids = [9001]
[anims]
"123" = "Tendrils"
"#,
        )
        .unwrap();
        assert!(d.matches(9001));
        assert_eq!(d.anim_action(123), Some("Tendrils"));
        assert!(d.is_idle(-1));
        assert_eq!(d.display(), "telos");
    }
}
