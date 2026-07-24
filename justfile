# Apollo AIR-1 — build / flash / OTA task runner.
#
# Updates go over MQTT: the device downloads its own firmware, so you never need
# its IP or hostname. (Push OTA would need `apollo-air-1-<mac>.local`, courtesy
# of name_add_mac_suffix — not necessarily something you have once the unit is
# deployed.) All configuration comes from Integrations/ESPHome/secrets.yaml, the
# same file the firmware compiles against; no env vars needed.
#
#   just                 list all recipes
#   just ota             bump patch, build, publish, trigger the OTA over MQTT
#   just ota minor       (or major, or an explicit 1.4.0)
#   just check           compare device firmware vs the repo
#   just logs            live device logs over MQTT (no address needed)
#   just watch           live sensor snapshots over MQTT
#   just install         first-time USB flash (makes the unit OTA-capable)
#
# ESPHome runs through uvx by default, pinned so builds stay reproducible and
# nothing needs installing. Override if you have it locally or run it in docker:
#   ESPHOME="esphome" just build
#   ESPHOME="docker run --rm -v $PWD:/config esphome/esphome" just build

set shell := ["bash", "-euo", "pipefail", "-c"]

esphome     := env_var_or_default("ESPHOME", "uvx esphome@2026.7.2")
yaml        := "Integrations/ESPHome/apollo-air1-mqtt.yaml"
build_dir   := justfile_directory() / "Integrations/ESPHome/.esphome/build/apollo-air-1/build"
ota_bin     := build_dir / "firmware.ota.bin"
port        := env_var_or_default("AIR1_PORT", "/dev/ttyACM0")

# Optional per-site MQTT topic root. If secrets.yaml defines mqtt_topic, it is
# passed to ESPHome as a substitution override, so the committed config keeps a
# neutral default and your topic tree lives in the gitignored secrets file.
site_topic  := `python3 scripts/air1-ota.py --get mqtt_topic 2>/dev/null || true`
sub         := if site_topic != "" { "-s mqtt_topic " + site_topic } else { "" }

# list all recipes (default)
default:
    @just --list

# validate the config without building
config:
    {{esphome}} {{sub}} config {{yaml}}

# compile the firmware
build:
    {{esphome}} {{sub}} compile {{yaml}}
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
    python3 scripts/air1-ota.py --subscribe debug

# watch the combined sensor snapshot
watch:
    python3 scripts/air1-ota.py --subscribe state

# FIRST-TIME USB flash — required once to get the pull-OTA agent onto the device
install:
    {{esphome}} {{sub}} run {{yaml}} --device {{port}}

# push over the network instead (needs the device's address; bench/recovery only)
install-net device:
    {{esphome}} {{sub}} run {{yaml}} --device {{device}}

# serial console
monitor:
    {{esphome}} {{sub}} logs {{yaml}} --device {{port}}

# remove build outputs
clean:
    rm -rf Integrations/ESPHome/.esphome/build
