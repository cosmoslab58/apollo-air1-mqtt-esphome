#!/usr/bin/env python3
"""
air1-ota.py — push a firmware image to the Apollo AIR-1 over the air, via MQTT.

Why MQTT rather than `esphome run`: push OTA needs the device's address, and
name_add_mac_suffix makes that `apollo-air-1-<mac>.local` — not necessarily
something you have once the unit is deployed, especially across VLANs or where
mDNS doesn't cross subnets. The device dials out to the broker, so the broker is
always a valid rendezvous point regardless of where the unit sits or what IP it
picked up. The ESP32-C3 supplies the whole receive path (dual app partitions,
SHA-256 verification, automatic rollback), so this script only has to publish a
file and say "go".

Flow:
  1. copy firmware.ota.bin (+ an .md5 sidecar) into the static web root,
     under a version-stamped filename so the URL is unique per build
  2. preflight the URL for a direct 200 and matching Content-Length
  3. publish {"command":"ota","url":...,"version":...} to <topic>/command/ota
  4. wait for the device to reboot and report the new version back over MQTT

Broker credentials come from Integrations/ESPHome/secrets.yaml — the same file
the firmware compiles against. No env vars required; --flags override.

TLS verification is ON by default; pass --insecure for a broker whose cert
isn't system-trusted (a private CA, say), or --no-tls for a plaintext broker.

Examples
  ./air1-ota.py                       # publish + trigger the current build
  ./air1-ota.py --check               # just report repo vs device version
  ./air1-ota.py --bin path/to.ota.bin # publish a specific image
  ./air1-ota.py --dry-run             # publish the file, print the command, don't send
  ./air1-ota.py --subscribe state     # stream sensor snapshots
  ./air1-ota.py --get mqtt_topic      # resolve one config value
"""
import argparse
import hashlib
import json
import os
import shutil
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

import yaml

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
YAML = os.path.join(ROOT, "Integrations", "ESPHome", "apollo-air1-mqtt.yaml")
SECRETS = os.path.join(ROOT, "Integrations", "ESPHome", "secrets.yaml")
BUILD_BIN = os.path.join(ROOT, "Integrations", "ESPHome", ".esphome", "build",
                         "apollo-air-1", "build", "firmware.ota.bin")


# --------------------------------------------------------------------------- #
#  Config from the firmware's own files
# --------------------------------------------------------------------------- #
class _IgnoreTags(yaml.SafeLoader):
    """SafeLoader that tolerates ESPHome's custom tags.

    The device config is full of `!secret` and `!lambda`, which SafeLoader would
    reject. We only ever read plain scalars out of it, so mapping every `!tag` to
    None is enough and keeps us from having to model ESPHome's schema.
    """


_IgnoreTags.add_multi_constructor("!", lambda loader, suffix, node: None)


def _load_yaml(path, loader=yaml.SafeLoader):
    try:
        with open(path) as f:
            return yaml.load(f, Loader=loader) or {}
    except (OSError, yaml.YAMLError):
        return {}


def yaml_substitution(key, path=YAML):
    """Read a value out of the config's `substitutions:` block."""
    val = _load_yaml(path, _IgnoreTags).get("substitutions", {}).get(key)
    return None if val is None else str(val)


def read_secrets(path=SECRETS):
    """The ESPHome secrets file, parsed as the YAML it actually is.

    Worth using a real parser rather than line regexes: the CA certificate is a
    multi-line block scalar, which a naive `key: value` match reads as "|".
    """
    return {k: v for k, v in _load_yaml(path).items() if isinstance(v, (str, int, float))}


def semver(s):
    """'1.2.3-mqtt' -> (1,2,3) for comparison; non-numeric parts become 0."""
    if not s:
        return None
    core = s.split("-", 1)[0]
    nums = [int(p) if p.isdigit() else 0 for p in core.split(".")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


# --------------------------------------------------------------------------- #
#  MQTT
# --------------------------------------------------------------------------- #
def mqtt_client(args):
    import paho.mqtt.client as mqtt
    # paho 2.x requires an explicit callback API version (and warns on VERSION1);
    # 1.x has no such argument. The on_connect callbacks below take *a so they
    # tolerate the extra `properties` argument VERSION2 passes.
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):
        c = mqtt.Client()
    if args.user:
        c.username_pw_set(args.user, args.password)
    if not args.no_tls:
        c.tls_set(cert_reqs=ssl.CERT_NONE if args.insecure else ssl.CERT_REQUIRED,
                  ca_certs=args.cafile)
        c.tls_insecure_set(args.insecure)
    return c


