#pragma once
//
// Sub-LSB brightness synthesis for the AIR-1's ambience effects.
//
// THE PROBLEM. The effects modulate a float, but a WS2812 channel is 8 bits and
// ESPHome's gamma stage spends most of that range at the top. At the brightness
// this unit is actually comfortable at, the LED runs at PWM 11/255, and the
// whole breathing swing covers 7..11 -- FIVE distinct levels for a seven-second
// curve. That is the "choppy": not the waveform, the output resolution.
//
// Lowering the gamma does not help, and it is worth writing down why, because it
// is the obvious first idea. Perceived brightness is a function of PWM alone, so
// "comfortable" IS PWM 11 whatever number sits next to it on the slider. Gamma
// only relabels the control.
//
// WHAT ACTUALLY WORKS. The strip has three LEDs and the eye sums them, so the
// cluster can sit between two PWM values by putting some LEDs on one and the
// rest on the next (spatial), and it can sit between two CLUSTER values by
// alternating across frames faster than the eye integrates (temporal). Together
// they turn a 13-step staircase into a continuum.
//
// WHY THIS IS DONE IN OUTPUT SPACE. An earlier version dithered the buffer
// value, which is simpler and needs no probing. It cannot work here: the
// buffer -> PWM map is a staircase with ~3-wide treads at this brightness, so
// both dithers spend most of their time moving within one tread and changing
// nothing at all. To synthesise a level you have to straddle a real PWM step,
// which means knowing where the steps are.
//
// WHY IT PROBES INSTEAD OF CALCULATING. ESPColorView applies the correction on
// assignment and get_*_raw() hands back the byte that will reach the LED, so the
// step positions can be MEASURED. The alternative is reproducing ESPHome's gamma
// exponent, its 16-bit table and its rounding in this file, and being silently
// wrong the day someone sets gamma_correct. Probing keeps these effects as
// ignorant of the output chain as they already are of the band table and the
// day/night ramp, which is the property worth protecting.

#include <algorithm>
#include "esphome/components/light/addressable_light.h"
#include "esphome/core/color.h"
#include "esphome/core/hal.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

