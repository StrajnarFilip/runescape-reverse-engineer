//! Address expressions: `0x7ff6...`, `rs2client.exe+0x1234`, `[rs2client.exe+0x10]+0x20`, `123` (decimal).

use anyhow::{anyhow, bail, Result};
use rsmem::{MemRead, ModuleInfo};

pub fn parse_num(s: &str) -> Result<i64> {
    let s = s.trim();
    let (neg, s) = match s.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, s),
    };
    let v = if let Some(h) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
        u64::from_str_radix(&h.replace('_', ""), 16).map_err(|_| anyhow!("bad hex number {s:?}"))? as i64
    } else {
        s.replace('_', "").parse::<i64>().map_err(|_| anyhow!("bad number {s:?} (use 0x for hex)"))?
    };
    Ok(if neg { -v } else { v })
}

struct P<'a, M> {
    s: &'a [u8],
    i: usize,
    mem: &'a M,
    modules: &'a [ModuleInfo],
}

impl<M: MemRead> P<'_, M> {
    fn ws(&mut self) {
        while self.i < self.s.len() && self.s[self.i].is_ascii_whitespace() {
            self.i += 1;
        }
    }

    fn expr(&mut self) -> Result<u64> {
        let mut v = self.term()?;
        loop {
            self.ws();
            match self.s.get(self.i) {
                Some(b'+') => {
                    self.i += 1;
                    v = v.wrapping_add(self.term()?);
                }
                Some(b'-') => {
                    self.i += 1;
                    v = v.wrapping_sub(self.term()?);
                }
                _ => return Ok(v),
            }
        }
    }

    fn term(&mut self) -> Result<u64> {
        self.ws();
        if self.s.get(self.i) == Some(&b'[') {
            self.i += 1;
            let inner = self.expr()?;
            self.ws();
            if self.s.get(self.i) != Some(&b']') {
                bail!("missing ]");
            }
            self.i += 1;
            return self.mem.read_ptr(inner).map_err(|e| anyhow!("deref failed: {e}"));
        }
        let start = self.i;
        while self.i < self.s.len() && !matches!(self.s[self.i], b'+' | b'-' | b'[' | b']') && !self.s[self.i].is_ascii_whitespace() {
            self.i += 1;
        }
        let tok = std::str::from_utf8(&self.s[start..self.i])?;
        if tok.is_empty() {
            bail!("expected a number or module name at position {start}");
        }
        if tok.contains('.') && !tok.starts_with("0x") {
            let m = self
                .modules
                .iter()
                .find(|m| m.name.eq_ignore_ascii_case(tok))
                .ok_or_else(|| anyhow!("module {tok:?} not loaded"))?;
            return Ok(m.base);
        }
        Ok(parse_num(tok)? as u64)
    }
}

pub fn parse_addr<M: MemRead>(s: &str, mem: &M, modules: &[ModuleInfo]) -> Result<u64> {
    let mut p = P { s: s.as_bytes(), i: 0, mem, modules };
    let v = p.expr()?;
    p.ws();
    if p.i != p.s.len() {
        bail!("trailing input in address {s:?}");
    }
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn expressions() {
        let mut m = FakeMem::new();
        m.add(0x1000, vec![0; 0x100]);
        m.write(0x1010u64, 0x5000u64);
        let mods = vec![ModuleInfo { name: "game.exe".into(), base: 0x1000, size: 0x100, path: String::new() }];
        assert_eq!(parse_addr("0x10", &m, &mods).unwrap(), 0x10);
        assert_eq!(parse_addr("16", &m, &mods).unwrap(), 16);
        assert_eq!(parse_addr("game.exe+0x10", &m, &mods).unwrap(), 0x1010);
        assert_eq!(parse_addr("[GAME.EXE + 0x10] + 0x20", &m, &mods).unwrap(), 0x5020);
        assert_eq!(parse_addr("0x100-0x10", &m, &mods).unwrap(), 0xF0);
        assert!(parse_addr("nope.dll+1", &m, &mods).is_err());
        assert!(parse_addr("[0x10", &m, &mods).is_err());
    }
}