def read_device_version(args, timeout=8):
    """The version the device is running.

    ESPHome retains entity state topics, so the `Apollo Firmware Version`
    text_sensor is readable instantly without waiting for a /state publish
    (the combined /state snapshot is deliberately not retained).

    Note the `sensor/` segment: ESPHome's MQTT layer publishes text sensors
    under the same component type as numeric ones, so the topic is
    `sensor/apollo_firmware_version/state`, not `text_sensor/...`.
    """
    topic = f"{args.topic}/sensor/apollo_firmware_version/state"
    got = {"v": None}
    ev = threading.Event()

    def on_connect(c, u, flags, rc, *a):
        c.subscribe(topic, qos=1)

    def on_message(c, u, msg):
        got["v"] = msg.payload.decode(errors="replace").strip()
        ev.set()

    c = mqtt_client(args)
    c.on_connect, c.on_message = on_connect, on_message
    c.connect(args.broker, args.broker_port, 30)
    c.loop_start()
    ev.wait(timeout)
    c.loop_stop()
    c.disconnect()
    return got["v"]


def trigger(args, url, version):
    payload = json.dumps({"command": "ota", "url": url, "version": version})
    topic = f"{args.topic}/command/ota"
    c = mqtt_client(args)
    print(f"  [mqtt] connect {args.broker}:{args.broker_port}")
    c.connect(args.broker, args.broker_port, 30)
    c.loop_start()
    # retain MUST stay False: a retained trigger replays on every reconnect, and
    # the device reconnects right after the OTA reboot -> endless reflash loop.
    info = c.publish(topic, payload, qos=1, retain=False)
    info.wait_for_publish(10)
    c.loop_stop()
    c.disconnect()
    print(f"  [mqtt] published to {topic}: {payload}")


def subscribe(args, suffix):
    """Stream everything published under <topic>/<suffix> until interrupted.

    Exists so the justfile doesn't have to re-derive the broker settings with a
    pile of greps and hand them to mosquitto_sub — which would also put the
    password in the process table where `ps` can see it.
    """
    topic = f"{args.topic}/{suffix}"

    def on_connect(c, u, flags, rc, *a):
        print(f"subscribed to {topic} on {args.broker}:{args.broker_port}", flush=True)
        c.subscribe(topic, qos=0)

    def on_message(c, u, msg):
        print(msg.payload.decode(errors="replace"), flush=True)

    c = mqtt_client(args)
    c.on_connect, c.on_message = on_connect, on_message
    c.connect(args.broker, args.broker_port, 30)
    try:
        c.loop_forever()
    except KeyboardInterrupt:
        print("\n(disconnected)")
        c.disconnect()


def wait_for_confirm(args, expected, timeout):
    print(f"confirm  : up to {timeout}s for the device to install and report back...")
    end = time.time() + timeout
    last = None
    while time.time() < end:
        v = None
        try:
            v = read_device_version(args, timeout=5)
        except Exception:
            pass
        if v and v != last:
            print(f"  [device] running {v}")
            last = v
        if v == expected:
            print(f"confirm  : device is up on {v} ✓")
            return True
        time.sleep(5)
    print("confirm  : no confirmation in the window — the device may still be "
          "downloading. Re-check with `just check`.")
    return False


# --------------------------------------------------------------------------- #
#  Publish
# --------------------------------------------------------------------------- #
def publish_file(binpath, publish_dir, version):
    """Copy the image + an .md5 sidecar into the web root under a versioned name."""
    os.makedirs(publish_dir, exist_ok=True)
    name = f"apollo-air-1-{version}.ota.bin"
    dest = os.path.join(publish_dir, name)
    shutil.copy2(binpath, dest)
    os.chmod(dest, 0o644)

    md5 = hashlib.md5(open(binpath, "rb").read()).hexdigest()
    with open(dest + ".md5", "w") as f:
        f.write(md5)
    os.chmod(dest + ".md5", 0o644)
    return name, md5


