use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Effect {
    Idle,
    Announcing(Instant),
    Fading(Instant),
}

impl Effect {
    pub fn advance(self, now: Instant, busy: bool) -> Self {
        match self {
            Self::Announcing(started)
                if !busy && now.duration_since(started) >= Duration::from_millis(650) =>
            {
                Self::Fading(now)
            }
            Self::Fading(started) if now.duration_since(started) >= Duration::from_secs(2) => {
                Self::Idle
            }
            other => other,
        }
    }

    pub fn style(self) -> u8 {
        match self {
            Self::Idle => 0,
            Self::Announcing(_) => 1,
            Self::Fading(_) => 2,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finale_waits_for_queued_speech_synthesis_and_playback() {
        let start = Instant::now();
        let effect = Effect::Announcing(start);
        let later = start + Duration::from_secs(5);
        assert_eq!(effect.advance(later, true), effect);
        let fading = effect.advance(later, false);
        assert_eq!(fading, Effect::Fading(later));
        assert_eq!(
            fading.advance(later + Duration::from_millis(1900), false),
            fading
        );
        assert_eq!(
            fading.advance(later + Duration::from_secs(2), false),
            Effect::Idle
        );
    }

    #[test]
    fn voice_disabled_still_gets_one_visible_flash() {
        let start = Instant::now();
        let effect = Effect::Announcing(start);
        assert_eq!(
            effect.advance(start + Duration::from_millis(150), false),
            effect
        );
        assert_eq!(
            effect
                .advance(start + Duration::from_millis(650), false)
                .style(),
            2
        );
    }
}
