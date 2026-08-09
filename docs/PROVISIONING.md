# Provisioning an AIR-1 from scratch

Taking a stock Apollo AIR-1 to a unit whose readings appear in InfluxDB and
Grafana, assuming no prior knowledge of this deployment.

The **device** side is documented in depth in
[`Integrations/ESPHome/apollo-air1-mqtt-README.md`](../Integrations/ESPHome/apollo-air1-mqtt-README.md)
— hardware, publish cadence, LED behaviour, CO2 calibration, OTA. This file
covers what that one does not: the **server side**, and how a unit is attached
to a tenant.

---

## 1. Secrets

ESPHome reads `secrets.yaml` (gitignored). Create it next to the YAML:

```yaml
wifi_ssid: "…"
wifi_password: "…"
mqtt_broker: "mqtt.example.com"
mqtt_username: "air1_<tenant>"
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

## 2. Substitutions — the tenant lives here

In `Integrations/ESPHome/apollo-air1-mqtt.yaml`:

```yaml
substitutions:
  name: apollo-air-1          # ESPHome node name; must be unique on the network
  mqtt_topic: <tenant>/air1   # ← the tenant root
  mqtt_port: "8883"
```

**`mqtt_topic` is the whole tenancy story.** Everything derives from it:

```
<tenant>/air1/state           combined JSON snapshot — the data path
<tenant>/air1/status          online/offline (birth + last will)
<tenant>/air1/config/home     retained tenant config — see §5
<tenant>/air1/command/ota     pull-OTA trigger
```

The **first segment is the tenant id**. Node-RED subscribes with a wildcard in
that slot and derives the tenant from it, so adding a tenant requires no flow
changes.

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
mosquitto_passwd -b /path/to/password.txt air1_<tenant> '<password>'

cat >> /path/to/acl.conf <<'EOF'

user air1_<tenant>
topic readwrite <tenant>/air1/#
EOF

docker kill -s HUP mosquitto
```

> **A denied publish is silent.** At QoS 0 the client is told nothing, and
> Mosquitto logs ACL denials at **`debug` level only** — so a wrong topic rule
> makes data vanish with no evidence anywhere. Add `log_type debug` temporarily
> while editing ACLs, confirm you see `Denied PUBLISH` for something that should
> fail, then remove it (debug is ~25k lines/hour).
>
> Also: **`log_type` takes one value per line.** `log_type error warning notice
> information` looks right and silently applies only `error`.

---

## 5. Register the tenant

This is the step that is easy to miss, because the device works without it —
its own readings flow, but every **outdoor** provider (AirNow, OpenWeatherMap,
Google Air Quality, PurpleAir) is polled per tenant and needs the tenant's
location.

Publish a **retained** JSON message to `<tenant>/air1/config/home`:

```bash
mosquitto_pub -h <broker> -p 8883 --capath /etc/ssl/certs \
  -u air1_<tenant> -P '<password>' -r \
  -t '<tenant>/air1/config/home' -m '{
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
2. Broker: `mosquitto_sub -t '<tenant>/air1/state' -v` shows one JSON snapshot.
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

## 8. Adding a second tenant

No flow changes, no cloned tabs:

1. Broker account + one ACL line (§4).
2. `mqtt_topic: <their-tenant>/air1` in their unit's substitutions, then flash.
3. Publish their retained `config/home` (§5).
4. Point their Grafana dashboard at `tenant == "<their id>"`.

---

## 9. Updating over the air

```bash
just ota          # bump patch, build, publish, trigger over MQTT
just check        # compare device version vs repo, without pushing
```

OTA is a **pull**: you publish a firmware URL to `<tenant>/air1/command/ota` and
the device fetches it itself, so it works for units behind a VLAN or anywhere
mDNS does not reach — no IP or hostname needed.
