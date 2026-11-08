use crate::Shared;
use crossbeam_channel::TrySendError;
use rsmodel::{Envelope, Event};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn unix_us() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_micros() as u64).unwrap_or(0)
}

/// Stamps and fans out events for one source.
pub(crate) struct Emitter {
    shared: Arc<Shared>,
    pub src: u32,
}

impl Emitter {
    pub fn new(shared: Arc<Shared>, src: u32) -> Self {
        Self { shared, src }
    }

    pub fn emit(&self, ev: Event, stamp: (Option<u64>, Option<f64>)) {
        let unix = unix_us();
        let t_us = self.shared.t0.elapsed().as_micros() as u64;
        // Assign seq under the lock so subscribers see seq in order.
        let mut subs = self.shared.subs.lock().unwrap();
        let env = Arc::new(Envelope {
            seq: self.shared.seq.fetch_add(1, Ordering::Relaxed),
            t_us,
            unix_us: unix,
            tick: stamp.0,
            tick_phase_ms: stamp.1,
            src: self.src,
            ev,
        });
        subs.retain(|s| match s.try_send(env.clone()) {
            Ok(()) => true,
            Err(TrySendError::Full(_)) => {
                tracing::warn!("event subscriber fell behind; disconnecting it");
                false
            }
            Err(TrySendError::Disconnected(_)) => false,
        });
    }
}
