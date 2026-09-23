"""Unittest-style tests for the fixture API (is_test via test_ prefix)."""

import unittest

from pyapp.helpers import format_total
from pyapp.server import format_greeting, sanitize


class ServerHelpersTest(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(sanitize("-abc"), "abc")
        self.assertEqual(sanitize("ok"), "ok")

    def test_greeting(self):
        self.assertEqual(format_greeting("ada"), "hello ada")

    def test_total(self):
        self.assertEqual(format_total(1000), "total=12.00")


if __name__ == "__main__":
    unittest.main()
