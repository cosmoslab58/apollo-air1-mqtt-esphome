# Provisioning an AIR-1 from scratch

Taking a stock Apollo AIR-1 to a unit whose readings appear in InfluxDB and
Grafana, assuming no prior knowledge of this deployment.

The **device** side is documented in depth in
[`Integrations/ESPHome/apollo-air1-mqtt-README.md`](../Integrations/ESPHome/apollo-air1-mqtt-README.md)
— hardware, publish cadence, LED behaviour, CO2 calibration, OTA. This file
covers what that one does not: the **server side**, and how a unit is attached
to an account.

---

## The topic convention

**This is the rule for the whole broker, not just for AIR-1 units.** It is the
authoritative statement; the chassis-shield repo's `docs/PROVISIONING.md` points
here rather than restating it.

```
<account>/<device-path>/<channel...>
```

- **Segment 0 is the account.** It is the ACL root, the isolation boundary, and
  the value of the `tenant` tag in InfluxDB. `walnut` is a household account;
  `cosmoslab` is the lab's own account. Same category of thing — an *owner* —
  even though one is a place and one is an organization.
- **The device path is one or more segments, and its LAST segment is the device
  id.** For a sensor that measures a whole room, the convention is to use the
  room (`living_room`). For a product family it may carry a grouping segment
  first (`aeroguard/J-ROC`).
- **Everything after the device path is the channel** — `state`, `status`,
  `telemetry`, `config/home`, `sensor/co2/state`.

Live examples, both trees:

```
walnut/living_room/state                     home sensor, device path depth 1
walnut/basement_bath_fan/status/switch:0     home actuator, device path depth 1
cosmoslab/aeroguard/J-ROC/telemetry          product family, device path depth 2
```

Node-RED derives identity from the topic and is depth-agnostic:
`tenantId = parts[0]`, `deviceId = parts[parts.length - 2]`.

Three things this rule deliberately does **not** say:

- **The device id is a device slug, not necessarily a room.**
  `walnut/basement_bath_fan` is a fixture, because a basement bathroom can hold
  both a fan and a sensor. Room-as-identity is the convention for whole-room
  sensors only.
- **The model name never appears in the topic.** `walnut/air1/living_room` was
  rejected: a homeowner thinks in rooms, not part numbers.
- **Variable device-path depth is accepted on purpose.** The `aeroguard/` segment
  is a product family, so a customer buying two product lines gets them separated
  inside their own account.

> ⚠️ **The subscriptions are where depth is baked in, and they are literal.**
> Node-RED's home-device subscriptions are `+/+/state` and `+/+/config/home`,
> which assume a home device is *exactly* depth 2; the AeroGuard ones are
> `+/aeroguard/+/telemetry` and `+/aeroguard/+/status`, which hardcode the product
> family. A device that does not fit either shape is **silently ignored** — no
> error, no log line, nothing in InfluxDB. So the depth constraints above are
> rules, not accidents: home devices are `<account>/<device>`, AeroGuard units are
> `<account>/aeroguard/<device>`. Adding a shape means adding an `mqtt in` node
> (one topic per node).

---

## 1. Secrets

ESPHome reads `secrets.yaml` (gitignored). Create it next to the YAML:

```yaml
wifi_ssid: "…"
wifi_password: "…"
mqtt_broker: "mqtt.example.com"
mqtt_username: "<account>_<device>"   # e.g. walnut_living_room
mqtt_password: "…"
ota_password: "…"
mqtt_ca_cert: |
  -----BEGIN CERTIFICATE-----
  …
  -----END CERTIFICATE-----
```

`mqtt_ca_cert` is required for port 8883. For a plaintext broker set
`mqtt_port: "1883"` in the substitutions **and** comment out the
`certificate_authority:` line, or the device will refuse to connect.

---

## 2. Substitutions — the account and device live here

In `Integrations/ESPHome/apollo-air1-mqtt.yaml`:

