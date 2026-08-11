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
cosmoslab/<category>/<account>/<vendor>/<model>/<device>/<channel...>
   seg0      seg1       seg2      seg3     seg4     seg5     seg6+
```

**Exactly six segments before the channel. That is a rule, not a habit** — three
separate properties below depend on the positions being fixed.

| Seg | Role | Today | Notes |
|---|---|---|---|
| 0 | operator | `cosmoslab` | Constant. Reserves the root so another operator's tree could coexist. |
| 1 | category | `smarthome`, `industrial` | Broad product line. |
| 2 | **account** | `walnut`, `founding-eng` | The **owner**: the ACL root, the isolation boundary, and the `tenant` tag in InfluxDB. |
| 3 | vendor | `apollo`, `shelly`, `chassisshield` | Who makes it. |
| 4 | model | `air1`, `mini1pmg4`, `aeroguard` | The product. Lowercase slug. |
| 5 | **device** | `living_room`, `basement_bath_fan`, `J-ROC` | Stable local name; the `device_id` tag. |
| 6+ | channel | `state`, `telemetry`, `config/home`, `sensor/co2/state` | One or more segments. |

Live examples, all three trees:

```
cosmoslab/smarthome/walnut/apollo/air1/living_room/state
cosmoslab/smarthome/walnut/shelly/mini1pmg4/basement_bath_fan/status/switch:0
cosmoslab/industrial/founding-eng/chassisshield/aeroguard/J-ROC/telemetry
```

Because the positions are fixed, every consumer derives identity by **absolute
left index** — `account = parts[2]`, `device = parts[5]` — which is correct for
*every* channel shape. Right-indexing (`parts[length - 2]`) is what the old
scheme needed, and it breaks on a two-segment channel like `config/home`.

### Why the account sits above the vendor

So that isolation is a **pure prefix**, the strongest form an MQTT ACL can take:

```
topic readwrite cosmoslab/smarthome/walnut/#      # no wildcards at all
```

Every wildcard between the root and the isolation boundary is a place a mistake
becomes a cross-account grant. Nothing is lost by putting the account first —
fleet queries just move the wildcard:

```
cosmoslab/+/+/apollo/air1/#               every AIR-1, every account
cosmoslab/+/+/apollo/#                    every Apollo device
cosmoslab/+/+/chassisshield/aeroguard/#   every AeroGuard unit
```

> 🔴 **The segment order must be identical in every category.** Account-first
> under `smarthome` but vendor-first under `industrial` would make
> `cosmoslab/+/walnut/#` match a *vendor* slot as well as an account slot — a
> customer sharing a name with a vendor would silently gain another tenant's
> tree — and every topic-parsing function would have to branch on the category.

### Two things this rule deliberately does not say

- **The device id is a slug, not necessarily a room.**
  `.../shelly/mini1pmg4/basement_bath_fan` is a fixture, because a basement
  bathroom can hold both a fan and a sensor. Room-as-identity is the convention
  for whole-room sensors only.
- **The model in segment 4 is a classification, not an identity.** The device is
  still `living_room`, not `air1`. Putting the model in the identity slot was
  rejected: a homeowner thinks in rooms, not part numbers.

> ⚠️ **Subscriptions are literal, and a device that does not fit is silently
> ignored** — no error, no log line, nothing in InfluxDB. Node-RED subscribes
> with `cosmoslab/+/+/+/+/+/state` and `.../config/home` for home devices, and
> scopes the AeroGuard ones to `cosmoslab/+/+/chassisshield/aeroguard/+/telemetry`
> and `.../status`. That family literal is deliberate: a blanket six-wildcard
> `status` subscription would also match an AIR-1's own online/offline status and
> feed foreign messages into a watchdog that is specifically about AeroGuard units.
> Supporting a genuinely new shape means adding an `mqtt in` node — one topic per
> node.

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
  mqtt_topic: cosmoslab/<category>/<account>/<vendor>/<model>/<device>
  #   e.g. cosmoslab/smarthome/walnut/apollo/air1/living_room
  mqtt_port: "8883"
