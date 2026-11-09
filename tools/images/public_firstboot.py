"""Initialize public-image access from a user-owned boot file, then assign the node."""
import configparser
import io
import json
import os
import subprocess
import uuid
from pathlib import Path


def validate_access(data):
    if not isinstance(data, dict) or set(data) != {"version", "password", "wifi"}:
        raise ValueError("uav-access.json requires version, password and wifi")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported access configuration version")
    password = data["password"]
    if not isinstance(password, str) or len(password) < 8 or any(c in password for c in "\r\n\x00"):
        raise ValueError("Choose a login password of at least 8 characters without line breaks")
    wifi = data["wifi"]
    if not isinstance(wifi, dict) or set(wifi) != {"ssid", "password"}:
        raise ValueError("wifi requires ssid and password")
    if any(not isinstance(wifi[k], str) or any(c in wifi[k] for c in "\r\n\x00") for k in wifi):
        raise ValueError("Wi-Fi values must be strings without line breaks")
    if len(wifi["ssid"].encode("utf-8")) > 32:
        raise ValueError("SSID exceeds 32 bytes")
    if wifi["ssid"] and not (8 <= len(wifi["password"]) <= 63 or
                             len(wifi["password"]) == 64 and all(c in "0123456789abcdefABCDEF" for c in wifi["password"])):
        raise ValueError("This template requires a WPA-Personal Wi-Fi password of 8-63 characters or 64 hex digits")
    if not wifi["ssid"] and wifi["password"]:
        raise ValueError("Specify an SSID or leave both Wi-Fi fields empty for Ethernet")
    return data


def wifi_profile(wifi):
    # GLib keyfiles decode backslash escapes and trim unescaped leading spaces.
    def escape(value):
        value = value.replace("\\", "\\\\").replace("\t", "\\t")
        return "\\s" * (len(value) - len(value.lstrip(" "))) + value.lstrip(" ")
    parser = configparser.ConfigParser(interpolation=None)
    parser["connection"] = {"id": "uav-debug", "uuid": str(uuid.uuid4()), "type": "wifi",
                            "interface-name": "wlan0", "autoconnect": "true", "permissions": ""}
    parser["wifi"] = {"mode": "infrastructure", "ssid": escape(wifi["ssid"])}
    parser["wifi-security"] = {"key-mgmt": "wpa-psk", "psk": escape(wifi["password"])}
    parser["ipv4"] = {"method": "auto"}
    parser["ipv6"] = {"method": "auto"}
    stream = io.StringIO()
    parser.write(stream, space_around_delimiters=False)
    return stream.getvalue()


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Root required")
    ready = Path("/var/lib/uav-image/access-ready")
    if not ready.exists():
        path = Path("/boot/firmware/uav-access.json")
        data = validate_access(json.loads(path.read_text(encoding="utf-8")))
        subprocess.run(["chpasswd"], input="uav:" + data["password"] + "\n", text=True, check=True)
        if data["wifi"]["ssid"]:
            target = Path("/etc/NetworkManager/system-connections/uav-debug.nmconnection")
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(wifi_profile(data["wifi"]))
            target.chmod(0o600)
        ready.parent.mkdir(parents=True, exist_ok=True)
        ready.write_text("User-owned login credentials initialized.\n")
        path.unlink()
        print("Access initialized; boot credential file removed.", flush=True)
    from firstboot import main as node_main
    node_main()


if __name__ == "__main__":
    main()
