use rsmodel::Num;
use serde::{Deserialize, Serialize};
use std::fmt;
use std::str::FromStr;

/// Primitive value types readable from memory (little-endian).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ValType {
    I8,
    U8,
    I16,
    U16,
    I32,
    U32,
    I64,
    U64,
    F32,
    F64,
}

impl ValType {
    pub fn size(self) -> usize {
        match self {
            ValType::I8 | ValType::U8 => 1,
            ValType::I16 | ValType::U16 => 2,
            ValType::I32 | ValType::U32 | ValType::F32 => 4,
            ValType::I64 | ValType::U64 | ValType::F64 => 8,
        }
    }

    pub fn is_float(self) -> bool {
        matches!(self, ValType::F32 | ValType::F64)
    }

    /// Zero-extended raw bits of the value in `b` (at least `size()` bytes).
    pub fn raw(self, b: &[u8]) -> u64 {
        let mut x = [0u8; 8];
        x[..self.size()].copy_from_slice(&b[..self.size()]);
        u64::from_le_bytes(x)
    }

    /// Interpret raw bits as a number.
    pub fn from_raw(self, raw: u64) -> Num {
        match self {
            ValType::I8 => Num::I(raw as u8 as i8 as i64),
            ValType::U8 => Num::I(raw as u8 as i64),
            ValType::I16 => Num::I(raw as u16 as i16 as i64),
            ValType::U16 => Num::I(raw as u16 as i64),
            ValType::I32 => Num::I(raw as u32 as i32 as i64),
            ValType::U32 => Num::I(raw as u32 as i64),
            ValType::I64 => Num::I(raw as i64),
            ValType::U64 => Num::I(raw as i64),
            ValType::F32 => Num::F(f32::from_bits(raw as u32) as f64),
            ValType::F64 => Num::F(f64::from_bits(raw)),
        }
    }

    pub fn decode(self, b: &[u8]) -> Num {
        self.from_raw(self.raw(b))
    }

    /// Decode, then extract an inclusive bit range from integer values.
    pub fn decode_bits(self, b: &[u8], bits: Option<(u8, u8)>) -> Num {
        let raw = self.raw(b);
        match bits {
            Some((lo, hi)) if !self.is_float() => {
                let width = (hi - lo + 1) as u32;
                let mask = if width >= 64 { u64::MAX } else { (1u64 << width) - 1 };
                Num::I(((raw >> lo) & mask) as i64)
            }
            _ => self.from_raw(raw),
        }
    }

    /// Encode a number to raw bits (used by scanners comparing against user input).
    pub fn to_raw(self, v: f64) -> u64 {
        match self {
            ValType::F32 => (v as f32).to_bits() as u64,
            ValType::F64 => v.to_bits(),
            ValType::I8 => (v as i64 as i8) as u8 as u64,
            ValType::I16 => (v as i64 as i16) as u16 as u64,
            ValType::I32 => (v as i64 as i32) as u32 as u64,
            _ => v as i64 as u64,
        }
    }
}

impl FromStr for ValType {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        Ok(match s.to_ascii_lowercase().as_str() {
            "i8" => ValType::I8,
            "u8" => ValType::U8,
            "i16" => ValType::I16,
            "u16" => ValType::U16,
            "i32" | "int" => ValType::I32,
            "u32" => ValType::U32,
            "i64" => ValType::I64,
            "u64" | "ptr" => ValType::U64,
            "f32" | "float" => ValType::F32,
            "f64" | "double" => ValType::F64,
            _ => return Err(format!("unknown type {s:?} (i8 u8 i16 u16 i32 u32 i64 u64 f32 f64)")),
        })
    }
}

impl fmt::Display for ValType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            ValType::I8 => "i8",
            ValType::U8 => "u8",
            ValType::I16 => "i16",
            ValType::U16 => "u16",
            ValType::I32 => "i32",
            ValType::U32 => "u32",
            ValType::I64 => "i64",
            ValType::U64 => "u64",
            ValType::F32 => "f32",
            ValType::F64 => "f64",
        };
        f.write_str(s)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_types() {
        assert_eq!(ValType::I32.decode(&(-5i32).to_le_bytes()), Num::I(-5));
        assert_eq!(ValType::U16.decode(&[0xFF, 0xFF]), Num::I(65535));
        assert_eq!(ValType::I16.decode(&[0xFF, 0xFF]), Num::I(-1));
        assert_eq!(ValType::F32.decode(&1.5f32.to_le_bytes()), Num::F(1.5));
        assert_eq!(ValType::I32.decode_bits(&0b1011_0000u32.to_le_bytes(), Some((4, 7))), Num::I(0b1011));
        assert_eq!(ValType::I32.from_raw(ValType::I32.to_raw(-7.0)), Num::I(-7));
        assert_eq!(ValType::F32.from_raw(ValType::F32.to_raw(2.25)), Num::F(2.25));
    }
}
