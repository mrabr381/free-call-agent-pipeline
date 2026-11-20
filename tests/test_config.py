"""Config tests — both shipped configs load, bad configs fail clearly.

Run: python -m unittest discover -s tests -v
"""

import copy
import os
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.config import ConfigError, load_config, validate_config

ROOT = os.path.join(os.path.dirname(__file__), "..")


class ConfigTest(unittest.TestCase):
    def _good(self):
        with open(os.path.join(ROOT, "configs", "clinic.yaml"), encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_clinic_config_loads(self):
        cfg = load_config(os.path.join(ROOT, "configs", "clinic.yaml"))
        self.assertEqual(cfg["business"]["name"], "Bright Smile Dental Clinic")
        self.assertEqual(len(cfg["services"]), 5)
        self.assertEqual(len(cfg["staff"]), 3)

    def test_real_estate_config_loads(self):
        cfg = load_config(os.path.join(ROOT, "configs", "real-estate.yaml"))
        self.assertEqual(cfg["business"]["name"], "City Homes Realty")
        self.assertEqual(len(cfg["services"]), 4)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(os.path.join(ROOT, "configs", "nope.yaml"))

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("")
            path = fh.name
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_bad_yaml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("business: [unclosed\n")
            path = fh.name
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_missing_section(self):
        cfg = self._good()
        del cfg["services"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_missing_business_field(self):
        cfg = self._good()
        del cfg["business"]["phone"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_bad_hours_format(self):
        cfg = self._good()
        cfg["hours"]["monday"] = {"open": "9am", "close": "18:00"}
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_open_after_close(self):
        cfg = self._good()
        cfg["hours"]["monday"] = {"open": "18:00", "close": "09:00"}
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_duplicate_service(self):
        cfg = self._good()
        cfg["services"].append(copy.deepcopy(cfg["services"][0]))
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_negative_price(self):
        cfg = self._good()
        cfg["services"][0]["price"] = -5
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_empty_staff(self):
        cfg = self._good()
        cfg["staff"] = []
        with self.assertRaises(ConfigError):
            validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
