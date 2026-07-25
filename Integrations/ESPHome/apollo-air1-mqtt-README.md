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
- **It is a mains-powered feature in practice.** Evaluation happens on each
  publish, so on USB power that is once a minute. On battery the device is
  asleep (and the LED dark) for most of each 5-minute cycle.
- The alarm is edge-triggered — it fires once on the transition rather than
  re-issuing every cycle, so the strobe runs continuously instead of restarting
  from its first color every minute.
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
