//! Global cooldown derivation.

use crate::tick::TickTracker;
use rsmodel::Event;
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct Active {
    pub basis: String,
    pub end_tick: Option<u64>,
    pub ends_at: Instant,
    pub ends_at_unix: u64,
    /// Readiness comes from a direct signal, not from the timer.
    pub authoritative: bool,
}

pub struct GcdTracker {
    ticks: u32,
    pub active: Option<Active>,
}

impl GcdTracker {
    pub fn new(ticks: u32) -> Self {
        Self { ticks, active: None }
    }

    pub fn is_active(&self) -> bool {
        self.active.is_some()
    }

    fn make(&mut self, basis: String, start_tick: Option<u64>, end_tick: Option<u64>, ends_at: Instant, now: Instant, now_unix: u64, authoritative: bool) -> Event {
        let remaining = ends_at.saturating_duration_since(now);
        let ends_at_unix = now_unix + remaining.as_micros() as u64;
        let duration_ms = match (start_tick, end_tick) {
            (Some(s), Some(e)) => (e.saturating_sub(s)) as f64 * crate::tick::NOMINAL_TICK_MS,
            _ => remaining.as_secs_f64() * 1000.0,
        };
        self.active = Some(Active { basis: basis.clone(), end_tick, ends_at, ends_at_unix, authoritative });
        Event::Gcd { basis, start_tick, end_tick, duration_ms, ends_at_unix_us: ends_at_unix }
    }

    /// An ability was used now: GCD = `ticks` game ticks from the start of the current tick.
    pub fn start(&mut self, basis: String, now: Instant, now_unix: u64, tick: &TickTracker) -> Event {
        let cur = tick.current(now);
        let dur = Duration::from_secs_f64(self.ticks as f64 * tick.interval_ms() / 1000.0);
        let begin = cur.and_then(|n| tick.tick_start(n)).filter(|b| *b <= now).unwrap_or(now);
        let ends_at = (begin + dur).max(now);
        self.make(basis, cur, cur.map(|n| n + self.ticks as u64), ends_at, now, now_unix, false)
    }

    /// A direct signal says `remaining_ms` of GCD are left.
    pub fn start_remaining(&mut self, basis: String, remaining_ms: f64, now: Instant, now_unix: u64, tick: &TickTracker) -> Event {
        let cur = tick.current(now);
        let ends_at = now + Duration::from_secs_f64(remaining_ms.max(0.0) / 1000.0);
        let end_tick = cur.map(|n| n + (remaining_ms / tick.interval_ms()).round() as u64);
        self.make(basis, cur, end_tick, ends_at, now, now_unix, true)
    }

    /// A direct signal gives the absolute tick at which the GCD ends.
    pub fn start_end_tick(&mut self, basis: String, end_tick: u64, now: Instant, now_unix: u64, tick: &TickTracker) -> Event {
        let cur = tick.current(now);
        let ends_at = tick.tick_start(end_tick).unwrap_or(now).max(now);
        self.make(basis, cur, Some(end_tick), ends_at, now, now_unix, true)
    }

    /// Explicitly end (direct signal reached zero). Returns the basis if one was active.
    pub fn finish(&mut self) -> Option<String> {
        self.active.take().map(|a| a.basis)
    }

    /// Timer/counter-driven readiness for derived GCDs.
    pub fn poll(&mut self, now: Instant, tick: &TickTracker) -> Option<String> {
        let a = self.active.as_ref()?;
        let done = if a.authoritative {
            // Safety net: if the direct signal never resets, give up 2 s late.
            now >= a.ends_at + Duration::from_secs(2)
        } else {
            match (a.end_tick, tick.counter_tick()) {
                (Some(e), Some(c)) => c >= e,
                _ => now >= a.ends_at,
            }
        };
        if done {
            self.finish()
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derived_gcd_three_ticks() {
        let t0 = Instant::now();
        let mut tick = TickTracker::new(t0);
        tick.on_counter(10, t0);
        tick.on_counter(11, t0 + Duration::from_millis(600));
        let mut g = GcdTracker::new(3);
        let now = t0 + Duration::from_millis(605);
        let ev = g.start("signal:x".into(), now, 1_000_000, &tick);
        match ev {
            Event::Gcd { start_tick, end_tick, duration_ms, ends_at_unix_us, .. } => {
                assert_eq!(start_tick, Some(11));
                assert_eq!(end_tick, Some(14));
                assert_eq!(duration_ms, 1800.0);
                // tick 11 began at t0+600; ends at t0+2400; now is t0+605 -> ~1795 ms left
                let left = ends_at_unix_us - 1_000_000;
                assert!((1_790_000..=1_800_000).contains(&left), "{left}");
            }
            other => panic!("{other:?}"),
        }
        assert!(g.poll(now, &tick).is_none());
        tick.on_counter(13, t0 + Duration::from_millis(1800));
        assert!(g.poll(t0 + Duration::from_millis(1801), &tick).is_none());
        tick.on_counter(14, t0 + Duration::from_millis(2400));
        assert_eq!(g.poll(t0 + Duration::from_millis(2401), &tick).as_deref(), Some("signal:x"));
        assert!(!g.is_active());
    }

    #[test]
    fn authoritative_waits_for_signal() {
        let t0 = Instant::now();
        let tick = TickTracker::new(t0);
        let mut g = GcdTracker::new(3);
        g.start_remaining("signal:r".into(), 1800.0, t0, 0, &tick);
        assert!(g.poll(t0 + Duration::from_millis(1900), &tick).is_none());
        assert_eq!(g.finish().as_deref(), Some("signal:r"));
    }
}