namespace ambience {

/// Hold the main loop at full speed while an ambience effect is running.
///
/// ESPHome gates the component phase on a 16ms loop interval by default, for
/// power. That is a hard ceiling of ~62 frames/sec on any effect, and it was
/// measured, not assumed: the first build of the temporal dither asked for 5ms
/// frames and logged "62 fps" for half an hour.
///
/// 62fps is fine for the waveforms -- they are seconds long -- but not for the
/// temporal half of the dithering, which spends frames as its currency. At
/// 62fps the alternation between two output levels lands at up to 31Hz and its
/// residual components reach down into single digits, which is precisely the
/// band the eye reads as flicker. Running the loop continuously moves the whole
/// pattern well above it.
///
/// Requested only while an effect is actually up, and released the moment it
/// stops: this is the one thing here with a real running cost, since it stops
/// the core idling between loops. Harmless on this unit, which is permanently
/// USB-powered (see the prevent_sleep switch), but it should not be on for a
/// steady colour that has no need of it.
inline void set_fast_loop(bool on) {
  static esphome::HighFrequencyLoopRequester requester;
  static bool active = false;
  if (on == active) {
    return;
  }
  active = on;
  if (on) {
    requester.start();
  } else {
    requester.stop();
  }
}

/// Per-effect dither state. One instance per effect, so switching effects does
/// not inherit a residual from the other.
struct DitherState {
  float err{0.0f};
};

/// Paint `color` onto the whole strip at modulation `m` (0..1), using spatial
/// and temporal dithering to reach brightnesses the 8-bit output cannot express.
///
/// `m` is a pure scale on the colour the band painter already chose: this
/// function never decides a hue, a band or a brightness setpoint.
inline void emit(esphome::light::AddressableLight &it, const esphome::Color &color, float m,
                 DitherState &state) {
  const int n = it.size();
  if (n <= 0) {
    return;
  }
  m = esphome::clamp(m, 0.0f, 1.0f);

  const float qf = 255.0f * m;
  const int q0 = esphome::clamp((int) qf, 0, 255);

  // Measure the PWM that a given buffer scale actually produces. LED 0 is used
  // as scratch; every LED is written before this function returns, and nothing
  // reaches the strip until the caller's schedule_show() runs afterwards.
  //
  // The brightest channel is the one that carries the modulation -- a saturated
  // hue leaves the others at or near zero, where they have no resolution to
  // contribute and would only drag the measurement down.
  auto probe = [&](int q) -> int {
    it[0] = color * (uint8_t) esphome::clamp(q, 0, 255);
    return std::max(it[0].get_red_raw(), std::max(it[0].get_green_raw(), it[0].get_blue_raw()));
  };

  // Find the tread of the staircase that q0 sits on: [q_lo, q_hi) all produce
  // the same PWM, and q_hi is the first value that produces more.
  //
  // The step count is bounded because a dark LED has wide treads -- at 2%
  // brightness a tread is ~100 buffer values -- and an unbounded walk there
  // would cost more than the smoothness is worth. Hitting the cap degrades to a
  // narrower interpolation, never to a wrong colour.
  const int kMaxWalk = 48;
  const int p0 = probe(q0);

  int q_hi = q0;
  for (int i = 0; i < kMaxWalk && q_hi < 255; i++) {
    if (probe(q_hi + 1) != p0) {
      break;
    }
    q_hi++;
  }
  q_hi = std::min(255, q_hi + 1);

  int q_lo = q0;
  for (int i = 0; i < kMaxWalk && q_lo > 0; i++) {
    if (probe(q_lo - 1) != p0) {
      break;
    }
    q_lo--;
  }

  // Where the ideal sits across that tread, 0..1.
  const float span = (float) (q_hi - q_lo);
  const float frac = (span > 0.0f) ? esphome::clamp((qf - (float) q_lo) / span, 0.0f, 1.0f) : 0.0f;

  // Spatial: how many LEDs take the upper value. Temporal: the remainder is
  // carried into the next frame, so the time-average lands on the exact
  // fraction rather than on the nearest third of it.
  //
  // A random tie-break whitens what would otherwise be a periodic carry
  // pattern. Error diffusion alone produces a regular pulse every 1/residual
  // frames, and at small residuals that pulse lands at a few Hz -- the one
  // frequency band the eye is most sensitive to. Spreading it into broadband
  // noise costs nothing and removes the only flicker mechanism here.
  const float want = frac * (float) n + state.err;
  int n_hi = (int) floorf(want + esphome::random_float());
  n_hi = esphome::clamp(n_hi, 0, n);
  state.err = want - (float) n_hi;
  // A long run against either rail (m pinned at 0 or 1) must not wind the carry
  // up into a jolt when the effect moves again.
  state.err = esphome::clamp(state.err, -1.0f, 1.0f);

  for (int i = 0; i < n; i++) {
    it[i] = color * (uint8_t) ((i < n_hi) ? q_hi : q_lo);
  }

  // The temporal half of this only works if frames arrive fast enough that the
  // alternation sits above the eye's flicker band, and the requested interval
  // is a floor, not a promise -- ESPHome runs effects from the main loop, which
  // WiFi and MQTT can stall. Report what is actually achieved rather than
  // assuming, since a rate that quietly collapsed would show up as flicker and
  // be blamed on the waveform.
  static uint32_t frames = 0;
  static uint32_t window_start = 0;
  frames++;
  const uint32_t now = esphome::millis();
  if (now - window_start >= 30000) {
    if (window_start != 0) {
      ESP_LOGD("ambience", "%.0f fps over the last %.0fs", frames * 1000.0f / (float) (now - window_start),
               (now - window_start) / 1000.0f);
    }
    frames = 0;
    window_start = now;
  }
}

}  // namespace ambience