```yaml
substitutions:
  name: apollo-air-1                 # ESPHome node name; must be unique on the network
  mqtt_topic: <account>/<device>     # ← the device's topic root, e.g. walnut/living_room
  mqtt_port: "8883"
```

In practice the committed YAML keeps a neutral default and the real value lives
in the gitignored `secrets.yaml` as `mqtt_topic:`, which `just` passes to ESPHome
as a substitution override (`-s mqtt_topic …`). Confirm which one is in effect
with `python3 scripts/air1-ota.py --get mqtt_topic`.

**`mqtt_topic` is the whole tenancy story.** Everything derives from it:

```
<account>/<device>/state           combined JSON snapshot — the data path
<account>/<device>/status          online/offline (birth + last will)
<account>/<device>/config/home     retained account config — see §5
<account>/<device>/command/ota     pull-OTA trigger
```

Per the convention above, the **first segment is the account** and the **last
segment before the channel is the device id**. Node-RED subscribes with wildcards
in both slots (`+/+/state`) and derives both from the topic, so adding an account
or a second device requires no flow changes — as long as the device path is one
segment deep.

---

## 3. Flash

```bash
just install              # USB
just install-net <device> # over the network, once it is already running
just logs                 # follow
```

---

## 4. Broker account and ACL

Naming an ACL file at all switches Mosquitto to "deny unless matched", so every
client must be listed.

```bash
mosquitto_passwd -b /path/to/password.txt <account>_<device> '<password>'

cat >> /path/to/acl.conf <<'EOF'

user <account>_<device>
topic readwrite <account>/<device>/#
EOF

docker kill -s HUP mosquitto
```

> 🔴 **That is one of TWO lines you need, and the second one is easy to miss.**
> The ingestion hub account `nodered` does **not** hold a bare `#` — it holds each
> account root explicitly (`cosmoslab/#` and `walnut/#` today). A brand-new
> `acme/` root is therefore **invisible to Node-RED** until a second grant is
> added under `user nodered`:
>
> ```
> user nodered
> topic readwrite acme/#
> ```
>
> The failure is completely silent: a denied SUBSCRIBE is not logged at any
> level, and a denied PUBLISH is logged at `debug` only, which is off. Adding a
> device to an account root that already exists needs only the first line.

> **A denied publish is silent.** At QoS 0 the client is told nothing, and
> Mosquitto logs ACL denials at **`debug` level only** — so a wrong topic rule
> makes data vanish with no evidence anywhere. Add `log_type debug` temporarily
> while editing ACLs, confirm you see `Denied PUBLISH` for something that should
> fail, then remove it (debug is ~25k lines/hour).
>
> Also: **`log_type` takes one value per line.** `log_type error warning notice
> information` looks right and silently applies only `error`.

---

## 5. Register the account

This is the step that is easy to miss, because the device works without it —
its own readings flow, but every **outdoor** provider (AirNow, OpenWeatherMap,
Google Air Quality, PurpleAir) is polled per account and needs the account's
location.

Publish a **retained** JSON message to `<account>/<device>/config/home`:

```bash
mosquitto_pub -h <broker> -p 8883 --capath /etc/ssl/certs \
  -u <account>_<device> -P '<password>' -r \
  -t '<account>/<device>/config/home' -m '{
    "lat": 42.583,
    "lon": -83.365,
    "zip": "48324",
    "location_slug": "orchard_lake",
    "purpleair_sensor": 178257
  }'
```

| Field | Used by | If omitted |
|---|---|---|
| `lat` / `lon` | OpenWeatherMap, Google | falls back to the built-in default coordinates |
| `zip` | AirNow | falls back to `48324` |
| `location_slug` | InfluxDB `location` tag | falls back to `orchard_lake` |
| `purpleair_sensor` | PurpleAir | falls back to sensor `178257`; set `null` to skip |