def check_url(url):
    """First-response status/length WITHOUT following redirects.

    Returns (status, content_length, location). status is None if the host
    couldn't be reached at all — a down web server or a wrong --base-url host
    is a normal enough mistake that it deserves a real message, not a traceback.
    """
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(url, timeout=15)
        cl = r.headers.get("Content-Length")
        status = r.status
        r.close()
        return status, (int(cl) if cl and cl.isdigit() else None), None
    except urllib.error.HTTPError as e:
        return e.code, None, (e.headers.get("Location") if e.headers else None)
    except (urllib.error.URLError, OSError) as e:
        print(f"url check: could not reach {url}: {e}")
        return None, None, None


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Publish + trigger an AIR-1 OTA over MQTT.")
    sec = read_secrets()
    p.add_argument("--bin", default=BUILD_BIN, help="path to firmware.ota.bin")
    p.add_argument("--check", action="store_true", help="report repo vs device version and exit")
    p.add_argument("--subscribe", metavar="SUFFIX",
                   help="stream <topic>/SUFFIX until interrupted (e.g. debug, state)")
    p.add_argument("--get", metavar="KEY",
                   help="print one resolved config value and exit (secrets.yaml, "
                        "then the config's substitutions). Used by the justfile.")
    p.add_argument("--dry-run", action="store_true", help="publish the file but don't trigger")
    # Hosting location comes from secrets.yaml (ota_publish_dir / ota_base_url);
    # an env var or an explicit flag overrides it. No defaults are baked in --
    # they'd only be right for one deployment.
    p.add_argument("--publish-dir",
                   default=os.environ.get("AIR1_PUBLISH_DIR") or
                   (os.path.expanduser(sec["ota_publish_dir"]) if sec.get("ota_publish_dir") else None))
    p.add_argument("--base-url",
                   default=os.environ.get("AIR1_BASE_URL") or sec.get("ota_base_url"))
    p.add_argument("--timeout", type=int, default=240, help="seconds to await confirmation")
    p.add_argument("--broker", default=os.environ.get("AIR1_BROKER") or sec.get("mqtt_broker"))
    p.add_argument("--broker-port", type=int, default=int(os.environ.get("AIR1_BROKER_PORT", 8883)))
    p.add_argument("--user", default=os.environ.get("AIR1_MQTT_USER") or sec.get("mqtt_username"))
    p.add_argument("--password", default=os.environ.get("AIR1_MQTT_PASS") or sec.get("mqtt_password"))
    # Topic root: a per-site mqtt_topic in secrets.yaml wins (it's what the
    # justfile passes to `esphome -s`), otherwise the config's own substitution.
    p.add_argument("--topic", default=os.environ.get("AIR1_TOPIC")
                   or sec.get("mqtt_topic") or yaml_substitution("mqtt_topic"))
    p.add_argument("--cafile", default=os.environ.get("AIR1_CAFILE"))
    p.add_argument("--insecure", action="store_true",
                   help="skip broker TLS verification (the broker has a real LE cert, so normally unnecessary)")
    p.add_argument("--no-tls", action="store_true", help="plaintext MQTT")
    args = p.parse_args()

    # --get resolves before the broker check: it's used to read config, and must
    # work (silently) even on a checkout with no secrets.yaml yet.
    if args.get:
        val = sec.get(args.get) or yaml_substitution(args.get)
        if val is None:
            return 1
        print(val)
        return 0

    if not (args.broker and args.topic):
        sys.exit(f"missing broker/topic — check {SECRETS} and the substitutions in {YAML}")

    if args.subscribe:
        subscribe(args, args.subscribe)
        return 0

    repo_version = yaml_substitution("version")
    if not repo_version:
        sys.exit(f"could not read the `version:` substitution from {YAML}")

    if args.check:
        print(f"repo     : {repo_version}")
        try:
            dv = read_device_version(args)
        except Exception as e:
            sys.exit(f"device   : could not reach the broker: {e}")
        if dv is None:
            print("device   : no retained version reported (is it online?)")
            return
        r, d = semver(repo_version), semver(dv)
        if r and d and r > d:
            print(f"device   : {dv}  ⚠ UPDATE AVAILABLE — run `just ota`")
        elif r and d and r < d:
            print(f"device   : {dv}  (ahead of the repo)")
        else:
            print(f"device   : {dv}  ✓ up to date")
        return

    if not (args.publish_dir and args.base_url):
        sys.exit("firmware hosting is not configured. Set ota_publish_dir and "
                 f"ota_base_url in {SECRETS} (see the .example file), or pass "
                 "--publish-dir / --base-url.")

    if not os.path.isfile(args.bin):
        sys.exit(f"no firmware at {args.bin} — run `just build` first")
    size = os.path.getsize(args.bin)

    name, md5 = publish_file(args.bin, args.publish_dir, repo_version)
    url = f"{args.base_url.rstrip('/')}/{name}"
    print(f"firmware : {args.bin} ({size} bytes, md5 {md5})")
    print(f"published: {os.path.join(args.publish_dir, name)}")
    print(f"url      : {url}")

    status, clen, loc = check_url(url)
    if status is None:
        sys.exit("url check: the firmware URL is unreachable from here — if the device "
                 "can't fetch it either, the OTA would fail mid-flight. Check the web "
                 "host, or pass --base-url.")
    if status != 200:
        extra = f" -> redirects to {loc}" if loc else ""
        sys.exit(f"url check: HTTP {status}{extra}. The device needs a direct 200 "
                 f"at this URL; fix the web root or pass --base-url.")
    if clen is not None and clen != size:
        sys.exit(f"url check: served {clen} bytes but the file is {size} (stale copy?)")
    print(f"url check: HTTP 200, {clen if clen is not None else '?'} bytes ✓")

    if args.dry_run:
        print("dry-run  : not triggering. Payload would be:")
        print("           " + json.dumps({"command": "ota", "url": url, "version": repo_version}))
        return

    try:
        current = read_device_version(args)
        if current == repo_version:
            print(f"note     : device already reports {current}; it will skip the flash.")
    except Exception:
        pass

    trigger(args, url, repo_version)
    wait_for_confirm(args, repo_version, args.timeout)
    print("done.")


if __name__ == "__main__":
    sys.exit(main() or 0)
