"""Read OpenRouter balance without persisting credentials or API responses."""
import json
import hashlib
import math
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    try:
        return (home / "codex-router" / "openrouter-api-key.secret").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def fetch_credits():
    try:
        key = api_key()
        if not key:
            return {"status": "missing"}
        request = Request("https://openrouter.ai/api/v1/credits",
                          headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=8) as response:
            data = json.loads(response.read(65537))["data"]
        values = [data["total_credits"], data["total_usage"]]
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Invalid credit values")
        return {"status": "ready", "remaining": values[0] - values[1], "ts": time.time(),
                "purchased": values[0], "spent": values[1],
                "account": hashlib.sha256(key.encode()).hexdigest()[:20]}
    except HTTPError as exc:
        return {"status": "auth" if exc.code in (401, 403) else "error"}
    except Exception:
        return {"status": "error"}


def credits_text(result, now=None):
    now = time.time() if now is None else now
    if "remaining" in result:
        stale = result.get("status") != "ready" or now - result["ts"] > 600
        stamp = time.strftime("%H:%M", time.localtime(result["ts"]))
        return f"OpenRouter 残り ${result['remaining']:.4f} USD · {stamp}" + (" · 更新待ち" if stale else "")
    return "OpenRouter · " + {"missing": "キー未設定", "auth": "残高取得の権限・認証を確認",
                               "error": "残高取得失敗", "loading": "残高取得中"}.get(result.get("status"), "残高未取得")
