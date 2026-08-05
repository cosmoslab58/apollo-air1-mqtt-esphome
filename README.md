# Apollo AIR-1

> ### This fork adds MQTT-only firmware (no Home Assistant)
>
> Upstream's configs all use Home Assistant's native API. This fork adds
> **[`Integrations/ESPHome/apollo-air1-mqtt.yaml`](Integrations/ESPHome/apollo-air1-mqtt.yaml)**,
> which replaces it with `mqtt:` so the AIR-1 can feed Node-RED, Telegraf, or
> any other MQTT consumer directly. See
> **[the MQTT firmware README](Integrations/ESPHome/apollo-air1-mqtt-README.md)**.
>
> Four pieces may be useful even if you don't own an AIR-1:
>
> - **One combined JSON snapshot per cycle** on `<topic>/state`, instead of
>   rejoining ~20 per-entity topics to reconstruct a single reading.
> - **MQTT-triggered pull OTA** — publish a firmware URL and the device fetches
>   and installs it itself, so updates never need the device's IP or hostname.
>   Handy for units behind a VLAN or anywhere mDNS doesn't reach. The pattern is
>   generic ESPHome, not AIR-1-specific.
> - **Day/night LED dimming driven by sun elevation**, with `time:` + `sun:` on
>   the device so the ramp needs no timezone and no network schedule — the
>   device works out where the sun is and eases between two brightness
>   setpoints across twilight.
> - **Sub-LSB brightness dithering** for the ambience effects
>   ([`ambience_dither.h`](Integrations/ESPHome/ambience_dither.h)). A
>   comfortable indoor brightness is a very small PWM number — 11/255 here — so
>   a smooth fade has almost no levels to move through. Dithering spatially
>   across the LEDs and temporally across frames synthesises the missing ones.
>   It measures where the output steps are rather than assuming a gamma curve,
>   which makes it portable to any addressable ESPHome light.
>
> Everything else below is upstream's documentation, unchanged.

---

[![Apollo AIR-1](https://img.youtube.com/vi/Tqq4Si1y34c/hqdefault.jpg)](https://www.youtube.com/watch?v=Tqq4Si1y34c)


Key Features of the AIR-1 Sensor:

MiCS-4514 Below have individual gas % readout: CO, C2H5OH (Alcohol), H2, NO2, and NH3

SCD40: CO2 and includes temperature and humidity sensing capabilities. 

SEN55: Particulate matter (PM1, PM2.5, PM10), VOCs, NOx, humidity, and temperature. 

DPS368: Barometric air pressure and temperature.

Dimensions & Design: 

The AIR-1 measures just 61mm x 61mm x 30mm, and we have focused on efficient heat management within this small package to maintain sensor accuracy. This includes a thoughtful PCB layout and case design, incorporating ventilation and strategic component placement. 

YAML Files:
- AIR-1.yaml: This file is a minimal config. It doesn't have the bluetooth or OTA components. Use this if you are looking to add BLE proxy or BLE tracking.
- AIR-1_BLE.yaml: This file contains BLE proxy code. We use it as an automated test during our build process. But can be an example for adding BLE proxy to your device.
- AIR-1_Factory.yaml: This is the firmware flashed by us on new devices. It contains the components for ESP improve, allowing easy adoption in Home Assistant. When you load the device in ESPHome addon, it will grab the firmware from AIR-1.yaml which no longer has the improve.



Links:

Discord (Support/feedback/discussion/future products): [https://dsc.gg/ApolloAutomation] \
Shop: [https://apolloautomation.com](https://apolloautomation.com/products/air-1) \
Wiki: [https://wiki.apolloautomation.com](https://wiki.apolloautomation.com/) \
3D Files: [https://www.printables.com/model/932001-apollo-automation-air-1-air-quality-sensor-for-hom](https://www.printables.com/model/932001-apollo-automation-air-1-air-quality-sensor-for-hom)
