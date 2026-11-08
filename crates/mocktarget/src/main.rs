//! A stand-in "game client" with a fixed, known memory layout.
//!
//! Layout (all heap except the `CLIENT` global):
//! ```text
//! CLIENT (static, in .data/.bss) -> Client
//!   Client+0x40 -> varps: [i32; 4096]
//!                    1000 lifepoints, 1001 lifepoints max, 1002 prayer, 1003 prayer max,
//!                    1004 adrenaline (0..1000), 1005 last ability tick
//!   Client+0x48 -> npc table: [*mut Npc; 16]
//!   Client+0x58    server tick (u32)
//! Npc: +0x10 id, +0x14 x, +0x18 y, +0x1C plane, +0x20 anim, +0x24 spotanim, +0x28 hp, +0x2C hp max
//! ```
//! Every write is logged to stdout as one JSON line `{"t":<unix_us>,"k":"<what>","v":<value>}`
//! so tests can measure end-to-end latency.
//!
//! Usage: `mocktarget [--tick-ms 600] [--ticks 0 (forever)]`

use std::io::Write;
use std::ptr::{null_mut, write_volatile};
use std::sync::atomic::{AtomicPtr, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const VARPS: usize = 4096;
pub const NPC_CAP: usize = 16;
pub const V_LP: usize = 1000;
pub const V_LP_MAX: usize = 1001;
pub const V_PRAYER: usize = 1002;
pub const V_PRAYER_MAX: usize = 1003;
pub const V_ADREN: usize = 1004;
pub const V_LAST_ABILITY: usize = 1005;
pub const BOSS_ID: i32 = 9001;
pub const OTHER_ID: i32 = 1234;
pub const BOSS_HP: i32 = 50_000;

#[allow(dead_code)]
#[repr(C)]
struct Npc {
    hdr: [u64; 2],
    id: i32,
    x: i32,
    y: i32,
    plane: i32,
    anim: i32,
    spotanim: i32,
    hp: i32,
    hp_max: i32,
    pad: [u8; 0x30],
}

#[allow(dead_code)]
#[repr(C)]
struct Client {
    magic: u64,
    pad: [u64; 7],
    varps: *mut i32,
    npcs: *mut *mut Npc,
    npc_cap: u32,
    pad2: u32,
    server_tick: u32,
    pad3: u32,
}

static CLIENT: AtomicPtr<Client> = AtomicPtr::new(null_mut());

#[inline(never)]
fn client() -> *mut Client {
    CLIENT.load(Ordering::Acquire)
}

fn unix_us() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_micros() as u64
}

struct Log(std::io::Stdout);

