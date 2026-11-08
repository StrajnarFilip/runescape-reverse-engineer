# RS3 event reader

This reads RuneScape 3 client state straight from `rs2client.exe` memory and streams it as typed events. There's no OCR or screen capture, and nothing is injected into the client. A separate process calls `ReadProcessMemory` about 1000 times a second. It compares each sample with the previous one, then emits:

- `player.lifepoints` / `player.prayer` / `player.adrenaline` with the old and new values (and max / pct)
- `tick`, `player.ability_used`, `player.gcd` (start tick, end tick, wall-clock end), `player.gcd_ready`
- `npc.spawn` / `npc.despawn` / `npc.health` / `npc.animation` / `npc.spotanim`
- `boss.action` / `boss.hp` / `boss.phase` for NPCs listed in `data/bosses/*.toml` (Telos first)

> Reading the live client isn't approved by Jagex, so it puts your account at risk. This project only observes. It never writes to game memory, sends input or tries to hide from detection.

## Layout

| Path | What |
|---|---|
| `crates/rs3events` | **The library.** `Reader::start(..)`, `subscribe()`, `snapshots()` |
| `crates/rs3-eventd` | WebSocket server (`ws://127.0.0.1:7878/events`) + JSONL recorder |
| `crates/rsscan` | Discovery toolkit (value scan, watch, tickfind, ptrscan, siggen, structdiff) |
| `crates/rsmem` | Read-only process memory primitives, PE parsing, byte patterns |
| `crates/rsoffsets` | `offsets.toml` / boss schema and signature resolution |
| `crates/rsmodel` | Event / snapshot / protocol types (serde) |
| `crates/mocktarget` | Fake game process with a known layout, used for end-to-end tests |
| `clients/viewer.html` | Zero-dependency live viewer (open the file in a browser) |
| `offsets.toml` | Where game state lives in memory, loaded at runtime |

## Build & test

```bash
cargo build --release
```

```bash
cargo test --workspace
```

The end-to-end test (`crates/mocktarget/tests/e2e.rs`) runs everything against the mock. It finds the mock's layout with rsscan, resolves it through a generated signature, streams it over the WebSocket, and checks that no change is lost. It also checks the GCD tick math and the boss events, and measures latency from write to WebSocket receipt (about 1 ms p50/p99 at the default 1 ms period).

## Run

Double-click **`run.bat`** in the repo root, or run it from a terminal. It builds the server if needed, starts it and opens the viewer. One port serves both:

| URL | What |
|---|---|
| http://127.0.0.1:7878/ | Live viewer: player gauges, GCD bar, boss panel, event log |
| ws://127.0.0.1:7878/events | WebSocket with the same data as JSON (protocol below) |

The server attaches to the RuneScape client on its own, including after you restart the game, and finds the values in memory within 20–60 seconds. Until then the page shows what it's waiting for. Press Ctrl+C in the window to stop it. Running `run.bat` again while the server is up just reopens the page.

Extra arguments go to the server, e.g. `run.bat --record session.jsonl`. Set `RS3_NO_OPEN=1` to skip opening the browser. You can also run it directly:

```bash
cargo run --release -p rs3-eventd -- --open
```

Any WebSocket client can connect:

```text
-> {"type":"hello","protocol":1,"sources":[20392]}
-> {"type":"snapshot","snapshot":{...}}
-> {"seq":812,"unix_us":...,"tick":4411,"tick_phase_ms":2.1,"src":20392,"type":"player.adrenaline","old":40,"new":48,"pct":48.0}
<- {"type":"subscribe","topics":["player.*","boss.telos.*","tick"]}
<- {"type":"source","src":20392}
```

Flags: `--open`, `--record <file.jsonl>`, `--pid`, `--all` (one source per client), `--period-us 500`, `--offsets`, `--bosses`, `--bind`. Relative `--offsets`/`--bosses` paths are also searched next to the executable, so the server finds the repo's files from any working directory.

## What's mapped (client build 6ada3f59-1a86000)

| Signal | Where | Encoding |
|---|---|---|
| Prayer | varp **3274** in the varp table | bits 0–14 = points ×10, bit 15 = flag, bits 16–23 = prayer level (max = level ×10) |
| Adrenaline | varp **679** | tenths of a percent (360 = 36.0%) |
| Server tick | `+0x180` in objects with vtable `rs2client.exe+0x15b3a88` | u32, +1 every 600 ms, counts from login |
| Lifepoints | var **13537** through the var index | plain integer |
| Lifepoints max (candidate) | var **13538** through the var index | reads 9900 = orb max; not yet confirmed to follow gear changes |

Every signal is found by **locators** at attach, so no pointer paths are needed. Locators scan memory for a container's *structure*, then read values by key. That takes 15–60 seconds after `rs3-eventd` starts or a client appears; the status is `waiting` until it finishes:

- `sorted_table`: the varp table is 2048 entries of 0x30 bytes, `{i32 id @+0, value @+8}`, sorted by id. It was ascending in one session and **descending after a restart**; both are handled. An 11k-entry definition table also matches; `max_entries` and an "id+1 values" check reject it.
- `var_index`: the client's chained hash table over **all** vars. It has 8783 slots; slot `id % 8783` points at the first entry of its chain. Every entry, whether a varp table row or a separately allocated var node, is a 0x30-byte record `{i32 id @0, value @8, next @0x28}`. Lookup walks the chain. To find the index, every pointer into the varp table "votes" for `slot address − (id % 8783) × 8`. The most-voted start that resolves the probe vars wins. Because the client keeps this index current when it reallocates nodes, reads through it are never stale.
- `vtable_object`: objects whose first qword is a given vtable. When many share it, `pick_by_mode_at` picks the copies that agree, and the reader fails over between them when one is freed.
- `hash_nodes` (no longer used): scanning for loose var nodes. Unreliable for lifepoints, because id 13537 also appears in about 20 unrelated id-keyed tables and the real node gets reallocated.

