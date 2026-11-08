use crate::{BaseDef, OffsetsFile, PatternKind};
use rsmem::{pe, rip_target, MemRead, ModuleInfo, Pattern};
use std::collections::BTreeMap;

/// Executable sections of modules, read once per attach.
#[derive(Default)]
pub struct CodeCache {
    code: BTreeMap<String, Vec<(u64, Vec<u8>)>>,
}

impl CodeCache {
    pub fn code<M: MemRead>(&mut self, mem: &M, module: &ModuleInfo) -> &[(u64, Vec<u8>)] {
        self.code.entry(module.name.to_ascii_lowercase()).or_insert_with(|| match pe::read_headers(mem, module.base) {
            Ok(h) => pe::read_code(mem, module.base, &h),
            Err(_) => Vec::new(),
        })
    }
}

/// Resolved static anchors (before per-sample chains).
#[derive(Debug, Default, Clone)]
pub struct Anchors {
    pub map: BTreeMap<String, u64>,
    /// Non-fatal notes (e.g. "pattern failed, used module_offset").
    pub notes: Vec<String>,
    /// Bases that could not be resolved.
    pub failures: Vec<String>,
}

fn find_module<'a>(modules: &'a [ModuleInfo], main: &'a ModuleInfo, name: Option<&str>) -> Option<&'a ModuleInfo> {
    match name {
        None => Some(main),
        Some(n) => modules.iter().find(|m| m.name.eq_ignore_ascii_case(n)),
    }
}

fn resolve_pattern<M: MemRead>(def: &BaseDef, mem: &M, module: &ModuleInfo, cache: &mut CodeCache) -> Result<u64, String> {
    let pat = Pattern::parse(def.pattern.as_deref().unwrap_or_default())?;
    let mut hits: Vec<u64> = Vec::new();
    let mut hit_bytes: Option<(u64, Vec<u8>)> = None;
    for (start, bytes) in cache.code(mem, module) {
        for off in pat.find_limit(bytes, 2) {
            hits.push(start + off as u64);
            if hit_bytes.is_none() {
                let end = (off + 64).min(bytes.len());
                hit_bytes = Some((start + off as u64, bytes[off..end].to_vec()));
            }
        }
        if hits.len() > 1 {
            break;
        }
    }
    match hits.len() {
        0 => return Err("pattern not found".into()),
        1 => {}
        _ => return Err("pattern is not unique".into()),
    }
    let (addr, bytes) = hit_bytes.expect("hit recorded");
    let target = match def.pattern_kind {
        PatternKind::Match => addr,
        PatternKind::Rip => {
            let d = def.disp_offset.ok_or("missing disp_offset")? as usize;
            let len = def.instr_len.ok_or("missing instr_len")? as u64;
            let disp = bytes.get(d..d + 4).ok_or("displacement outside pattern")?;
            let disp = i32::from_le_bytes([disp[0], disp[1], disp[2], disp[3]]);
            rip_target(addr, len, disp)
        }
    };
    Ok(target)
}

/// Resolve every base's static anchor. Signatures win over `module_offset`;
/// `module_offset` is the fallback when a signature fails.
pub fn resolve_anchors<M: MemRead>(
    cfg: &OffsetsFile,
    mem: &M,
    modules: &[ModuleInfo],
    main: &ModuleInfo,
    cache: &mut CodeCache,
) -> Anchors {
    let mut out = Anchors::default();
    for (name, def) in &cfg.bases {
        let Some(module) = find_module(modules, main, def.module.as_deref()) else {
            out.failures.push(format!("base {name}: module {:?} not loaded", def.module));
            continue;
        };
        let mut anchor = None;
        if def.pattern.is_some() {
            match resolve_pattern(def, mem, module, cache) {
                Ok(a) => anchor = Some(a),
                Err(e) => {
                    if def.module_offset.is_some() {
                        out.notes.push(format!("base {name}: {e}; falling back to module_offset"));
                    } else {
                        out.failures.push(format!("base {name}: {e}"));
                    }
                }
            }
        }
        if anchor.is_none() {
            if let Some(off) = def.module_offset {
                anchor = Some(module.base + off);
            }
        }
        if let Some(a) = anchor {
            out.map.insert(name.clone(), a.wrapping_add(def.add as u64));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    /// Build a fake module: PE header page + .text page with `mov rax,[rip+X]`.
    fn fake_module() -> (FakeMem, ModuleInfo) {
        let base = 0x14000_0000u64;
        let mut img = vec![0u8; 0x3000];
        img[0..2].copy_from_slice(b"MZ");
        img[0x3C..0x40].copy_from_slice(&0x80u32.to_le_bytes());
        img[0x80..0x84].copy_from_slice(b"PE\0\0");
        let fh = 0x84;
        img[fh + 2..fh + 4].copy_from_slice(&1u16.to_le_bytes());
        img[fh + 16..fh + 18].copy_from_slice(&0xF0u16.to_le_bytes());
        let opt = fh + 20;
        img[opt + 56..opt + 60].copy_from_slice(&0x3000u32.to_le_bytes());
        let st = opt + 0xF0;
        img[st..st + 5].copy_from_slice(b".text");
        img[st + 8..st + 12].copy_from_slice(&0x1000u32.to_le_bytes());
        img[st + 12..st + 16].copy_from_slice(&0x1000u32.to_le_bytes());
        img[st + 36..st + 40].copy_from_slice(&0x6000_0020u32.to_le_bytes());
        // At .text+0x10: 48 8B 05 <disp> 48 85 C0  ; mov rax,[rip+disp]; test rax,rax
        let ins = 0x1010usize;
        let global = 0x2100u64; // RVA of the global
        let disp = (global as i64 - (ins as i64 + 7)) as i32;
        img[ins..ins + 3].copy_from_slice(&[0x48, 0x8B, 0x05]);
        img[ins + 3..ins + 7].copy_from_slice(&disp.to_le_bytes());
        img[ins + 7..ins + 10].copy_from_slice(&[0x48, 0x85, 0xC0]);
        let mut m = FakeMem::new();
        m.add(base, img);
        (m, ModuleInfo { name: "game.exe".into(), base, size: 0x3000, path: String::new() })
    }

    #[test]
    fn resolves_rip_pattern_and_fallback() {
        let (mem, module) = fake_module();
        let cfg = OffsetsFile::parse(
            r#"
[bases.client]
pattern = "48 8B 05 ?? ?? ?? ?? 48 85 C0"
disp_offset = 3
instr_len = 7
add = 8

[bases.fallback]
pattern = "DE AD BE EF"
pattern_kind = "match"
module_offset = 0x2200

[bases.broken]
pattern = "DE AD BE EF"
pattern_kind = "match"
"#,
        )
        .unwrap();
        let mut cache = CodeCache::default();
        let a = resolve_anchors(&cfg, &mem, std::slice::from_ref(&module), &module, &mut cache);
        assert_eq!(a.map["client"], module.base + 0x2100 + 8);
        assert_eq!(a.map["fallback"], module.base + 0x2200);
        assert!(!a.map.contains_key("broken"));
        assert_eq!(a.failures.len(), 1);
        assert_eq!(a.notes.len(), 1);
    }
}