impl Log {
    fn w(&mut self, k: &str, v: i64) {
        let t = unix_us();
        let mut o = self.0.lock();
        let _ = writeln!(o, r#"{{"t":{t},"k":"{k}","v":{v}}}"#);
        let _ = o.flush();
    }
}

unsafe fn set_var(c: *mut Client, id: usize, v: i32, log: &mut Log, k: &str) {
    write_volatile((*c).varps.add(id), v);
    log.w(k, v as i64);
}

unsafe fn get_var(c: *mut Client, id: usize) -> i32 {
    std::ptr::read_volatile((*c).varps.add(id))
}

fn new_npc(id: i32, hp: i32) -> *mut Npc {
    Box::into_raw(Box::new(Npc {
        hdr: [0x4E50_4300, 0],
        id,
        x: 3200,
        y: 3200,
        plane: 0,
        anim: -1,
        spotanim: -1,
        hp,
        hp_max: hp,
        pad: [0; 0x30],
    }))
}

fn main() {
    let mut tick_ms = 600u64;
    let mut ticks = 0u64;
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--tick-ms" => {
                tick_ms = args[i + 1].parse().expect("--tick-ms N");
                i += 1;
            }
            "--ticks" => {
                ticks = args[i + 1].parse().expect("--ticks N");
                i += 1;
            }
            _ => {}
        }
        i += 1;
    }

    let varps: &'static mut [i32] = Box::leak(vec![0i32; VARPS].into_boxed_slice());
    let table: &'static mut [*mut Npc] = Box::leak(vec![null_mut::<Npc>(); NPC_CAP].into_boxed_slice());
    let c = Box::into_raw(Box::new(Client {
        magic: 0x4D4F_434B_434C_4E54,
        pad: [0; 7],
        varps: varps.as_mut_ptr(),
        npcs: table.as_mut_ptr(),
        npc_cap: NPC_CAP as u32,
        pad2: 0,
        server_tick: 1000,
        pad3: 0,
    }));
    CLIENT.store(c, Ordering::Release);

    let base = unsafe { windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(std::ptr::null()) } as u64;
    let global = &CLIENT as *const _ as u64;
    let mut log = Log(std::io::stdout());
    {
        let c = client();
        unsafe {
            println!(
                r#"{{"ready":true,"pid":{},"module_base":{},"client_global_rva":{},"client_ptr":{},"varps_ptr":{},"npc_table_ptr":{}}}"#,
                std::process::id(),
                base,
                global - base,
                c as u64,
                (*c).varps as u64,
                (*c).npcs as u64
            );
            let _ = std::io::stdout().flush();
            set_var(c, V_LP, 9900, &mut log, "lp");
            set_var(c, V_LP_MAX, 9900, &mut log, "lp_max");
            set_var(c, V_PRAYER, 990, &mut log, "prayer");
            set_var(c, V_PRAYER_MAX, 990, &mut log, "prayer_max");
            set_var(c, V_ADREN, 0, &mut log, "adren");
            set_var(c, V_LAST_ABILITY, 0, &mut log, "ability");
            write_volatile((*c).npcs.add(7), new_npc(OTHER_ID, 1000));
            write_volatile((*c).npcs.add(3), new_npc(BOSS_ID, BOSS_HP));
            log.w("spawn", BOSS_ID as i64);
        }
    }

    let start = Instant::now();
    let mut n: u64 = 0;
    let mut boss_dead_until: Option<u64> = None;
    loop {
        n += 1;
        let target = start + Duration::from_millis(n * tick_ms);
        let now = Instant::now();
        if target > now {
            std::thread::sleep(target - now);
        }
        let c = client();
        unsafe {
            let tick = (*c).server_tick + 1;
            write_volatile(&mut (*c).server_tick, tick);
            log.w("tick", tick as i64);
            let t = tick as i64;

            if t % 2 == 0 {
                let mut lp = get_var(c, V_LP) - ((t * 37) % 300) as i32 - 1;
                if lp < 2000 {
                    lp = 9900;
                }
                set_var(c, V_LP, lp, &mut log, "lp");
            }
            if t % 3 == 0 {
                let mut p = get_var(c, V_PRAYER) - 10;
                if p < 0 {
                    p = 990;
                }
                set_var(c, V_PRAYER, p, &mut log, "prayer");
            }
            if t % 4 == 1 {
                set_var(c, V_LAST_ABILITY, tick as i32, &mut log, "ability");
                let a = (get_var(c, V_ADREN) + 80).min(1000);
                set_var(c, V_ADREN, a, &mut log, "adren");
            }

            let slot = (*c).npcs.add(3);
            let boss = std::ptr::read_volatile(slot);
            match (boss.is_null(), boss_dead_until) {
                (true, Some(until)) if n >= until => {
                    write_volatile(slot, new_npc(BOSS_ID, BOSS_HP));
                    boss_dead_until = None;
                    log.w("spawn", BOSS_ID as i64);
                }
                (false, _) => {
                    let anim = match t % 5 {
                        0 => 101,
                        2 => 202,
                        _ => -1,
                    };
                    if std::ptr::read_volatile(&(*boss).anim) != anim {
                        write_volatile(&mut (*boss).anim, anim);
                        log.w("anim", anim as i64);
                    }
                    let spot = if t % 10 == 2 { 303 } else { -1 };
                    if std::ptr::read_volatile(&(*boss).spotanim) != spot {
                        write_volatile(&mut (*boss).spotanim, spot);
                        log.w("spot", spot as i64);
                    }
                    if t % 2 == 1 {
                        let hp = std::ptr::read_volatile(&(*boss).hp) - 2_500;
                        if hp <= 0 {
                            write_volatile(slot, null_mut());
                            log.w("despawn", BOSS_ID as i64);
                            drop(Box::from_raw(boss));
                            boss_dead_until = Some(n + 4);
                        } else {
                            write_volatile(&mut (*boss).hp, hp);
                            log.w("hp", hp as i64);
                        }
                    }
                }
                _ => {}
            }
        }
        if ticks > 0 && n >= ticks {
            break;
        }
    }
}