**How lifepoints was pinned down:**

1. A damage test showed that only 2 of the 21 nodes with key 13537 followed the orb (9900 → 7771 → 4709).
2. Their only referrers were slot 4754 of two 8783-slot arrays, and 13537 % 8783 = 4754.
3. After a client restart, varp 3274 was hidden behind var 12057 in the same bucket (12057 % 8783 = 3274). 12057's entry linked to it at `+0x28`, which revealed the chaining.

**Restart test (2026-11-08, same build):**

- `rs3-eventd` in auto-attach mode noticed the old client exit and attached to the new one by itself.
- Varps and tick re-located with no config changes. Values matched the screen (prayer 975/990, adrenaline 36%), and the tick counter restarted from login.
- For comparison, pointer paths saved before the restart mostly broke: **4 of 496** tick paths and **0 of 45** lifepoints paths survived. The four survivors start at `rs2client.exe+0x1963d08`, `+0x1935480` and `+0x1935108`, and could make attach faster.
- Lifepoints through the var index, checked against the orb on each run:
  - run 1 (fresh client): 3897 ✓
  - run 2 (server restart): 3704 ✓
  - run 3 (client restart, index at a new address): 3365 ✓
  - Regeneration streamed as events on tick boundaries (+9 every 10 ticks).

Still to map: GCD and ability cooldowns (needs a combat session), NPCs and Telos.

Not verified yet: the next weekly client update (the build id will change; the vtable offset likely moves).

## Discovery tools added during the live session

- `rsscan live --nodes --tag 7 --label 13537=lifepoints`: a live web page of every var-table node (http://127.0.0.1:7879), for matching values by eye.
- `rsscan first i32 unknown`, then `next changed` / `next unchanged` while you toggle something in game: finds a value without knowing its encoding.
- `rsscan fieldmatch --was 674 --now 662 --max 990`: finds the candidate whose bit-field, ×10, `max − v` or float form went from 674 to 662. This is how the packed prayer varp was found.
- `rsscan table <entry addr> [--keys 679,3274]`: walks a sorted `{id, value}` table.
- `rsscan refs <addr>`: lists who points at an address.
- `rsscan watch --nodes [--loose] --min-changes 3`: ranks which var nodes change while you do something.

Lessons learned:

- A prayer that drains slowly makes repeated `next dec` passes drop the real address. Wait longer between passes, or use changed/unchanged rounds.
- RS3 packs values into varps, so plain value scans miss them. Use `fieldmatch`.

## Discovery playbook (fills `offsets.toml`)

All commands are single steps. State is kept in `.rsscan/`. Use `--pid` when more than one client is open (`rsscan clients` lists them).

**Lifepoints / prayer (current and max)**: read the exact number off the orb.

```bash
rsscan --pid 20392 attach
```

```bash
rsscan first i32 9900
```

Take damage (or let prayer drain), then repeat with the new value until a few candidates remain:

```bash
rsscan next 9640
```

```bash
rsscan list
```

```bash
rsscan save lifepoints 0x1f2e4a10 --type i32
```

**Adrenaline**: the scale is unknown (maybe 0–100 or 0–1000), so scan both at once:

```bash
rsscan first i32 eq 100,1000
```

```bash
rsscan next eq 92,920
```

**Var array**: if several values sit in one i32 array indexed by var id, give their addresses and ids (from the RS Wiki Varplayer list) to get the array base:

```bash
rsscan varp --pair 0x1f2e4a10:1000 --pair 0x1f2e4a18:1002
```

**Stable path to a dynamic address**: run a pointer scan, restart the client, find the value again, then verify:

```bash
rsscan ptrscan 0x1f2e4000
```

```bash
rsscan ptrverify --expect-value i32:9900
```

`ptrverify` prints a `[bases.*]` snippet. Turn its static anchor into an update-resilient signature:

```bash
rsscan siggen rs2client.exe+0x1a2b3c0 --name varps
```

**Tick counter**: stay logged in, then:

```bash
rsscan tickfind
```

**GCD**: narrow to a few hundred candidates (for example `first u32 unknown`, use abilities, `next changed`). Then watch them while pressing your ability keys; each press counts as a mark:

```bash
rsscan watch --mark-keys 1,2,3,4 --secs 30 --keep-top 20
```

The ranking shows which values change right after each ability use.

**NPCs and Telos**: scan for Telos's NPC id while in the fight, then lay out the struct while he attacks. Press F8 each time he starts an attack:

```bash
rsscan first i32 <telos npc id>
```

```bash
rsscan structdiff 0x2a4b0000 --len 1024 --secs 30
```

Fields that change on every mark are animation or spotanim. A field that only goes down as you deal damage is HP. Then `ptrscan` to the NPC list root.

**Check offsets.toml against the live client**:

```bash
rsscan resolve
```

**Label Telos's actions**: record a kill with `--record`, then map each `npc.animation` id to what he visibly did, in `data/bosses/telos.toml`.

## Patch day

When a weekly update moves things, `rs3-eventd` reports `offsets_stale` and emits no game events, so it never streams garbage. Run `rsscan resolve` to see which bases and signals broke. Signature-based bases usually survive. Re-find only the broken fields with the steps above.
