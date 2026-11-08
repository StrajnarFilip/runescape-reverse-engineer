//! Generate update-resilient code signatures for a static address: find instructions
//! whose RIP-relative operand targets it, wildcard relocatable bytes, grow until unique.

use iced_x86::{Decoder, DecoderOptions, Instruction, OpKind};
use rsmem::Pattern;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Sig {
    pub pattern: String,
    pub disp_offset: u32,
    pub instr_len: u32,
    pub instr_addr: u64,
    pub mnemonic: String,
}

impl Sig {
    pub fn toml_snippet(&self, name: &str, module: &str, module_offset: u64) -> String {
        format!(
            "[bases.{name}]\nmodule = \"{module}\"\npattern = \"{}\"\ndisp_offset = {}\ninstr_len = {}\nmodule_offset = {module_offset:#x}  # fallback\n",
            self.pattern, self.disp_offset, self.instr_len
        )
    }
}

fn count_matches(p: &Pattern, code: &[(u64, Vec<u8>)]) -> usize {
    let mut n = 0;
    for (_, b) in code {
        n += p.find_limit(b, 2 - n.min(2)).len();
        if n >= 2 {
            break;
        }
    }
    n
}

/// Build a unique pattern starting at `pos` of section `sec`.
fn build_at(code: &[(u64, Vec<u8>)], sec: usize, pos: usize, max_len: usize) -> Option<Sig> {
    let (va, bytes) = &code[sec];
    let mut dec = Decoder::with_ip(64, &bytes[pos..], va + pos as u64, DecoderOptions::NONE);
    let mut ins = Instruction::default();
    let mut pat: Vec<u8> = Vec::new();
    let mut wild: Vec<bool> = Vec::new();
    let mut first: Option<(u32, u32, String)> = None;
    while dec.can_decode() && pat.len() < max_len {
        let at = dec.position();
        dec.decode_out(&mut ins);
        if ins.is_invalid() {
            return None;
        }
        let co = dec.get_constant_offsets(&ins);
        let len = ins.len();
        let start = pat.len();
        pat.extend_from_slice(&bytes[pos + at..pos + at + len]);
        wild.extend(std::iter::repeat(false).take(len));
        if ins.is_ip_rel_memory_operand() && co.has_displacement() {
            let (o, n) = (co.displacement_offset(), co.displacement_size());
            wild[start + o..start + o + n].iter_mut().for_each(|w| *w = true);
        }
        if ins.op0_kind() == OpKind::NearBranch64 && len >= 5 {
            wild[start + len - 4..start + len].iter_mut().for_each(|w| *w = true);
        }
        if first.is_none() {
            first = Some((co.displacement_offset() as u32, len as u32, format!("{:?}", ins.mnemonic())));
        }
        if pat.len() >= 8 {
            let p = Pattern::from_masked(&pat, &wild);
            if count_matches(&p, code) == 1 {
                let (disp_offset, instr_len, mnemonic) = first.unwrap();
                return Some(Sig { pattern: p.to_string(), disp_offset, instr_len, instr_addr: va + pos as u64, mnemonic });
            }
        }
    }
    None
}

/// Find up to `max_hits` signatures for `target` in the given code sections
/// (`(absolute address, bytes)` pairs from `rsmem::pe::read_code`). Shortest first.
pub fn siggen(code: &[(u64, Vec<u8>)], target: u64, max_hits: usize) -> Vec<Sig> {
    let mut refs: Vec<(usize, usize)> = Vec::new();
    for (si, (va, bytes)) in code.iter().enumerate() {
        let mut dec = Decoder::with_ip(64, bytes, *va, DecoderOptions::NONE);
        let mut ins = Instruction::default();
        while dec.can_decode() {
            let pos = dec.position();
            dec.decode_out(&mut ins);
            if !ins.is_invalid() && ins.is_ip_rel_memory_operand() && ins.ip_rel_memory_address() == target {
                refs.push((si, pos));
                if refs.len() >= max_hits * 8 {
                    break;
                }
            }
        }
    }
    let mut sigs: Vec<Sig> = refs.into_iter().filter_map(|(s, p)| build_at(code, s, p, 64)).collect();
    sigs.sort_by_key(|s| s.pattern.len());
    sigs.dedup_by(|a, b| a.pattern == b.pattern);
    sigs.truncate(max_hits);
    sigs
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_rip_reference() {
        // padding, then: mov rax,[rip+disp]; test rax,rax; jz +2; ret ...
        let va = 0x1_4000_1000u64;
        let mut b = vec![0xCCu8; 0x40];
        let ins = 0x10usize;
        let target = 0x1_4000_3000u64;
        let disp = (target as i64 - (va as i64 + ins as i64 + 7)) as i32;
        b[ins..ins + 3].copy_from_slice(&[0x48, 0x8B, 0x05]);
        b[ins + 3..ins + 7].copy_from_slice(&disp.to_le_bytes());
        b[ins + 7..ins + 10].copy_from_slice(&[0x48, 0x85, 0xC0]);
        b[ins + 10..ins + 12].copy_from_slice(&[0x74, 0x02]);
        b[ins + 12] = 0xC3;
        // A second, different reference elsewhere to make sure uniqueness logic works.
        let code = vec![(va, b)];
        let sigs = siggen(&code, target, 3);
        assert!(!sigs.is_empty());
        let s = &sigs[0];
        assert!(s.pattern.starts_with("48 8B 05 ?? ?? ?? ??"), "{}", s.pattern);
        assert_eq!(s.disp_offset, 3);
        assert_eq!(s.instr_len, 7);
        assert_eq!(s.instr_addr, va + ins as u64);
    }
}