**Retained matters.** Node-RED builds its tenant registry from this message on
every reconnect; a non-retained publish registers the tenant until the next
redeploy and then silently disappears.

API keys come from the Node-RED container's environment
(`AIRNOW_API_KEY`, `OWM_API_KEY`, `GOOGLE_AQ_API_KEY`, `PURPLEAIR_API_KEY`) and
are shared across tenants unless a per-tenant key is set in the registry entry.

---

## 6. Verify

1. `just logs` — MQTT connected, a `state` publish each cycle.
2. Broker: `mosquitto_sub -h <broker> -p <port> -u <account>_<device> -P '<pw>' -t '<account>/<device>/state' -v`
   shows one JSON snapshot. **Pass the connection flags explicitly.** Do not keep
   them in a `~/.config/mosquitto_sub` defaults file: mosquitto's clients read
   that file implicitly, it stores the password in plaintext, and it goes stale
   silently the moment the account is renamed or rotated.
3. Node-RED: the *Fan out tenants* nodes report `N tenant(s)`, and *Tenant
   Config Ingress* shows your tenant and its `location_slug`.
4. InfluxDB: `air_quality` measurement carries `tenant` **and** `device_id`
   tags.

A tenant that appears in the fan-out but not in the config ingress means the
`config/home` message was not retained.

---

## 7. Monitoring

The flow already runs a **per-tenant freshness watchdog**: the AIR-1 publishes
every 60 s (15 s when its air band is elevated), so five minutes of silence
raises a Gotify alert, and recovery clears it. It is edge-triggered, so a unit
that stays down does not repeat.

Its blind spot is deliberate and worth knowing: it runs *inside* Node-RED, so it
cannot report Node-RED itself being down. That needs an external check (an
Uptime Kuma monitor on `:1880`).

---

## 8. Adding a second account

No flow changes, no cloned tabs:

1. Broker account + **two** ACL lines (§4) — the account's own `user`/`topic`
   pair, **and** a grant on the new root under `user nodered`. It is not one
   line, and omitting the second fails silently.
2. `mqtt_topic: <their-account>/<their-device>` in their unit's substitutions,
   then flash.
3. Publish their retained `config/home` (§5).
4. Point their Grafana dashboard at `tenant == "<their id>"`.

---

## 9. Updating over the air

```bash
just ota          # bump patch, build, publish, trigger over MQTT
just check        # compare device version vs repo, without pushing
```

OTA is a **pull**: you publish a firmware URL to `<account>/<device>/command/ota`
and the device fetches it itself, so it works for units behind a VLAN or anywhere
mDNS does not reach — no IP or hostname needed.

> 🔴 **Two OTA traps, both of which have cost real time.**
>
> - **`just ota` / `just push` triggers on the NEW topic.** `air1-ota.py --topic`
>   defaults to `$AIR1_TOPIC`, then to `secrets.yaml`'s `mqtt_topic`. During a
>   *topic migration* that is the topic the device is not listening on yet, so the
>   trigger is a silent no-op. Correct sequence: bump and build with the new topic,
>   then trigger on the OLD one with `AIR1_TOPIC=<old-root> just push`. The
>   post-trigger confirmation watches `<topic>/sensor/apollo_firmware_version/state`,
>   so aimed at the old root it **times out even on success** — verify with
>   `just check`, not with the confirmation window.
> - **The device skips an OTA whose `version` equals its own.** Two different
>   images under one version means the second silently does not install. `just bump`
>   before every build.
>
> **Keep at least one published image other than the running one on the current
> topic and a live account.** Every image on the shelf that bakes in a retired
> topic root or a deleted broker account is not an old version, it is a trap: a
> deliberate rollback to one produces a device that boots, joins WiFi, and is
> permanently unreachable over MQTT — including the pull-OTA trigger itself, which
> is the only way to push it forward again. Retire those images out of the
> published directory rather than leaving them served.
