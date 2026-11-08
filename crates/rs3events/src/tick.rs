//! Game-tick tracking: from an in-memory counter when available, else a
//! phase-locked estimate from the arrival times of server-driven changes.

use rsmodel::TickBasis;
use std::time::{Duration, Instant};

pub const NOMINAL_TICK_MS: f64 = 600.0;

#[derive(Debug, Clone, PartialEq)]
pub struct TickUpdate {
    pub n: u64,
    pub basis: TickBasis,
    pub interval_ms: Option<f64>,
}

struct Counter {
    last: i64,
    last_at: Instant,
    interval_ema: Option<f64>,
}

#[derive(Default)]
struct Estimator {
    phase_ms: Option<f64>,
    last_obs: Option<Instant>,
    obs: u32,
    last_n: Option<i64>,
}

pub struct TickTracker {
    origin: Instant,
    counter: Option<Counter>,
    est: Estimator,
}

impl TickTracker {
    pub fn new(origin: Instant) -> Self {
        Self { origin, counter: None, est: Estimator::default() }
    }

    pub fn has_counter(&self) -> bool {
        self.counter.is_some()
    }

    pub fn reset(&mut self) {
        self.counter = None;
        self.est = Estimator::default();
    }

    /// Feed the in-memory tick counter. Returns an update when it changes.
    pub fn on_counter(&mut self, v: i64, now: Instant) -> Option<TickUpdate> {
        match &mut self.counter {
            None => {
                self.counter = Some(Counter { last: v, last_at: now, interval_ema: None });
                Some(TickUpdate { n: v.max(0) as u64, basis: TickBasis::Counter, interval_ms: None })
            }
            Some(c) => {
                if v == c.last {
                    return None;
                }
                let dt = now.duration_since(c.last_at).as_secs_f64() * 1000.0;
                let delta = v - c.last;
                let interval = if delta > 0 && delta < 50 {
                    let per = dt / delta as f64;
                    c.interval_ema = Some(match c.interval_ema {
                        None => per,
                        Some(e) => e * 0.9 + per * 0.1,
                    });
                    Some(per)
                } else {
                    None
                };
                c.last = v;
                c.last_at = now;
                Some(TickUpdate { n: v.max(0) as u64, basis: TickBasis::Counter, interval_ms: interval })
            }
        }
    }

    fn t_ms(&self, now: Instant) -> f64 {
        now.duration_since(self.origin).as_secs_f64() * 1000.0
    }

    /// A server-driven value changed at `now` (feeds the estimator; ignored with a counter).
    pub fn observe_server_change(&mut self, now: Instant) {
        if self.counter.is_some() {
            return;
        }
        if let Some(last) = self.est.last_obs {
            if now.duration_since(last) < Duration::from_millis(200) {
                return; // one observation per tick burst
            }
        }
        self.est.last_obs = Some(now);
        let t = self.t_ms(now);
        match self.est.phase_ms {
            None => self.est.phase_ms = Some(t.rem_euclid(NOMINAL_TICK_MS)),
            Some(p) => {
                let r = (t - p).rem_euclid(NOMINAL_TICK_MS);
                let resid = if r > NOMINAL_TICK_MS / 2.0 { r - NOMINAL_TICK_MS } else { r };
                if resid.abs() < 150.0 {
                    self.est.phase_ms = Some((p + 0.2 * resid).rem_euclid(NOMINAL_TICK_MS));
                }
            }
        }
        self.est.obs = self.est.obs.saturating_add(1);
    }

    fn est_index(&self, now: Instant) -> Option<(i64, f64)> {
        let p = self.est.phase_ms?;
        if self.est.obs < 2 {
            return None;
        }
        let x = self.t_ms(now) - p;
        let k = (x / NOMINAL_TICK_MS).floor() as i64;
        (k >= 0).then(|| (k, x - k as f64 * NOMINAL_TICK_MS))
    }

