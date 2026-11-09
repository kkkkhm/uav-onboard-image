import copy
import unittest

from tools.images.public_firstboot import validate_access, wifi_profile


class PublicAccessTests(unittest.TestCase):
    def data(self):
        return {"version": 1, "password": "example-test-only", "wifi": {"ssid": "Test WiFi", "password": "test-only-123"}}

    def test_empty_template_and_line_breaks_are_rejected(self):
        for password in ("", "short", "valid-length\nroot:bad", "null\x00password"):
            data = self.data()
            data["password"] = password
            with self.subTest(password=repr(password)), self.assertRaises(ValueError):
                validate_access(data)

    def test_ethernet_and_wpa_personal_configurations(self):
        data = self.data()
        self.assertEqual(validate_access(data), data)
        data["wifi"] = {"ssid": "", "password": ""}
        validate_access(data)
        data["wifi"] = {"ssid": "x", "password": "a" * 64}
        validate_access(data)
        for wifi in ({"ssid": "x", "password": "short"}, {"ssid": "", "password": "unused-password"},
                     {"ssid": "x\nnew", "password": "test-only-123"}):
            data["wifi"] = wifi
            with self.assertRaises(ValueError):
                validate_access(data)

    def test_profile_uses_wlan0_and_never_includes_login_password(self):
        data = validate_access(self.data())
        profile = wifi_profile(data["wifi"])
        self.assertIn("interface-name=wlan0", profile)
        self.assertIn("autoconnect=true", profile)
        self.assertNotIn(data["password"], profile)
        self.assertNotIn("wlan1", profile)
        self.assertNotEqual(profile, wifi_profile(data["wifi"]))


if __name__ == "__main__":
    unittest.main()
