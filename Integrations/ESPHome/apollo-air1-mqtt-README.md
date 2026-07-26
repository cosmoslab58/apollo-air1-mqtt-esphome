# Apollo AIR-1 — MQTT firmware

Custom [ESPHome](https://esphome.io/) firmware for the [Apollo Automation AIR-1](https://apolloautomation.com/products/air-1)
air quality sensor (ESP32-C3) that talks **directly to an MQTT broker, with no
Home Assistant anywhere in the picture**. Upstream's configs all assume HA's
native API; this one replaces it with `mqtt:`, so the readings land somewhere
any MQTT consumer can pick them up — Node-RED, Telegraf, a custom subscriber,
whatever you already run.

```
Apollo AIR-1  --MQTT-->  your broker  -->  your consumer (Node-RED / Telegraf / ...)
```

Two things here may be useful even if you don't own an AIR-1:

- **One combined JSON snapshot per cycle** on `<topic>/state`, instead of
  rejoining ~20 separate per-entity topics to reconstruct a single reading.
- **MQTT-triggered pull OTA** — publish a firmware URL and the device fetches
  and installs it itself, so updates never need the device's IP or hostname.
  See [Updating (OTA)](#updating-ota). The pattern is not AIR-1-specific.

## Configuration

Everything site-specific lives in `secrets.yaml` (gitignored) — broker host,
credentials, CA certificate, and where firmware images are hosted. Start from
`apollo-air1-mqtt.secrets.yaml.example`, which documents each key.

The only things to edit in `apollo-air1-mqtt.yaml` itself are the substitutions
at the top:

| Substitution | Default | Purpose |
|---|---|---|
| `name` / `friendly_name` | `apollo-air-1` | ESPHome device name |
| `version` | `1.1.0-mqtt` | firmware version; the OTA path compares it |
| `mqtt_topic` | `esphome/apollo-air-1` | root of the topic tree; everything derives from it |
| `mqtt_port` | `8883` | `8883` TLS, or `1883` plaintext (also comment out `certificate_authority:`) |

If you already have a topic convention and would rather not carry a local edit
to a tracked file, set `mqtt_topic` in `secrets.yaml` instead — the justfile
passes it through as `esphome -s mqtt_topic <value>` and the OTA script reads it
from the same place, so the two stay in sync.

## Hardware

- ESP32-C3, SEN55 (PM1/2.5/4/10, VOC index, NOx index, temperature, humidity),
  DPS310 (pressure), WS2812 RGB status LED, physical button.
- **SCD40 (CO2)** — an optional add-on, and this config assumes it is installed.
- **MICS-4514 (NO2/CO/H2/ethanol/methane/ammonia)** — the other optional add-on,
  left out here because the unit this was written for doesn't have it (the
  component would only fail its I2C init and publish null gas fields). If your
  unit does have it, copy the `mics_4514:` block back in from upstream
  `Core.yaml`.
- Battery/USB powered, runs on a deep-sleep duty cycle: wakes, reads all
  sensors, publishes over MQTT, sleeps for 5 minutes (configurable via the
  "Sleep Duration" number entity), repeat. A 2-minute run-duration failsafe
  forces sleep even if MQTT never connects.
- **Continuous (USB) mode:** `prevent_sleep` defaults on, so a freshly flashed
  unit on USB power never deep-sleeps, and a 60s `interval:` re-publishes the
  combined `${mqtt_topic}/state` snapshot every minute. This is the recommended
  mode for a plugged-in monitor — the SEN55 VOC/NOx gas-index algorithm needs
  continuous runtime to learn its baseline. Turn `prevent_sleep` off (the
  device's web UI or its MQTT command topic) to fall back to the battery
  duty-cycle behavior above, one publish per 5-minute wake.

  Note that `prevent_sleep` is `restore_mode: ALWAYS_ON` — it is **forced back
  on at every boot** and does not persist an off state. You can still turn it
  off at runtime to get the duty cycle, but a reboot or power cycle returns the
  unit to continuous mode.

  That is deliberate for a permanently mains-powered monitor. Three things can
  turn the switch off (the device web UI, an MQTT publish, a dashboard control),
  and a persisted off is a quiet, lasting degradation: data drops from one point
  per minute to roughly one per five, the air-danger LED is dark for most of
  each cycle, and the SEN55 VOC/NOx gas-index algorithms lose the continuous
  runtime they need to hold their learned baselines. If you are actually running
  on battery, change this to `RESTORE_DEFAULT_ON` so an off survives reboots.

## MQTT

Topics below are written relative to the `mqtt_topic` substitution, shown here
with its default `esphome/apollo-air-1`.

- **`<topic>/state`** — the data path. One non-retained JSON message per cycle,
  carrying every reading at once:

  ```json
  {
    "co2_ppm": 612,
    "pressure_hpa": 987.3,
    "temperature_c": 22.1,
    "humidity_pct": 41.5,
    "pm1_0_ugm3": 1.2,
    "pm2_5_ugm3": 2.4,
    "pm4_0_ugm3": 2.6,
    "pm10_0_ugm3": 2.9,
    "voc_index": 96,
    "nox_index": 1,
    "voc_quality": "Normal",
    "aqi": 12,
    "wifi_rssi_db": -58,
    "esp_temperature_c": 34.2,
    "uptime_s": 23,
    "firmware_version": "1.0.0-mqtt"
  }
  ```

  Publishing one combined message rather than ~20 per-entity ones means a
  consumer gets a complete, self-consistent reading without having to join
  topics or wait for stragglers.

- **`<topic>/command/ota`** — pull-OTA trigger. Publish here, never retained.
  See [Updating (OTA)](#updating-ota).
- **`<topic>/status`** — `online` / `offline` (MQTT birth message + LWT).
- ESPHome's `mqtt:` component also auto-publishes every entity individually
  under `<topic>/<domain>/<object_id>/state` (e.g.
  `esphome/apollo-air-1/sensor/co2/state`), plus command topics for the switches
  and buttons (e.g. `<topic>/switch/prevent_sleep/command`). Handy for poking at
  the device with `mosquitto_sub -t '<topic>/#' -v`; not needed for the normal
  data path. Home Assistant MQTT discovery is deliberately off (`discovery: false`).

## Air quality LED

The steady RGB colour, ported from upstream `Core.yaml` and then extended with a
combined `All` mode. Two config entities drive it:

| Entity | Values | Default |
|---|---|---|
| `Air Quality LED Source` | `Off`, `All`, `PM AQI`, `CO2`, `VOC Index`, `NOx Index` | `All` |
| `LED Brightness` | 0–100 % (0 = off) | 100 |

`All` is the local addition and the default: it colours the LED by the **worst**
band across every air-quality channel at once, so a spike on any one of them is
visible without knowing in advance which to watch. The single-metric options pin
the LED to one channel, which is what you want while calibrating or debugging
that channel. Each metric is banded into severity levels sharing one colour
table:

| Colour | PM AQI | CO2 (ppm) | VOC Index | NOx Index |
|---|---|---|---|---|
| Green | ≤ 50 — Good | ≤ 800 — Well ventilated | < 80 | < 20 |
| Yellow | ≤ 100 — Moderate | ≤ 1100 — Acceptable | < 150 | < 150 |
| Orange | ≤ 150 — Unhealthy (sensitive) | ≤ 2000 — Stuffy | < 250 | < 250 |
| Red | ≤ 200 — Unhealthy | ≤ 3500 — Poorly ventilated | < 400 | < 400 |
| Purple | ≤ 300 — Very unhealthy | ≤ 5000 — Very poor | ≥ 400 | ≥ 400 |
| Dim red | > 300 — Hazardous | > 5000 — Occupational limit | — | — |

**Each channel is labelled in its own terms, deliberately.** Upstream applied
the EPA AQI category names (*Good* … *Hazardous*) across every column. Those are
legally defined terms for outdoor criteria pollutants: they are correct for the
AQI column and borrowed authority everywhere else. EPA does not regulate indoor
CO2 at all — it is not a criteria pollutant — so calling 2500 ppm "Hazardous",
as upstream did, was both unfounded and wrong on the facts. 2500 ppm is an
ordinary closed bedroom overnight.

### Why it is "PM AQI" and not "NowCast AQI"

Upstream names this sensor *NowCast AQI*. It does not compute NowCast.

ESPHome's `aqi` component takes the **current** PM2.5 and PM10 readings, runs
each through the EPA breakpoint table with linear interpolation, and publishes
the higher of the two (`aqi_calculator.h`). It keeps no history whatsoever.

EPA's **NowCast** is a different algorithm: a weighted average over the previous
**12 hours** of PM, where the weight factor is derived from the ratio of minimum
to maximum concentration in that window. It exists so AirNow can report something
comparable to the 24-hour-averaged AQI in near-real-time while damping short
spikes. A component with no buffer structurally cannot implement it.

*PM* is the other half of the name and earns its place: a real EPA AQI is the
maximum across PM2.5, PM10, ozone, NO2, SO2 and CO. This device measures only the
two particulate channels, so on a high-ozone day the true outdoor AQI is higher
than this figure and nothing here would know. Read it as "the PM-driven portion
of an AQI", not as an AQI.

The breakpoints themselves are current: the first PM2.5 boundary is 9.1 µg/m³,
the 2024 revised NAAQS, not the superseded 12.1.

### Where the CO2 numbers come from

There is **no official government severity scale for indoor CO2.** Not from EPA,
not from CDC, not from WHO (whose indoor air guidelines cover benzene, CO,
formaldehyde, NO2 and radon among others, but not CO2). Only the ends of this
scale are anchored:

| ppm | Source | What it actually is |
|---|---|---|
| 800 | CDC ventilation guidance | Ventilation-adequacy proxy, **not** a health threshold |
| 1100 | ASHRAE comfort/odour range | Professional society, ~700 ppm over outdoor ambient |
| 5000 | **OSHA PEL** (29 CFR 1910.1000), NIOSH REL | 8 h TWA — the one enforceable number here |

`2000` and `3500` are interpolation between those anchors. They are a reasonable
curve, not guidance, and are not presented as anything more.

Two caveats worth keeping in view. ASHRAE 62.1 deliberately sets no CO2 limit —
it treats CO2 as a proxy for whether enough outdoor air is arriving, not as a
toxin. And the OSHA PEL is an 8-hour occupational limit for **healthy adult
workers**; a bedroom, occupied eight hours a night indefinitely and possibly by
children, is not the population or the exposure pattern it was written for. It is
the best-defined number available, not a bedroom-appropriate one.

The top CO2 band now coincides exactly with the `danger_co2_ppm` strobe
threshold rather than sitting at half of it, so the worst steady colour means
"you are at the level that trips the alarm" instead of contradicting it.

The two Sensirion gas indices carry no category words at all and cap at purple.
They score against a learned ~72 h baseline, so they measure "different from
recent normal", not "harmful" — which is not a severity claim, and certainly not
one that earns a top band. Their band edges differ from each other because VOC is
centred on an index offset of 100 while NOx uses the driver default of 1.

The top band is rendered as a dimmed red rather than a true maroon. ESPHome
normalises every light call so the brightest RGB channel becomes 1.0, so a
"dark red" `set_rgb(0.5, 0, 0)` is rescaled straight back to pure red and is
indistinguishable from the red band — brightness is the only axis that survives
normalisation.

A metric that has not produced a reading yet (NaN) does not vote. Under `All` a
warming-up SCD40 or a post-reset VOC baseline can neither hold the colour at
green nor invent a red; if *every* selected channel is NaN the LED goes dark, so
a missing module reads as dark, not as "Good".

The LED repaints on every sensor reading (~10 s), not once per MQTT publish, so
it tracks a change at about the rate a person notices one. Repaints that would
not change anything are skipped, which keeps the light's own MQTT state topic
quiet on an idle device.

### Fade and pulse

Band changes are not instant swaps:

| Change | Behaviour |
|---|---|
| Air improves (e.g. orange → green) | 1 s crossfade |
| Air worsens (e.g. green → orange) | Snap to the new colour, blink it 3× , then hold |
| Brightness slider moved | 1 s crossfade |
| LED turning off | Instant, so the `0 %` master-off feels responsive |

A degradation is the thing worth looking up for, so it announces itself; an
improvement does not need to interrupt you. The pulse modulates brightness only,
dipping to 10 % of the current level and back, so it stays proportional whatever
the slider is set to. A worsening skips the crossfade because a 1 s colour fade
underneath a blink muddies both.

Going from off to a colour is not treated as a worsening — the LED being enabled
says nothing about the air. Pulses are cancelled if the air improves mid-blink,
if the LED goes dark, or if the danger alarm fires, since in each case the blink
would be dimming a colour that no longer means what it was announcing.

> The alarm strobe is never faded. ESPHome rejects a transition and an effect in
> the same light call, so the fade necessarily lives only in the steady-state
> path — which is also where you want it.

### How it interacts with the air danger alarm

The alarm below outranks this. While it is active the strobe owns the LED and
the steady color is suppressed; when the air clears, the band color is
restored rather than the LED simply going dark. The boot self-test and the
button-press status flash also take priority for the few seconds they run.

What happens *between* alarms is a choice, made by two switches:

| `LED Alarm Mode` | `LED Steady In Alarm Mode` | Between alarms | At danger |
|---|---|---|---|
| Off | (ignored) | Steady band color | Steady band color — no strobe |
| **On** (default) | **Off** (default) | Dark | Strobe |
| On | On | Steady band color | Strobe, over the color |

The default pairing is the quiet one, and it is the default for a reason: a dark
LED makes the strobe unmissable, and makes *dark itself* mean "nothing is
wrong". Turning on `LED Steady In Alarm Mode` trades that away for a light that
reports the band all the time — you can read the air without opening anything,
but you no longer get the answer from across the room without looking at which
color it is. Nothing else moves: the strobe keeps its own thresholds, its
hardcoded 100%, and its priority over the color.

Two guards are inherited from upstream and matter if you edit this: the LED is
not written in the first 5 s after boot (the select's `restore_value` fires
`on_value` before the LED strip is initialised, which faults), and repaints are
skipped while `statusCheck` or `testScript` is running.

> **`restore_value` persists the option's index, not its name.** The `All`
> default therefore only applies to a newly flashed or factory-reset unit, and —
> more surprisingly — inserting `All` at position 1 shifted the whole list, so
> deployed units came back pointing at a *different* option than before:
>
> | Was set to | Old index | Restores as |
> |---|---|---|
> | `Off` | 0 | `Off` |
> | `NowCast AQI` | 1 | `All` |
> | `CO2` | 2 | `NowCast AQI` |
> | `VOC Index` | 3 | `CO2` |
>
> The names above are the ones in force at the time of that upgrade. `NowCast
> AQI` was later renamed to `PM AQI` **in place**, keeping index 2, so that
> rename was safe for deployed units — renaming an option is fine, inserting or
> reordering one is not.
>
> Check the entity after any OTA that touches this list, and set it explicitly:
>
> ```bash
> mosquitto_pub -t '<topic>/select/air_quality_led_source/command' -m 'All'
> ```
>
> Append new options at the end unless you actually want that remap.

## Air danger alarm

The RGB LED strobes through saturated colors (red → white → blue → amber →
magenta, with dark gaps) whenever a reading crosses a level that is genuinely
harmful to breathe. It clears itself once everything is back under.

| Reading | Threshold | Why |
|---|---|---|
| CO2 | **5000 ppm** | ACGIH TLV / OSHA PEL 8-hour occupational limit |
| AQI | **200** | EPA "Very Unhealthy"; derived from PM2.5 + PM10, so particulates are covered by this one number |
| VOC Index | **off** | Disabled by default — see below. Set to `400` for the top band of `VOC Quality` ("Extremely abnormal") |

Tune them in the `substitutions:` at the top of `apollo-air1-mqtt.yaml`
(`danger_co2_ppm`, `danger_aqi`, `danger_voc_index`). **Setting any of them to
`0` switches that channel off** — the reading is still taken and published, it
just stops driving the LED. The comparison folds away at compile time, so a
disabled channel costs nothing.

These sit deliberately high. 1000–2000 ppm CO2 is stuffy and measurably hurts
concentration, but it is not dangerous, and an alarm that fires every afternoon
in a closed office is one you stop seeing. The softer "you should ventilate"
tier is already handled by the Node-RED Gotify alert at 1000 ppm / AQI 100 —
this is the tier above that, meant to read as *leave the room*.

Notes and caveats:

- **VOC is off by default, and that depends on where the unit lives.** The VOC
  Index is scored against a ~72h learned baseline rather than an absolute
  concentration, so it answers "is this different from recent normal?" and not
  "is this harmful?". Everyday domestic events — cleaning spray, deodorant, a
  new package off-gassing — reach the top band with nothing actually wrong. In a
  bedroom that is a light strobing at you overnight for no reason, and a
  nuisance alarm is one you stop reading, which costs you the CO2 and AQI
  triggers that do mean something. Somewhere solvent vapour is a genuine hazard
  and a spike really does mean something — a workshop, garage, or lab — set
  `danger_voc_index: "400"` and it behaves as an alarm channel like the others.
  Either way the VOC index and `VOC Quality` are measured and published.
- **A dead sensor does not raise the alarm.** Readings are NaN-guarded, so a
  sensor that never reports reads as "not dangerous". This is an alerting
  convenience, not a life-safety device — it is not a substitute for a CO or
  smoke alarm, neither of which this hardware can detect at all.
- **The strobe always runs at 100%, and `LED Brightness` never touches it** —
  not even `0` silences it. An alarm a stray slider drag can dim to invisibility
  is not an alarm. The slider applies only to the steady indicator color, which
  means it does nothing whatsoever in the default alarm-only pairing (there is
  no steady color to scale) and works normally again once `LED Steady In Alarm
  Mode` gives it one. (ESPHome template numbers cannot be greyed out at runtime,
  so the slider stays movable either way; it simply has no effect in the mode
  with no color of its own.)
- **It is a mains-powered feature in practice.** Evaluation happens on every
  air-quality reading, so on USB power that is roughly every 10 seconds. On
  battery the device is asleep (and the LED dark) for most of each 5-minute
  cycle.
- The alarm is edge-triggered — it fires once on the transition rather than
  re-issuing every cycle, so the strobe runs continuously instead of restarting
  from its first color on every reading.
- It overrides the LED, so it will paint over the boot status colors
  (blue/green/yellow) that `statusCheck` shows for the first 5 seconds.

## Flashing

1. `cp apollo-air1-mqtt.secrets.yaml.example secrets.yaml` (in this directory)
   and fill it in — WiFi credentials, an OTA password, your broker host and
   credentials, and the CA that signed your broker's certificate. The example
   file explains each key, and ships with Let's Encrypt's ISRG Root X1 already
   in place since that covers the common case. Set the broker port via the
   `mqtt_port` substitution in `apollo-air1-mqtt.yaml`, not here.
2. First flash over USB (from the repo root):
   ```
   just install
   ```
   This one-time push is what puts the pull-OTA agent on the device. Everything
   after it goes over MQTT.

## Updating (OTA)

Updates are **pull-based and triggered over MQTT**: you publish a firmware URL,
and the device downloads and installs the image itself.

```
just ota            # bump patch, build, publish, trigger
just ota minor      # or major, or an explicit 1.4.0
just check          # what's the device running vs. this repo?
just logs           # live device logs over MQTT
```

The reason it works this way rather than `esphome run`: push OTA needs the
device's address, and `name_add_mac_suffix: true` makes that
`apollo-air-1-<mac>.local` — not necessarily something you have once the unit is
deployed, particularly across VLANs or anywhere mDNS doesn't cross subnets.
(`.esphome/storage/…json` even records a plain `apollo-air-1.local`, which won't
resolve.) The device dials *out* to the broker, so the broker is a reliable
rendezvous point no matter where the unit sits or what IP it picked up.

**Setup:** point `ota_publish_dir` and `ota_base_url` in `secrets.yaml` at a
directory you can write to that's served by a web server the *device* can reach.
Any static host works — nginx, Caddy, Apache, even `python3 -m http.server`.

Either scheme is fine. `https://` needs no extra configuration — the ESP32
validates against esp-idf's bundled root CAs, which is a separate trust store
from the `mqtt_ca_cert` used for the broker. `http://` works too and is one
fewer certificate to keep valid, at the cost of the image being unauthenticated
in transit; the `.md5` sidecar travels the same path, so it catches corruption
but not tampering. Redirects are followed either way, though `air1-ota.py`
insists on a direct 200 so a misconfigured host fails loudly rather than
serving the device an HTML redirect page.

What `just ota` does:

1. bumps the `version:` substitution and rebuilds
2. copies `firmware.ota.bin` + an `.md5` sidecar into `ota_publish_dir` under a
   version-stamped filename
3. checks the URL returns a direct HTTP 200 of the right length
4. publishes to `<topic>/command/ota`:
   ```json
   {"command":"ota","url":"https://example.com/apollo-air1/apollo-air-1-1.2.0-mqtt.ota.bin","version":"1.2.0-mqtt"}
   ```
5. waits for the device to reboot and report the new version back

The device writes the image to its spare flash partition, reboots into it, and
ESP-IDF rolls back to the previous partition automatically if the new firmware
doesn't stay up — none of which needs configuring, it's built into the chip.
TLS works out of the box too: ESPHome's `http_request` verifies against esp-idf's
bundled Mozilla root CA store, so plain `https://` is fine.

> **Never publish the OTA command retained.** The broker would replay it on
> every reconnect, and the device reconnects right after an OTA reboot — an
> endless reflash loop. `air1-ota.py` always publishes with `retain=False`. As a
> backstop the firmware compares the payload's `version` against its own and
> skips the flash when they match, which also makes re-sending a command safe.

Push OTA is still available as a bench/recovery path (`just install` over USB,
or `just install-net <address>`), and is password-protected via `ota_password`.

### Deep sleep and OTA

On USB the unit stays awake (`prevent_sleep` defaults on), so an OTA can land at
any time. On battery it's only awake ~2 minutes per cycle, so hold the button for
3s first — that turns `Prevent Sleep` on and stops the deep-sleep timer — then
trigger the update. Since `prevent_sleep` is `ALWAYS_ON`, a unit that is
duty-cycling has had the switch turned off *this* boot; power-cycling it is
enough to get back to a continuously awake device you can OTA at will.

## CO2 calibration

The SCD40's **automatic self-calibration (ASC) is deliberately off** here
(`automatic_self_calibration: false`). ASC re-baselines the sensor by assuming
the lowest CO2 it sees over a multi-day window is fresh outdoor air (~400ppm).
That holds for a sensor that gets aired out regularly and is actively wrong for
one that lives indoors permanently — an occupied room rarely drops to 400ppm,
so ASC would drag the baseline down and under-report. The cost of leaving it off
is that absolute accuracy drifts unless you calibrate manually, below. (Upstream
`Core.yaml` exposes a runtime `CO2 Auto Calibration` switch instead; it is not
ported here. It needs a script of raw I2C commands — stop periodic measurement,
write 0x2416, restart — because the SCD40 only accepts the setting while idle
and forgets it on power loss.)

Forced calibration is the manual alternative: the `Calibrate SCD40 To 420ppm`
button (device web UI, or publish any payload to
`<topic>/button/calibrate_scd40_to_420ppm/command`) tells the sensor it is in
fresh outdoor air *right now*.

> Only press it outdoors, or indoors after several minutes of real air
> exchange. Running it at a normal indoor reading bakes that reading in as
> 420ppm — a 700ppm room calibrated this way leaves a persistent ~280ppm error.

## Relationship to upstream

This is a fork of [ApolloAutomation/AIR-1](https://github.com/ApolloAutomation/AIR-1);
`apollo-air1-mqtt.yaml` sits alongside their own `Core.yaml` / `AIR-1.yaml` in
this directory. The ESP32-C3 pinout, I2C bus, sensor platforms, deep-sleep
timing, and RGB status logic all come straight from `Core.yaml`. The substantive
changes are swapping Home Assistant's native `api:` for `mqtt:`, adding the
combined `/state` snapshot, and adding pull OTA — see the comment block at the
top of `apollo-air1-mqtt.yaml` for the full list.

Diff against upstream any time:

```
git fetch upstream
git diff upstream/main -- Integrations/ESPHome/Core.yaml Integrations/ESPHome/apollo-air1-mqtt.yaml
```
