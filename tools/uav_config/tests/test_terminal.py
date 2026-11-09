import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "posix" and shutil.which("whiptail"), "需要Linux whiptail终端")
class TerminalTests(unittest.TestCase):
    def test_real_menu_arrows_input_cancel_and_confirmation(self):
        import fcntl
        import pty
        import select
        import struct
        import termios
        import time

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        script = """
import json
from tools.uav_config.ui import Dialog
d=Dialog()
a=d.menu('MENU_READY', [('node','Node'),('exit','Exit')])
b=d.input('INPUT_READY','uav1')
c=d.input('CANCEL_READY','unchanged')
e=d.confirm('CONFIRM_READY')
print('RESULT_JSON='+json.dumps([a,b,c,e]), flush=True)
"""
        env = dict(os.environ, TERM="xterm", LANG="C.UTF-8", LC_ALL="C.UTF-8")
        def attach_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        child = subprocess.Popen([sys.executable, "-c", script], stdin=slave, stdout=slave, stderr=slave,
                                 env=env, preexec_fn=attach_terminal)
        os.close(slave)
        output = b""

        def wait_for(marker):
            nonlocal output
            deadline = time.monotonic()+8
            while marker not in output and time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    try:
                        output += os.read(master, 65536)
                    except OSError:
                        break
            if marker not in output:
                Path('/tmp/uav-config-terminal-test.log').write_bytes(output)
                self.fail("未出现终端标记 " + marker.decode() + "; child=" + str(child.poll()))
            time.sleep(0.15)

        try:
            wait_for(b"MENU_READY")
            os.write(master, b"\x1b[B")
            time.sleep(0.1)
            os.write(master, b"\r")
            wait_for(b"INPUT_READY")
            os.write(master, b"\x15uav3\r")
            wait_for(b"CANCEL_READY")
            os.write(master, b"\t\t\r")
            wait_for(b"CONFIRM_READY")
            os.write(master, b"\t\r")
            wait_for(b"RESULT_JSON=")
            child.wait(timeout=5)
            while select.select([master], [], [], 0.1)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
            line = output.split(b"RESULT_JSON=", 1)[1].splitlines()[0].decode().strip()
            self.assertEqual(json.loads(line), ["exit", "uav3", None, False])
            self.assertEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            os.close(master)


if __name__ == "__main__":
    unittest.main()
