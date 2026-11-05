"""DeepSeek Platformの残高を読み取り専用で取得する。

認証情報はDSHの保存済みグラントをメモリ上で読むだけで、保存も表示もしない。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from core import default_home

KEY = "deepseek-account-platform/default"
DEFAULT_ORIGIN = "https://platform.deepseek.com"
TIMEOUT = 20
_MISSING = object()


def credential_file():
    return default_home() / ".credentials.yaml"


def _scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_grant(path=None):
    """credentials.yamlから残高取得用の(issuer, token)を読む。"""
    path = Path(path or credential_file())
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    inside = False
    origin = None
    token = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if not inside:
            if indent == 2 and stripped.rstrip(":") == KEY:
                inside = True
            continue
        if indent <= 2:
            break
        if stripped.startswith("token:"):
            token = _scalar(stripped.split(":", 1)[1])
        elif stripped.startswith("issuer:"):
            origin = _scalar(stripped.split(":", 1)[1])
    if not token or not origin:
        return None
    return origin.rstrip("/"), token


def _wallets(payload):
    body = payload.get("data") if isinstance(payload, dict) else None
    body = body.get("biz_data") if isinstance(body, dict) else None
    if not isinstance(body, dict):
        return None
    normal = body.get("normal_wallets")
    bonus = body.get("bonus_wallets")
    if not isinstance(normal, list) or not isinstance(bonus, list):
        return None
    totals = {}
    for wallet in normal + bonus:
        if not isinstance(wallet, dict):
            continue
        currency = wallet.get("currency")
        try:
            amount = float(wallet.get("balance"))
        except (TypeError, ValueError):
            continue
        if currency not in ("USD", "CNY"):
            continue
        totals[currency] = totals.get(currency, 0.0) + amount
    costs = {}
    for row in body.get("total_costs") or []:
        if not isinstance(row, dict):
            continue
        try:
            amount = float(row.get("amount"))
        except (TypeError, ValueError):
            continue
        currency = row.get("currency")
        if currency in ("USD", "CNY"):
            costs[currency] = costs.get(currency, 0.0) + amount
    return totals, costs


def parse_response(payload, now=None):
    parsed = _wallets(payload)
    if parsed is None:
        return {"status": "failed", "reason": "response"}
    totals, costs = parsed
    if not totals:
        return {"status": "failed", "reason": "response"}
    currency = "USD" if "USD" in totals else next(iter(totals))
    return {"status": "ready", "ts": time.time() if now is None else now,
            "balance": round(totals[currency], 6), "currency": currency,
            "total_cost": round(costs.get(currency, 0.0), 6)}


def fetch_balance(now=None, opener=None, grant=_MISSING):
    grant = read_grant() if grant is _MISSING else grant
    if not grant:
        return {"status": "unavailable", "reason": "credential"}
    origin, token = grant
    parts = urlsplit(origin)
    if (parts.scheme != "https" or parts.hostname != "platform.deepseek.com"
            or parts.port not in (None, 443) or parts.path not in ("", "/")):
        return {"status": "unavailable", "reason": "issuer"}
    request = urllib.request.Request(
        origin + "/api/v0/users/get_user_summary",
        headers={"x-dsh-auth-token": token, "accept": "application/json",
                 "user-agent": "DeepSeekMeter/0.1"},
    )
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=TIMEOUT) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                return {"status": "failed", "reason": f"http {status}"}
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"status": "auth" if error.code in (401, 403) else "failed",
                "reason": f"http {error.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "failed", "reason": "network"}
    except (ValueError, UnicodeError):
        return {"status": "failed", "reason": "response"}
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        return {"status": "failed", "reason": "response"}
    return parse_response(payload, now)


def money(currency, value):
    symbol = "¥" if currency == "CNY" else "$"
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{symbol}{value:,.0f}"
    return f"{symbol}{value:,.2f}"
