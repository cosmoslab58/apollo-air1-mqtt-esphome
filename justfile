# Apollo AIR-1 — build / flash / OTA task runner.
#
# Updates go over MQTT: the device downloads its own firmware, so you never need
# its IP or hostname. (Push OTA would need `apollo-air-1-<mac>.local`, courtesy
# of name_add_mac_suffix — not something you have in the field.) Broker creds
# come from Integrations/ESPHome/secrets.yaml, the same file the firmware
# compiles against; no env vars needed.
#
#   just                 list all recipes
#   just ota             bump patch, build, publish, trigger the OTA over MQTT
#   just ota minor       (or major, or an explicit 1.4.0)
#   just check           compare device firmware vs the repo
#   just logs            live device logs over MQTT (no address needed)
#   just install         first-time USB flash (makes the unit OTA-capable)
#
# ESPHome is not installed on this box, so it runs through uvx by default,
# pinned to the version that produced the current build. Override if you have it
# installed locally or run it in docker:
#   ESPHOME="esphome" just build
#   ESPHOME="docker run --rm -v $PWD:/config esphome/esphome" just build

set shell := ["bash", "-euo", "pipefail", "-c"]

esphome     := env_var_or_default("ESPHOME", "uvx esphome@2026.7.2")
yaml        := "Integrations/ESPHome/apollo-air1-mqtt.yaml"
build_dir   := justfile_directory() / "Integrations/ESPHome/.esphome/build/apollo-air-1/build"
ota_bin     := build_dir / "firmware.ota.bin"
port        := env_var_or_default("AIR1_PORT", "/dev/ttyACM0")
secrets     := justfile_directory() / "Integrations/ESPHome/secrets.yaml"

# list all recipes (default)
default:
    @just --list

# validate the config without building
config:
    {{esphome}} config {{yaml}}

# compile the firmware
build:
    {{esphome}} compile {{yaml}}
    @printf 'ota image: %s bytes (partition is 1835008)\n' "$(stat -c%s {{ota_bin}})"

# bump the version substitution (patch | minor | major | X.Y.Z)
bump how="patch":
    python3 scripts/bump.py {{how}}

# bump, build, publish, and trigger the OTA over MQTT
ota how="patch": (bump how) build
    python3 scripts/air1-ota.py

# publish + trigger the CURRENT build with no bump (skipped if versions match)
push *args:
    python3 scripts/air1-ota.py {{args}}

# publish the image and print the MQTT command without sending it
dry-run: build
    python3 scripts/air1-ota.py --dry-run

# compare the version the device reports against this repo
check:
    python3 scripts/air1-ota.py --check

# live device logs over MQTT — works without knowing the device's address
logs:
    #!/usr/bin/env bash
    set -euo pipefail
    topic="$(grep -oP '^\s+mqtt_topic:\s*\K\S+' {{yaml}})"
    host="$(grep -oP '^mqtt_broker:\s*\K.*' {{secrets}} | tr -d '"')"
    user="$(grep -oP '^mqtt_username:\s*\K.*' {{secrets}} | tr -d '"')"
    pass="$(grep -oP '^mqtt_password:\s*\K.*' {{secrets}} | tr -d '"')"
    echo "subscribing to $topic/debug on $host:8883"
    mosquitto_sub -h "$host" -p 8883 --capath /etc/ssl/certs \
      -u "$user" -P "$pass" -t "$topic/debug" -v

# watch the combined sensor snapshot
watch:
    #!/usr/bin/env bash
    set -euo pipefail
    topic="$(grep -oP '^\s+mqtt_topic:\s*\K\S+' {{yaml}})"
    host="$(grep -oP '^mqtt_broker:\s*\K.*' {{secrets}} | tr -d '"')"
    user="$(grep -oP '^mqtt_username:\s*\K.*' {{secrets}} | tr -d '"')"
    pass="$(grep -oP '^mqtt_password:\s*\K.*' {{secrets}} | tr -d '"')"
    mosquitto_sub -h "$host" -p 8883 --capath /etc/ssl/certs \
      -u "$user" -P "$pass" -t "$topic/state" -v

# FIRST-TIME USB flash — required once to get the pull-OTA agent onto the device
install:
    {{esphome}} run {{yaml}} --device {{port}}

# push over the network instead (needs the device's address; bench/recovery only)
install-net device:
    {{esphome}} run {{yaml}} --device {{device}}

# serial console
monitor:
    {{esphome}} logs {{yaml}} --device {{port}}

# remove build outputs
clean:
    rm -rf Integrations/ESPHome/.esphome/build
