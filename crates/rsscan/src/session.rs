//! Scan session persisted on disk so every CLI command is a single, non-interactive step.

use anyhow::Result;
use rsoffsets::ValType;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct State {
    pub pid: Option<u32>,
    pub ty: Option<ValType>,
    pub align: Option<usize>,
    pub count: usize,
    pub has_snapshot: bool,
    pub history: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Found {
    /// Absolute address at the time it was saved (hex).
    pub addr: String,
    #[serde(rename = "type")]
    pub ty: ValType,
    #[serde(default)]
    pub note: String,
}

pub struct Session {
    pub dir: PathBuf,
}

impl Session {
    pub fn open(dir: impl AsRef<Path>) -> Result<Self> {
        let dir = dir.as_ref().to_path_buf();
        std::fs::create_dir_all(&dir)?;
        Ok(Self { dir })
    }

    fn p(&self, name: &str) -> PathBuf {
        self.dir.join(name)
    }

    pub fn snapshot_path(&self) -> PathBuf {
        self.p("snapshot.bin")
    }
    pub fn ptrs_path(&self, name: &str) -> PathBuf {
        self.p(&format!("ptrs_{name}.json"))
    }
    pub fn watch_log_path(&self) -> PathBuf {
        self.p("watch.jsonl")
    }

    pub fn state(&self) -> State {
        std::fs::read_to_string(self.p("session.json")).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
    }

    pub fn save_state(&self, s: &State) -> Result<()> {
        std::fs::write(self.p("session.json"), serde_json::to_string_pretty(s)?)?;
        Ok(())
    }

    pub fn save_cands(&self, cands: &[(u64, u64)]) -> Result<()> {
        let mut w = std::io::BufWriter::new(std::fs::File::create(self.p("cands.bin"))?);
        for (a, v) in cands {
            w.write_all(&a.to_le_bytes())?;
            w.write_all(&v.to_le_bytes())?;
        }
        w.flush()?;
        Ok(())
    }

    pub fn load_cands(&self) -> Result<Vec<(u64, u64)>> {
        let mut bytes = Vec::new();
        std::fs::File::open(self.p("cands.bin"))?.read_to_end(&mut bytes)?;
        Ok(bytes
            .chunks_exact(16)
            .map(|c| (u64::from_le_bytes(c[0..8].try_into().unwrap()), u64::from_le_bytes(c[8..16].try_into().unwrap())))
            .collect())
    }

    pub fn found(&self) -> BTreeMap<String, Found> {
        std::fs::read_to_string(self.p("found.toml")).ok().and_then(|s| toml::from_str(&s).ok()).unwrap_or_default()
    }

    pub fn save_found(&self, f: &BTreeMap<String, Found>) -> Result<()> {
        std::fs::write(self.p("found.toml"), toml::to_string_pretty(f)?)?;
        Ok(())
    }
}