```

In practice the committed YAML keeps a neutral default and the real value lives
in the gitignored `secrets.yaml` as `mqtt_topic:`, which `just` passes to ESPHome
as a substitution override (`-s mqtt_topic …`). Confirm which one is in effect
with `python3 scripts/air1-ota.py --get mqtt_topic`.

**`mqtt_topic` is the whole tenancy story.** Everything derives from it:

```
cosmoslab/<category>/<account>/<vendor>/<model>/<device>/state         combined JSON snapshot — the data path
cosmoslab/<category>/<account>/<vendor>/<model>/<device>/status        online/offline (birth + last will)
cosmoslab/<category>/<account>/<vendor>/<model>/<device>/config/home   retained account config — see §5
cosmoslab/<category>/<account>/<vendor>/<model>/<device>/command/ota   pull-OTA trigger
```

Per the convention above, the **account is segment 2** and the **device is
segment 5**. Node-RED subscribes with wildcards in every slot between them
(`cosmoslab/+/+/+/+/+/state`) and derives both by absolute index, so adding an
account, a vendor, a model or another device requires no flow changes — as long
as the topic is exactly six segments before the channel.

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
topic readwrite cosmoslab/<category>/<account>/<vendor>/<model>/<device>/#
EOF

docker kill -s HUP mosquitto
```

> ✅ **It is ONE line. It used to be two, and that trap is now gone.**
>
> The ingestion hub account `nodered` holds a single grant, `cosmoslab/#`, which
> covers the entire tree forever. Before the six-segment scheme each account had
> its own root (`walnut/#`, `acme/#`, …) and `nodered` needed a second, separate
> grant on every new one — which was easy to forget and **failed in total
> silence**, because a denied SUBSCRIBE is not logged at any level and a denied
> PUBLISH only at `debug`. Putting every account under `cosmoslab/` removed the
> failure mode rather than documenting it.
>
> Still true and still worth knowing: **a denied publish is silent at QoS 0.**
> Verify a new device by InfluxDB row counts, never by a dashboard, which renders
> its last good value indefinitely. Turn on `log_type debug` while editing ACLs
> and take it back off afterwards — that is the only level denials appear at.

---

## 5. Register the account

This is the step that is easy to miss, because the device works without it —
its own readings flow, but every **outdoor** provider (AirNow, OpenWeatherMap,
Google Air Quality, PurpleAir) is polled per account and needs the account's
location.

Publish a **retained** JSON message to `<…>/<device>/config/home`:

```bash
mosquitto_pub -h <broker> -p 8883 --capath /etc/ssl/certs \
  -u <account>_<device> -P '<password>' -r \
  -t 'cosmoslab/<category>/<account>/<vendor>/<model>/<device>/config/home' -m '{
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
2. Broker: `mosquitto_sub -h <broker> -p <port> -u <account>_<device> -P '<pw>' -t 'cosmoslab/<category>/<account>/<vendor>/<model>/<device>/state' -v`
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
every 5 s (a flat rate since 1.3.15-mqtt — it was 60 s, or 15 s when the air
band was elevated), so five minutes of silence raises a Gotify alert, and
recovery clears it. It is edge-triggered, so a unit that stays down does not
repeat.

Note the watchdog watches **MQTT**, not InfluxDB. Node-RED decimates to a slower
fixed rate on the way into the database, so the stored cadence is deliberately
not the published one — do not retune this threshold against what InfluxDB shows.

Its blind spot is deliberate and worth knowing: it runs *inside* Node-RED, so it
cannot report Node-RED itself being down. That needs an external check (an
Uptime Kuma monitor on `:1880`).

---

## 8. Adding a second account

No flow changes, no cloned tabs:

1. Broker account + **one** ACL line (§4): the account's own `user`/`topic`
   pair. `nodered` already holds `cosmoslab/#` and needs nothing added.
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

OTA is a **pull**: you publish a firmware URL to `<…>/<device>/command/ota`
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
