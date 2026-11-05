import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import balance

PAYLOAD = {
    "code": 0,
    "msg": "",
    "data": {"biz_code": 0, "biz_msg": "", "biz_data": {
        "normal_wallets": [{"currency": "USD", "balance": "4.5000000", "token_estimation": "0"}],
        "bonus_wallets": [{"currency": "USD", "balance": "0.5000000", "token_estimation": "0"}],
        "total_costs": [{"currency": "USD", "amount": "0.2500000"}],
    }},
}


class ParseTests(unittest.TestCase):
    def test_parses_wallets_and_costs(self):
        result = balance.parse_response(PAYLOAD, now=100.0)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["balance"], 5.0)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["total_cost"], 0.25)
        self.assertEqual(result["ts"], 100.0)

    def test_rejects_missing_wallets(self):
        self.assertEqual(balance.parse_response({"code": 0}, now=1)["status"], "failed")

    def test_money(self):
        self.assertEqual(balance.money("USD", 4.5), "$4.50")
        self.assertEqual(balance.money("CNY", 12.0), "¥12.00")
        self.assertEqual(balance.money("USD", None), "—")


class GrantTests(unittest.TestCase):
    def test_reads_deepseek_account_grant(self):
        path = Path(tempfile.mkdtemp()) / ".credentials.yaml"
        path.write_text(
            "version: 1\nrefs:\n  OTHER: abc\nrecords:\n"
            "  deepseek-account-platform/device:\n    kind: grant\n    payload:\n      id: uuid\n"
            "  deepseek-account-platform/default:\n    kind: grant\n    payload:\n"
            "      version: 1\n      token: secret-token\n"
            "      issuer: https://platform.deepseek.com\n", encoding="utf-8")
        self.assertEqual(balance.read_grant(path),
                         ("https://platform.deepseek.com", "secret-token"))

    def test_missing_file(self):
        self.assertIsNone(balance.read_grant(Path(tempfile.mkdtemp()) / "none.yaml"))


class FetchTests(unittest.TestCase):
    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_fetch_ready(self):
        seen = []

        def opener(request, timeout=0):
            seen.append(request.full_url)
            return self.Response(PAYLOAD)

        result = balance.fetch_balance(now=10.0, opener=opener,
                                       grant=("https://platform.deepseek.com", "token"))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(seen, ["https://platform.deepseek.com/api/v0/users/get_user_summary"])

    def test_fetch_unauthorized(self):
        def opener(request, timeout=0):
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)

        result = balance.fetch_balance(opener=opener,
                                       grant=("https://platform.deepseek.com", "token"))
        self.assertEqual(result["status"], "auth")

    def test_fetch_network_failure(self):
        def opener(request, timeout=0):
            raise urllib.error.URLError("offline")

        result = balance.fetch_balance(opener=opener,
                                       grant=("https://platform.deepseek.com", "token"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "network")

    def test_missing_grant(self):
        result = balance.fetch_balance(grant=None)
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
