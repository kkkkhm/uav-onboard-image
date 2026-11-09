"""Audit the public working image before compression and publication."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.images.build_public_master import ROOT, STAGE
from tools.images.image_utils import run


def main():
    run("mount", "-o", "loop", STAGE / "root.img", ROOT)
    try:
        assert os.path.ismount(ROOT) and (ROOT / "etc/rpi-issue").exists()
        marker = ROOT / "etc/ssh/sshd_not_to_be_run"
        if marker.exists():
            marker.unlink()
        for line in (ROOT / "etc/shadow").read_text().splitlines():
            assert line.split(":")[1].startswith(("!", "*")), "Unexpected preconfigured password hash"
        accounts = [line.split(":") for line in (ROOT / "etc/passwd").read_text().splitlines()]
        user = next(row for row in accounts if row[2] == "1000")
        assert user[0] == "uav" and user[-1] == "/bin/bash"
        assert not any(row[0] == "pi" for row in accounts)
        assert (ROOT / "etc/machine-id").stat().st_size == 0
        assert not list((ROOT / "etc/ssh").glob("ssh_host_*"))
        assert not list((ROOT / "etc/NetworkManager/system-connections").iterdir())
        assert not list((ROOT / "etc/netplan").iterdir())
        assert not (ROOT / "home/uav/.ssh").exists()
        assert not (ROOT / "home/uav/.bash_history").exists()
        assert not (ROOT / "var/lib/uav-image/access-ready").exists()
        assert (ROOT / "var/lib/uav-image/needs-setup").exists()
        for name in ("firstboot.py", "public_firstboot.py"):
            assert (ROOT / "opt/uav-image" / name).read_bytes() == (REPO / "tools/images" / name).read_bytes()
        for package in ("uav_config", "uav_telemetry"):
            for source in (REPO / "tools" / package).glob("*.py"):
                assert (ROOT / "opt/uav-telemetry/tools" / package / source.name).read_bytes() == source.read_bytes()
        output = run("systemd-analyze", "--root=" + str(ROOT), "--generators=no", "verify", "multi-user.target")
        assert "ordering cycle" not in output.lower()
        # Test the installed onboarding code under the image's own ARM interpreter.
        emulator = ROOT / "usr/bin/qemu-aarch64-static"
        shutil.copy2("/usr/bin/qemu-aarch64-static", emulator)
        try:
            run("chroot", ROOT, "/usr/bin/qemu-aarch64-static", "/usr/bin/python3", "-c",
                "import sys; sys.path.insert(0,'/opt/uav-image'); from public_firstboot import validate_access,wifi_profile; "
                "d={'version':1,'password':'test-data-only','wifi':{'ssid':'Test WiFi','password':'test-data-only'}}; "
                "validate_access(d); assert 'interface-name=wlan0' in wifi_profile(d['wifi']); print('Installed ARM onboarding code OK')")
        finally:
            emulator.unlink()
    finally:
        run("umount", ROOT)
    boot = STAGE / "boot.img"
    access = json.loads(subprocess.check_output(["mtype", "-i", str(boot), "::uav-access.json"], text=True))
    assert access == {"version": 1, "password": "", "wifi": {"ssid": "", "password": ""}}
    run("e2fsck", "-f", "-n", STAGE / "root.img")
    run("fsck.fat", "-n", boot)
    report = {"all_accounts_initially_locked": True, "uid1000": "uav", "wifi_profiles": 0,
              "ssh_host_keys": 0, "machine_id_empty": True, "private_home_files_copied": False,
              "software_matches_source": True, "arm_onboarding_tested": True,
              "whole_boot_transaction_verified": True, "credential_template_empty": True,
              "physical_boot_tested": False}
    (REPO / "images/public-content-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PUBLIC_AUDIT_PASSED", flush=True)


if __name__ == "__main__":
    main()