    /// With no counter: emit an update when the estimated tick index advances.
    pub fn poll_estimated(&mut self, now: Instant) -> Option<TickUpdate> {
        if self.counter.is_some() {
            return None;
        }
        let (k, _) = self.est_index(now)?;
        if self.est.last_n == Some(k) {
            return None;
        }
        self.est.last_n = Some(k);
        Some(TickUpdate { n: k as u64, basis: TickBasis::Estimated, interval_ms: Some(NOMINAL_TICK_MS) })
    }

    /// (tick number, ms since that tick started) for stamping events.
    pub fn stamp(&self, now: Instant) -> (Option<u64>, Option<f64>) {
        if let Some(c) = &self.counter {
            return (Some(c.last.max(0) as u64), Some(now.duration_since(c.last_at).as_secs_f64() * 1000.0));
        }
        match self.est_index(now) {
            Some((k, ph)) => (Some(k as u64), Some(ph)),
            None => (None, None),
        }
    }

    pub fn current(&self, now: Instant) -> Option<u64> {
        self.stamp(now).0
    }

    /// The counter's current tick (None without a counter).
    pub fn counter_tick(&self) -> Option<u64> {
        self.counter.as_ref().map(|c| c.last.max(0) as u64)
    }

    pub fn interval_ms(&self) -> f64 {
        self.counter.as_ref().and_then(|c| c.interval_ema).unwrap_or(NOMINAL_TICK_MS)
    }

    /// Best estimate of when tick `n` began.
    pub fn tick_start(&self, n: u64) -> Option<Instant> {
        let ms = |x: f64| Duration::from_secs_f64(x.max(0.0) / 1000.0);
        if let Some(c) = &self.counter {
            let back = (c.last - n as i64) as f64 * self.interval_ms();
            return if back >= 0.0 { c.last_at.checked_sub(ms(back)) } else { Some(c.last_at + ms(-back)) };
        }
        let p = self.est.phase_ms?;
        Some(self.origin + ms(n as f64 * NOMINAL_TICK_MS + p))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counter_updates() {
        let t0 = Instant::now();
        let mut t = TickTracker::new(t0);
        let u = t.on_counter(100, t0).unwrap();
        assert_eq!(u.n, 100);
        assert!(t.on_counter(100, t0 + Duration::from_millis(10)).is_none());
        let u = t.on_counter(101, t0 + Duration::from_millis(600)).unwrap();
        assert_eq!(u.interval_ms.map(|x| x.round()), Some(600.0));
        assert_eq!(t.counter_tick(), Some(101));
        let (n, ph) = t.stamp(t0 + Duration::from_millis(650));
        assert_eq!(n, Some(101));
        assert!((ph.unwrap() - 50.0).abs() < 1.0);
        let s = t.tick_start(104).unwrap();
        let d = s.duration_since(t0).as_millis();
        assert!((2399..=2401).contains(&d), "{d}");
    }

    #[test]
    fn estimator_locks_phase() {
        let t0 = Instant::now();
        let mut t = TickTracker::new(t0);
        // Server changes arrive at 37 ms + k*600 with small jitter.
        for k in 0..20u64 {
            let jitter = [0i64, 3, -2, 5, -4][k as usize % 5];
            t.observe_server_change(t0 + Duration::from_millis((37 + k * 600) as u64) + Duration::from_millis(jitter.max(0) as u64));
        }
        let p = t.est.phase_ms.unwrap();
        assert!((p - 37.0).abs() < 6.0, "phase {p}");
        let at = t0 + Duration::from_millis(37 + 600 * 30 + 10);
        let (n, ph) = t.stamp(at);
        assert_eq!(n, Some(30));
        assert!(ph.unwrap() < 20.0);
        assert!(t.poll_estimated(at).is_some());
        assert!(t.poll_estimated(at + Duration::from_millis(5)).is_none());
        assert!(t.poll_estimated(at + Duration::from_millis(600)).is_some());
    }
}
