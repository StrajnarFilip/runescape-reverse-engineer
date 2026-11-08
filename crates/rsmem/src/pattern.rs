use std::fmt;

/// An IDA-style byte pattern, e.g. `"48 8B 05 ?? ?? ?? ?? 48 85 C0"`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pattern {
    bytes: Vec<Option<u8>>,
}

impl Pattern {
    pub fn parse(s: &str) -> Result<Self, String> {
        let mut bytes = Vec::new();
        for tok in s.split_whitespace() {
            if tok == "?" || tok == "??" {
                bytes.push(None);
            } else {
                let b = u8::from_str_radix(tok, 16).map_err(|_| format!("bad pattern byte {tok:?}"))?;
                bytes.push(Some(b));
            }
        }
        if bytes.is_empty() {
            return Err("empty pattern".into());
        }
        if bytes.iter().all(|b| b.is_none()) {
            return Err("pattern is all wildcards".into());
        }
        Ok(Self { bytes })
    }

    /// Build from concrete bytes and a wildcard mask (true = wildcard).
    pub fn from_masked(bytes: &[u8], wild: &[bool]) -> Self {
        assert_eq!(bytes.len(), wild.len());
        Self {
            bytes: bytes.iter().zip(wild).map(|(b, w)| if *w { None } else { Some(*b) }).collect(),
        }
    }

    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty()
    }

    pub fn matches_at(&self, hay: &[u8], i: usize) -> bool {
        if i + self.bytes.len() > hay.len() {
            return false;
        }
        self.bytes.iter().enumerate().all(|(k, b)| b.map_or(true, |b| hay[i + k] == b))
    }

    /// All match offsets, stopping after `limit` matches.
    pub fn find_limit(&self, hay: &[u8], limit: usize) -> Vec<usize> {
        let mut out = Vec::new();
        if hay.len() < self.bytes.len() || limit == 0 {
            return out;
        }
        // Anchor on the first concrete byte to skip quickly.
        let (anchor_idx, anchor) = self
            .bytes
            .iter()
            .enumerate()
            .find_map(|(i, b)| b.map(|b| (i, b)))
            .expect("pattern has a concrete byte");
        let last_start = hay.len() - self.bytes.len();
        let mut i = 0usize;
        while i <= last_start {
            match hay[i + anchor_idx..=last_start + anchor_idx].iter().position(|&b| b == anchor) {
                None => break,
                Some(p) => {
                    let start = i + p;
                    if self.matches_at(hay, start) {
                        out.push(start);
                        if out.len() >= limit {
                            break;
                        }
                    }
                    i = start + 1;
                }
            }
        }
        out
    }

    pub fn find_all(&self, hay: &[u8]) -> Vec<usize> {
        self.find_limit(hay, usize::MAX)
    }

    pub fn find_first(&self, hay: &[u8]) -> Option<usize> {
        self.find_limit(hay, 1).into_iter().next()
    }
}

impl fmt::Display for Pattern {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let parts: Vec<String> = self
            .bytes
            .iter()
            .map(|b| b.map_or_else(|| "??".to_string(), |b| format!("{b:02X}")))
            .collect();
        f.write_str(&parts.join(" "))
    }
}

/// Target of a RIP-relative operand: next-instruction address plus displacement.
pub fn rip_target(instr_addr: u64, instr_len: u64, disp: i32) -> u64 {
    (instr_addr as i64).wrapping_add(instr_len as i64).wrapping_add(disp as i64) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_and_display() {
        let p = Pattern::parse("48 8b 05 ?? ?? ? ?? 90").unwrap();
        assert_eq!(p.to_string(), "48 8B 05 ?? ?? ?? ?? 90");
        assert!(Pattern::parse("?? ??").is_err());
        assert!(Pattern::parse("zz").is_err());
    }

    #[test]
    fn finds_matches() {
        let hay = [0x90, 0x48, 0x8B, 0x05, 1, 2, 3, 4, 0x90, 0x48, 0x8B, 0x05, 9, 9, 9, 9, 0xC3];
        let p = Pattern::parse("48 8B 05 ?? ?? ?? ??").unwrap();
        assert_eq!(p.find_all(&hay), vec![1, 9]);
        let p2 = Pattern::parse("48 8B 05 ?? ?? ?? ?? C3").unwrap();
        assert_eq!(p2.find_all(&hay), vec![9]);
        let p3 = Pattern::parse("?? 8B 05").unwrap();
        assert_eq!(p3.find_all(&hay), vec![1, 9]);
        assert_eq!(p.find_limit(&hay, 1), vec![1]);
    }

    #[test]
    fn rip() {
        // mov rax,[rip+0x10] at 0x1000, len 7 -> 0x1017
        assert_eq!(rip_target(0x1000, 7, 0x10), 0x1017);
        assert_eq!(rip_target(0x1000, 7, -0x7), 0x1000);
    }
}
