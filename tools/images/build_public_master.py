"""Build a public image from verified upstream OS plus allowlisted ARM64 software."""
import argparse
import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.images.image_utils import extract, run
from tools.uav_config.config import defaults
from tools.uav_config.system import mesh_files, uart_files
from tools.uav_telemetry.config import build

CACHE = REPO / "images/github-release/upstream"
STAGE = REPO / "images/github-release/public-stage"
ROOT, SOFTWARE = STAGE / "root", STAGE / "software"
OUTPUT = REPO / "images/uav-rpi4-public-20261109.img"


def put(name, text, mode=0o644):
    path = ROOT / name.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def prepare():
    STAGE.mkdir(parents=True, exist_ok=False)
    provenance = json.loads((CACHE / "upstream.json").read_text())
    compressed = CACHE / provenance["url"].split("/")[-1]
    digest = hashlib.sha256()
    with compressed.open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1048576), b""):
            digest.update(data)
    assert digest.hexdigest() == provenance["sha256"]
    upstream = STAGE / "upstream.img"
    with lzma.open(compressed, "rb") as source, upstream.open("xb") as target:
        shutil.copyfileobj(source, target, 8 * 1048576)
    with upstream.open("rb") as stream:
        mbr = stream.read(512)
    assert mbr[510:512] == b"\x55\xaa"
    parts = []
    for index in range(2):
        offset, size = struct.unpack_from("<II", mbr, 446 + 16 * index + 8)
        parts.append({"offset": offset * 512, "size": size * 512})
    for part, name in zip(parts, ("boot.img", "root.img")):
        extract(upstream, part["offset"], part["size"], STAGE / name)
    root = STAGE / "root.img"
    with root.open("r+b") as stream:
        stream.truncate(8 * 1024**3)
    run("e2fsck", "-f", "-y", root, allowed=(0, 1))
    run("resize2fs", root)
    (STAGE / "layout.json").write_text(json.dumps({"parts": parts, "upstream": provenance}, indent=2))
    print("Public base prepared", flush=True)


