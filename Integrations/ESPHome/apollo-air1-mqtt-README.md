# Apollo AIR-1 — MQTT firmware

Custom [ESPHome](https://esphome.io/) firmware for the [Apollo Automation AIR-1](https://apolloautomation.com/products/air-1)
air quality sensor (ESP32-C3), configured with the CO2 add-on (SCD40) installed.
No Home Assistant — the device talks directly to a Mosquitto MQTT broker, and
[Node-RED](../coslab-nodered-flows) picks the readings up from there and writes
them into InfluxDB.

```
Apollo AIR-1  --MQTT-->  mosquitto (bodhi)  --MQTT-->  Node-RED  --Flux write-->  InfluxDB (bucket: air_quality)
```

## Hardware

- ESP32-C3, SEN55 (PM1/2.5/4/10, VOC index, NOx index, temperature, humidity),
  DPS310 (pressure), SCD40 (CO2 — optional add-on, installed on this unit),
  WS2812 RGB status LED, physical button. (The optional MICS-4514 gas sensor
  is not installed on this unit, so it is left out of the firmware.)
- Battery/USB powered, runs on a deep-sleep duty cycle: wakes, reads all
  sensors, publishes over MQTT, sleeps for 5 minutes (configurable via the
  "Sleep Duration" number entity), repeat. A 2-minute run-duration failsafe
  forces sleep even if MQTT never connects.
- **Continuous (USB) mode:** `prevent_sleep` defaults on, so a freshly flashed
  unit on USB power never deep-sleeps, and a 60s `interval:` re-publishes the
  combined `${mqtt_topic}/state` snapshot every minute — InfluxDB/the dashboard
  get one point per minute. This is the recommended mode for a plugged-in
  monitor (the SEN55 VOC/NOx gas-index algorithm needs continuous runtime to
  learn its baseline). Turn `prevent_sleep` off (dashboard Sleep toggle, the
  device's web UI, or its MQTT command topic) to fall back to the battery/
  duty-cycle behavior above (one publish per 5-minute wake).

## Relationship to upstream

This lives in a real GitHub fork of
[ApolloAutomation/AIR-1](https://github.com/ApolloAutomation/AIR-1), as
`apollo-air1-mqtt.yaml` alongside their own `Core.yaml`/`AIR-1.yaml` in this
same directory — the ESP32-C3 pinout, i2c bus, sensor platforms, and
deep-sleep timing all come directly from `Core.yaml`. The only substantive
change is swapping Home Assistant's native `api:` component for `mqtt:`,
since this deployment doesn't run Home Assistant. See the comment block at
the top of `apollo-air1-mqtt.yaml` for the full list of deltas. Diff against
upstream any time with `git fetch upstream && git diff upstream/main --
Integrations/ESPHome/Core.yaml Integrations/ESPHome/apollo-air1-mqtt.yaml`.

## MQTT

- Combined snapshot (what Node-RED subscribes to): **`cosmos-lab/smarthome/air1/state`**,
  one retained-off JSON message per wake cycle:

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

- ESPHome's `mqtt:` component also auto-publishes every entity individually
  under `cosmos-lab/smarthome/air1/<component>/<object_id>/state` (e.g.
  `cosmos-lab/smarthome/air1/sensor/co2/state`), and command topics for the switches/buttons
  (e.g. `cosmos-lab/smarthome/air1/switch/prevent_sleep/command`). Useful for debugging with
  `mosquitto_sub -t 'cosmos-lab/smarthome/air1/#' -v`; not needed for the normal data path.
- Availability: `cosmos-lab/smarthome/air1/status` gets `online`/`offline` (MQTT birth/LWT).

## Flashing

1. `cp apollo-air1-mqtt.secrets.yaml.example secrets.yaml` (in this directory)
   and fill in your WiFi SSID/password, an OTA password, and mosquitto
   host/username/password. The broker is `mqtt.cosmoslab.dev:8883` over TLS
   (the `iot` stack on `bodhi`, same endpoint the chassis firmware uses); the
   firmware embeds the Let's Encrypt root CA (ISRG Root X1) to verify it, and
   the port (8883) is set in `apollo-air1-mqtt.yaml`, not the secrets file.
2. First flash over USB:
   ```
   esphome run apollo-air1-mqtt.yaml
   ```
3. Subsequent updates can go over OTA the same way, as long as the device is
   awake — either wait for its next wake cycle, or wake it manually and hold
   the button for 3s (turns the `Prevent Sleep` switch on, which also stops
   the deep-sleep timer) so it doesn't go back to sleep mid-flash. Turn
   `Prevent Sleep` back off afterwards (via the web UI at the device's IP, or
   by publishing to `cosmos-lab/smarthome/air1/switch/prevent_sleep/command`) to resume the
   normal duty cycle.

## CO2 calibration

The SCD40 needs periodic forced calibration for accurate absolute readings.
The `Calibrate SCD40 To 420ppm` button (device web UI, or publish any payload
to `cosmos-lab/smarthome/air1/button/set_scd40_calibrate/command`) performs a forced
calibration assuming the sensor is currently in fresh outdoor air (~420ppm) —
run it outdoors, or in a well-ventilated room after a few minutes of
air exchange.

## Related repos

- [`coslab-nodered-flows`](../coslab-nodered-flows) — the "Apollo AIR-1" tab
  (MQTT in → InfluxDB out) lives there, since that repo tracks the live
  Node-RED instance directly.
- [`apollo-air1-dashboard`](../apollo-air1-dashboard) — web app for viewing
  current readings and history from InfluxDB.
