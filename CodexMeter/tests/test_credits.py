import io
import json
import unittest
from unittest.mock import patch, Mock

from credits import fetch_credits, credits_text


class CreditTests(unittest.TestCase):
    def fetch(self, data):
        stream = io.BytesIO(json.dumps({"data": data}).encode())
        opener = Mock()
        opener.open.return_value = stream
        with patch("credits.api_key", return_value="secret"), patch("credits.build_opener", return_value=opener):
            result = fetch_credits()
        self.assertNotIn("secret", str(result))
        return result

    def test_balance_and_negative(self):
        for used in (3, 12):
            result = self.fetch({"total_credits": 10, "total_usage": used})
            self.assertEqual(result["remaining"], 10 - used)

    def test_invalid_and_missing(self):
        self.assertEqual(self.fetch({"total_credits": True, "total_usage": 0})["status"], "error")
        self.assertEqual(self.fetch({"total_credits": float("nan"), "total_usage": 0})["status"], "error")
        with patch("credits.api_key", return_value=""), patch("credits.build_opener") as opener:
            self.assertEqual(fetch_credits()["status"], "missing")
            opener.assert_not_called()

    def test_stale_balance(self):
        result = {"status": "error", "remaining": 7, "ts": 1000}
        self.assertIn("更新待ち", credits_text(result, now=1001))
        self.assertIn("$7.0000", credits_text(result, now=1001))