def copy_venv(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for name in ("bin", "include", "lib", "lib64", "pyvenv.cfg"):
        item = source / name
        destination = target / name
        if item.is_symlink():
            destination.symlink_to(item.readlink())
        elif item.is_dir():
            shutil.copytree(item, destination, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif item.is_file():
            shutil.copy2(item, destination)


def create_user():
    passwd = (ROOT / "etc/passwd").read_text()
    accounts = [line.split(":") for line in passwd.splitlines()]
    assert not any(row[0] == "uav" or row[2] == "1000" and row[0] != "pi" for row in accounts)
    passwd = "\n".join(":".join(row) for row in accounts if row[0] != "pi")
    put("/etc/passwd", passwd + "\nuav:x:1000:1000:UAV:/home/uav:/bin/bash\n")
    shadow = ROOT / "etc/shadow"
    shadow.write_text("\n".join(line for line in shadow.read_text().splitlines() if not line.startswith("pi:")) + "\nuav:!:1:0:99999:7:::\n")
    group = (ROOT / "etc/group").read_text().splitlines()
    assert not any(line.startswith("uav:") or line.split(":")[2] == "1000" and not line.startswith("pi:") for line in group)
    group = [line for line in group if not line.startswith("pi:")]
    groups = {"sudo", "dialout", "netdev", "gpio", "i2c", "spi", "adm", "video", "render"}
    for index, line in enumerate(group):
        fields = line.split(":")
        members = ["uav" if m == "pi" else m for m in fields[3].split(",") if m]
        if fields[0] in groups and "uav" not in members:
            members.append("uav")
        fields[3] = ",".join(members)
        group[index] = ":".join(fields)
    put("/etc/group", "\n".join(group) + "\nuav:x:1000:\n")
    gshadow = ROOT / "etc/gshadow"
    protected_groups = []
    for line in gshadow.read_text().splitlines():
        fields = line.split(":")
        if fields[0] == "pi":
            continue
        members = ["uav" if m == "pi" else m for m in fields[3].split(",") if m]
        if fields[0] in groups and "uav" not in members:
            members.append("uav")
        fields[3] = ",".join(members)
        protected_groups.append(":".join(fields))
    gshadow.write_text("\n".join(protected_groups) + "\nuav:!::\n")
    put("/etc/sudoers.d/90-uav", "uav ALL=(ALL:ALL) ALL\n", 0o440)
    old_home = ROOT / "home/pi"
    if old_home.exists():
        shutil.rmtree(old_home)
    home = ROOT / "home/uav"
    shutil.copytree(ROOT / "etc/skel", home, symlinks=True)
    copy_venv(SOFTWARE / "home/uav/uav-env", home / "uav-env")
    for directory, dirs, files in os.walk(home, followlinks=False):
        os.lchown(directory, 1000, 1000)
        for name in dirs + files:
            os.lchown(Path(directory) / name, 1000, 1000)


def customize(software_image):
    for directory in (ROOT, SOFTWARE):
        directory.mkdir(exist_ok=True)
    assert software_image.is_file()
    with software_image.open("rb") as stream:
        mbr = stream.read(512)
    offset, sectors = struct.unpack_from("<II", mbr, 446 + 16 + 8)
    run("mount", "-o", "loop", STAGE / "root.img", ROOT)
    try:
        assert (ROOT / "etc/rpi-issue").exists() and os.path.ismount(ROOT)
        assert (ROOT / "usr/bin/python3").readlink().name == "python3.13"
        run("mount", "-o", f"loop,ro,offset={offset * 512},sizelimit={sectors * 512}", software_image, SOFTWARE)
        try:
            # Only these Python environments cross from the private installation.
            # Account databases, logs, network profiles and user documents never do.
            for name in ("uav-config", "uav-telemetry"):
                target = ROOT / "opt" / name
                copy_venv(SOFTWARE / "opt" / name / "venv", target / "venv")
                (target / "tools").mkdir(exist_ok=True)
                (target / "tools/__init__.py").touch()
            create_user()
        finally:
            run("umount", SOFTWARE)
        for package in ("uav_config", "uav_telemetry"):
            destinations = ("uav-config", "uav-telemetry") if package == "uav_config" else ("uav-telemetry",)
            for name in destinations:
                shutil.copytree(REPO / "tools" / package, ROOT / "opt" / name / "tools" / package,
                                ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
        for name in ("uav-config", "uav-telemetry"):
            module = name.replace("-", "_")
            put("/usr/local/sbin/" + name,
                f'#!/bin/sh\ncd /opt/{name}\nexec /opt/{name}/venv/bin/python -m tools.{module} "$@"\n', 0o755)
        for name in ("firstboot.py", "public_firstboot.py"):
            put("/opt/uav-image/" + name, (REPO / "tools/images" / name).read_text())
        put("/usr/local/sbin/uav-setup", '#!/bin/sh\nexec /usr/bin/python3 /opt/uav-image/firstboot.py "$@"\n', 0o755)
        unit = (REPO / "scripts/uav-image-firstboot.service").read_text().replace(
            "/opt/uav-image/firstboot.py", "/opt/uav-image/public_firstboot.py")
        put("/etc/systemd/system/uav-image-firstboot.service", unit)
        put("/etc/systemd/system/uav-telemetry.service", (REPO / "scripts/uav-telemetry.service").read_text())
        node = defaults("uav1")
        for name, (text, mode) in mesh_files(node, "0e8d", "7612").items():
            put(name, text, mode)
        put("/etc/uav/config.json", json.dumps(node, indent=2) + "\n", 0o600)
        put("/etc/uav/telemetry.json", json.dumps(build(ROOT, (1, 2, 3, 4)), indent=2) + "\n")
        put("/var/lib/uav-image/needs-setup", "Choose a unique node.\n")
        put("/etc/cloud/cloud-init.disabled", "")
        put("/etc/machine-id", "")
        dbus_id = ROOT / "var/lib/dbus/machine-id"
        if dbus_id.exists() or dbus_id.is_symlink():
            dbus_id.unlink()
        dbus_id.parent.mkdir(parents=True, exist_ok=True)
        dbus_id.symlink_to("/etc/machine-id")
        put("/etc/hostname", "uav-setup\n")
        hosts = (ROOT / "etc/hosts").read_text()
        hosts = re.sub(r"^127\.0\.1\.1\s+.*\n?", "", hosts, flags=re.M)
        put("/etc/hosts", hosts.rstrip() + "\n127.0.1.1\tuav-setup\n")
        for key in (ROOT / "etc/ssh").glob("ssh_host_*"):
            key.unlink()
        for directory in ("etc/NetworkManager/system-connections", "etc/netplan"):
            path = ROOT / directory
            if path.exists():
                for file in path.iterdir():
                    if file.is_file():
                        file.unlink()
        for name in ("var/lib/NetworkManager/secret_key", "var/lib/systemd/random-seed"):
            path = ROOT / name
            if path.exists():
                path.unlink()
        for name in ("uav-mesh", "uav-telemetry"):
            put("/etc/systemd/system/" + name + ".service.d/10-image-identity.conf",
                "[Unit]\nRequires=uav-image-firstboot.service\nAfter=uav-image-firstboot.service\nConditionPathExists=!/var/lib/uav-image/needs-setup\n")
        wants = ROOT / "etc/systemd/system/multi-user.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        for name in ("uav-image-firstboot", "uav-mesh", "uav-telemetry"):
            (wants / (name + ".service")).symlink_to("/etc/systemd/system/" + name + ".service")
        ssh_link = wants / "ssh.service"
        if not ssh_link.is_symlink():
            ssh_link.symlink_to("/usr/lib/systemd/system/ssh.service")
        for name in ("serial-getty@serial0.service", "serial-getty@ttyAMA0.service", "serial-getty@ttyS0.service"):
            path = ROOT / "etc/systemd/system" / name
            if not path.is_symlink():
                path.symlink_to("/dev/null")
        # Replace the upstream placeholder-user wizard with uav-access.json setup.
        userconfig = ROOT / "etc/systemd/system/userconfig.service"
        if not userconfig.exists() and not userconfig.is_symlink():
            userconfig.symlink_to("/dev/null")
        for name in ("usr/sbin/iw", "usr/bin/nmcli", "usr/bin/whiptail", "usr/sbin/sshd", "usr/sbin/chpasswd"):
            assert (ROOT / name).exists(), name
        assert not list((ROOT / "etc/NetworkManager/system-connections").glob("*.nmconnection"))
        assert next(x for x in (ROOT / "etc/shadow").read_text().splitlines() if x.startswith("uav:")).split(":")[1] == "!"
        # Run the ARM interpreter and native wheels under emulation, not the host Python.
        emulator = ROOT / "usr/bin/qemu-aarch64-static"
        shutil.copy2("/usr/bin/qemu-aarch64-static", emulator)
        try:
            result = run("chroot", ROOT, "/usr/bin/qemu-aarch64-static", "/opt/uav-telemetry/venv/bin/python",
                         "-c", "import sys,serial,fastcrc,lxml.etree; from pymavlink import mavutil; print(sys.version); print('ARM64 MAVLink dependencies OK')")
            (STAGE / "arm-runtime-validation.txt").write_text(result)
        finally:
            emulator.unlink()
        print("PUBLIC_CONTENT_VALIDATED", flush=True)
    finally:
        run("umount", ROOT)
    boot = STAGE / "boot.img"
    boot_text = subprocess.check_output(["mtype", "-i", str(boot), "::config.txt"], text=True)
    cmdline = subprocess.check_output(["mtype", "-i", str(boot), "::cmdline.txt"], text=True)
    boot_text, cmdline = uart_files(boot_text, cmdline, node["uart"])
    words = [w for w in cmdline.split() if not w.startswith(("ds=nocloud", "cfg80211.ieee80211_regdom="))]
    cmdline = " ".join(words + ["cfg80211.ieee80211_regdom=CN"]) + "\n"
    templates = {"config.txt": boot_text, "cmdline.txt": cmdline,
                 "uav-node.json": json.dumps({"version": 1, "node_id": None, "serial_device": "/dev/serial0", "baud": 115200, "peers": [1, 2, 3, 4]}, indent=2) + "\n",
                 "uav-access.json": json.dumps({"version": 1, "password": "", "wifi": {"ssid": "", "password": ""}}, indent=2) + "\n"}
    for name, text in templates.items():
        path = STAGE / name
        path.write_text(text)
        run("mcopy", "-o", "-i", boot, path, "::" + name)
    listing = run("mdir", "-b", "-i", boot, "::/").splitlines()
    for name in ("user-data", "meta-data", "network-config", "firstrun.sh", "userconf", "userconf.txt", "ssh"):
        if "::/" + name in listing:
            run("mdel", "-i", boot, "::" + name)
    run("fsck.fat", "-n", boot)
    run("e2fsck", "-f", "-y", STAGE / "root.img", allowed=(0, 1))
    print("Public customization complete", flush=True)


def assemble():
    layout = json.loads((STAGE / "layout.json").read_text())
    parts = layout["parts"]
    with (STAGE / "upstream.img").open("rb") as source:
        prefix = bytearray(source.read(parts[0]["offset"]))
    root_size = (STAGE / "root.img").stat().st_size
    struct.pack_into("<I", prefix, 446 + 16 + 12, root_size // 512)
    with OUTPUT.open("xb") as out:
        out.write(prefix)
        for part, name in zip(parts, ("boot.img", "root.img")):
            out.seek(part["offset"])
            with (STAGE / name).open("rb") as source:
                shutil.copyfileobj(source, out, 8 * 1048576)
    compressed = OUTPUT.with_suffix(".img.gz")
    digest = hashlib.sha256()
    with OUTPUT.open("rb") as source, compressed.with_suffix(".gz.partial").open("xb") as out:
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, compresslevel=3, mtime=0) as archive:
            for data in iter(lambda: source.read(8 * 1048576), b""):
                digest.update(data)
                archive.write(data)
    temp = compressed.with_suffix(".gz.partial")
    verify = hashlib.sha256()
    with gzip.open(temp, "rb") as stream:
        for data in iter(lambda: stream.read(8 * 1048576), b""):
            verify.update(data)
    assert verify.hexdigest() == digest.hexdigest()
    temp.rename(compressed)
    zipped = hashlib.sha256()
    with compressed.open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1048576), b""):
            zipped.update(data)
    report = {"upstream": layout["upstream"], "software_paths_allowlisted": True,
              "private_account_databases_copied": False, "private_wifi_profiles_copied": False,
              "private_logs_or_documents_copied": False, "login_initially_locked": True,
              "arm_runtime_imports_verified": True, "physical_boot_tested": False,
              "image_bytes": OUTPUT.stat().st_size, "compressed_bytes": compressed.stat().st_size,
              "sha256": digest.hexdigest(), "compressed_sha256": zipped.hexdigest()}
    (REPO / "images/public-build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    (REPO / "images/SHA256SUMS-public.txt").write_text(digest.hexdigest() + "  " + OUTPUT.name + "\n" + zipped.hexdigest() + "  " + compressed.name + "\n")
    print("PUBLIC_IMAGE_COMPLETE\n" + json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "customize", "assemble"))
    parser.add_argument("--software-image", type=Path, default=REPO / "images/uav-rpi4-master-20261109.img")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as Linux root to mount working images")
    if args.phase == "customize":
        customize(args.software_image)
    else:
        {"prepare": prepare, "assemble": assemble}[args.phase]()
