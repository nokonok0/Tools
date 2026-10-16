from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

KINDS = ("routine", "implementation", "investigation")
BANNED_MODELS = frozenset(("gpt-6-astra", "gpt-6-sol"))
ROLES = {
    "routine": (("gpt-6-luna", "gpt-5.6-luna"), "low"),
    "implementation": (("gpt-6.1-sol", "gpt-5.6-sol"), "low"),
    "investigation": (("gpt-6.1-sol", "gpt-5.6-sol"), "low"),
}
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
QUESTIONS = {
    "task_kind": {
        "type": "choice",
        "instructions": "Classify only the current request. Ignore the topic of completed work.",
        "criteria": {
            "routine": "A simple conversational reply, explanation, translation, wording edit, summary, or a small clearly specified change.",
            "implementation": "Implement software, change application behavior, or set up a development environment.",
            "investigation": "Investigate a bug, diagnose a failure, design a system, or plan complex scientific research.",
        },
    },
    "high_impact": {
        "type": "noul",
        "instructions": "The CURRENT request requires destructive data changes, secrets handling, authentication changes, or financial transactions. Mere discussion of these topics does not qualify.",
    },
}


class RoutingError(Exception):
    pass


def check_allowed_model(model):
    if isinstance(model, str) and model.strip().lower() in BANNED_MODELS:
        raise RoutingError(f"{model} は使用禁止です。6.1 SolまたはLunaなどを指定してください")


def codex_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def read_json(path, maximum=16 * 1024 * 1024):
    if path.stat().st_size > maximum:
        raise RoutingError("設定ファイルが大きすぎます")
    return json.loads(path.read_text(encoding="utf-8"))


def overrides(arguments):
    found = {}
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        key = None
        value = None
        if arg in ("-m", "--model", "-c", "--config", "-p", "--profile"):
            if index + 1 >= len(arguments):
                raise RoutingError(f"{arg} の値がありません")
            index += 1
            value = arguments[index]
            key = {"-m": "model", "--model": "model", "-p": "profile",
                   "--profile": "profile", "-c": "config", "--config": "config"}[arg]
        elif arg.startswith(("--model=", "--profile=", "--config=")):
            key, value = arg[2:].split("=", 1)
        elif arg.startswith("-m") and len(arg) > 2:
            key, value = "model", arg[2:]
        elif arg.startswith("-c") and len(arg) > 2:
            key, value = "config", arg[2:]
        elif arg.startswith("-p") and len(arg) > 2:
            key, value = "profile", arg[2:]
        elif arg == "--oss" or arg.startswith("--local-provider"):
            found["provider"] = "oss"
        if key == "config" and "=" in value:
            field, raw = value.split("=", 1)
            if field.strip() in ("model", "model_reasoning_effort", "model_provider", "model_catalog_json"):
                try:
                    parsed = tomllib.loads(value)[field.strip()]
                except (tomllib.TOMLDecodeError, KeyError):
                    parsed = raw
                found[field.strip()] = str(parsed)
        elif key:
            found[key] = value
        index += 1
    return found


def configuration(tail):
    home = codex_home()
    settings = {}
    try:
        settings = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    options = overrides(tail)
    profile = options.get("profile")
    if profile:
        embedded = settings.get("profiles", {}).get(profile, {})
        settings.update(embedded)
        try:
            settings.update(tomllib.loads((home / f"{profile}.config.toml").read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
    for key in ("model_provider", "model_catalog_json"):
        if key in options:
            settings[key] = options[key]
    return settings, options


def catalog(settings):
    custom = settings.get("model_catalog_json")
    paths = [Path(custom).expanduser()] if custom else [codex_home() / "models_cache.json"]
    for path in paths:
        try:
            data = read_json(path)
        except FileNotFoundError:
            continue
        records = data.get("models", data.get("data", [])) if isinstance(data, dict) else data
        result = {}
        for model in records:
            name = model.get("slug") or model.get("model") or model.get("id")
            if not name or name in BANNED_MODELS or model.get("visibility") == "hide" or model.get("hidden") is True:
                continue
            levels = model.get("supported_reasoning_levels", model.get("supportedReasoningEfforts", []))
            efforts = [level.get("effort", level.get("reasoningEffort")) if isinstance(level, dict) else level
                       for level in levels]
            result[name] = {"efforts": [x for x in efforts if x in EFFORTS],
                            "default": model.get("default_reasoning_level", model.get("defaultReasoningEffort"))}
        if result:
            return result
    raise RoutingError("Codexのモデル一覧がありません。通常のcodexを一度起動するか、--modelで指定してください")


def native_codex():
    explicit = os.environ.get("CODEX_AUTO_CODEX_EXE")
    if explicit:
        executable = Path(explicit).expanduser()
        if executable.is_file() and executable.suffix.lower() not in (".cmd", ".bat", ".ps1"):
            return [str(executable)]
        raise RoutingError("CODEX_AUTO_CODEX_EXEには実行ファイルのパスを指定してください")
    for name in ("codex.exe", "codex"):
        executable = shutil.which(name)
        if executable and Path(executable).suffix.lower() not in (".cmd", ".bat", ".ps1"):
            return [executable]
    shim = shutil.which("codex.cmd") or shutil.which("codex.ps1")
    if shim:
        package = Path(shim).parent / "node_modules" / "@openai" / "codex"
        node = shutil.which("node.exe") or shutil.which("node")
        entry = package / "bin" / "codex.js"
        # Keep npm's installation metadata and signal handling without a shell.
        if node and entry.is_file():
            return [node, str(entry)]
        architecture = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
        candidates = list(package.glob(f"node_modules/@openai/codex-*/vendor/{architecture}-pc-windows-msvc/**/codex.exe"))
        candidates += list(package.glob(f"vendor/{architecture}-pc-windows-msvc/**/codex.exe"))
        if candidates:
            return [str(candidates[0])]
    raise RoutingError("Codex CLIが見つかりません。通常のcodexが実行できる環境で使ってください")


def hidden_flags():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Ollaya:
    def __init__(self, timeout=15):
        address = os.environ.get("OLLAYA_HOST", "127.0.0.1:11435")
        if "://" not in address:
            address = "http://" + address
        parsed = urllib.parse.urlsplit(address)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise RoutingError("Ollayaの接続先はローカルのループバックアドレスにしてください")
        if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise RoutingError("OLLAYA_HOSTの形式が正しくありません")
        self.base = address.rstrip("/")
        self.address = parsed.netloc
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.model = os.environ.get("CODEX_AUTO_OLLAYA_MODEL", "laya:multilingual")

    def request(self, endpoint, data=None, timeout=None):
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("OLLAYA_API_KEY")
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(self.base + endpoint,
                                         data=json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None,
                                         headers=headers)
        with self.opener.open(request, timeout=timeout or self.timeout) as response:
            body = response.read(65537)
        if len(body) > 65536:
            raise RoutingError("Ollayaの応答が大きすぎます")
        return json.loads(body)

    def ensure_running(self):
        try:
            return self.request("/api/tags", timeout=1)
        except urllib.error.HTTPError:
            raise RoutingError("Ollayaの認証または接続設定を確認してください") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            executable = shutil.which("ollaya.exe") or shutil.which("ollaya")
            if not executable:
                raise RoutingError("Ollayaが停止中で、ollayaコマンドも見つかりません") from None
            env = os.environ.copy()
            env["OLLAYA_HOST"] = self.address
            env.setdefault("OLLAYA_THREADS", "2")
            env.setdefault("OLLAYA_DEVICE", "cpu")
            subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=env, **hidden_flags())
            deadline = time.monotonic() + min(self.timeout, 5)
            while time.monotonic() < deadline:
                try:
                    return self.request("/api/tags", timeout=1)
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(.15)
            raise RoutingError("Ollayaの起動を確認できませんでした")

    def decide(self, prompt):
        tags = self.ensure_running()
        names = {m.get("name") or m.get("model") for m in tags.get("models", [])}
        name = self.model if ":" in self.model else self.model + ":latest"
        if name not in names:
            raise RoutingError("選択用のOllayaモデルが未インストールです")
        if len(prompt) > 12000:
            raise RoutingError("依頼文が長いため高性能側を選択します")
        return self.request("/api/decide", {"model": self.model, "state": prompt,
                                            "questions": QUESTIONS, "keep_alive": "60s"})


def assess(answer):
    if answer.get("state_truncated"):
        raise RoutingError("Ollayaの入力が切り詰められたため高性能側を選択します")
    answers = answer.get("answers", {})
    classification = answers.get("task_kind", {})
    kind = classification.get("choice")
    confidence = classification.get("confidence")
    risk = answers.get("high_impact", {}).get("noul")
    if kind not in KINDS or not isinstance(confidence, (int, float)) or not isinstance(risk, (int, float)):
        raise RoutingError("Ollayaの判定形式を確認できませんでした")
    if not all(math.isfinite(x) and 0 <= x <= 1 for x in (confidence, risk)):
        raise RoutingError("Ollayaの判定値が正しくありません")
    if risk >= .35:
        return "investigation", confidence, "データ・認証などへの影響を考慮"
    if confidence < .85:
        return "investigation", confidence, "判定が曖昧なため高性能側を選択"
    return kind, confidence, {"routine": "定型作業", "implementation": "コード修正・実装",
                              "investigation": "原因調査・設計・分析"}[kind]


def classify(prompt, decide, history=(), has_image=False):
    if has_image or not prompt.strip():
        raise RoutingError("画像または空の入力は高性能側で扱います")
    # 完全一致だけを使う。謝辞に続く操作指示や「そうして」は除外する。
    if re.fullmatch(r"\s*(?:ありがとう(?:ございます|ございました)?|サンクス|thanks|ナイス|おつかれ(?:さま)?|こんにちは|こんにひは|こんばんは|おはよう(?:ございます)?)[!！。\s]*", prompt, re.IGNORECASE):
        return "routine", 1.0, "操作指示を含まない挨拶・謝辞", "rule"
    if re.fullmatch(r"\s*(?:パス|保存先)(?:を)?(?:教えて|はどこ)[?？。\s]*", prompt):
        return "routine", 1.0, "既知の保存先の提示", "rule"
    state = prompt
    # 自足した依頼に、以前の作業の複雑さ・危険性を混ぜない。
    if history and len(prompt) < 80 and (re.search(r"それ|これ|その|この|続け|再開|そうし|任せ|頼む|やって|直して|修正|変更|反映|戻し|push|プッシュ", prompt, re.IGNORECASE)
                                       or re.fullmatch(r"\s*(?:yes|ok|はい|うん|そうですね)[。!！\s]*", prompt, re.IGNORECASE)):
        context = "\n".join(f"{role}: {text}" for role, text in history)
        state = f"今回の依頼（この作業を判定）:\n{prompt}\n\n参照用の背景（過去の作業を再実行する指示ではない）:\n{context}"
    kind, confidence, reason = assess(decide(state))
    return kind, confidence, reason, "ollaya"


def choose_model(models, kind, preferred_effort=None):
    names, default_effort = ROLES[kind]
    effort = preferred_effort or default_effort
    if kind == "routine":
        names = (*names, *ROLES["implementation"][0], *ROLES["investigation"][0])
    for name in dict.fromkeys(names):
        if name in BANNED_MODELS:
            continue
        record = models.get(name)
        if not record:
            continue
        if record["efforts"] and effort not in record["efforts"]:
            continue
        if not record["efforts"]:
            if preferred_effort or record["default"] != "low":
                continue
            return name, "low"
        return name, effort
    raise RoutingError("選択条件に合うモデルが一覧にありません。--list-modelsで確認し、--modelで指定してください")


def quota_summary():
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local"))) / "CodexMeter" / "observations.json"
    try:
        rows = read_json(path).get("events", [])
        point = max((row for row in rows if row.get("kind") == "quota" and row.get("bucket") == "codex"),
                    key=lambda row: row["ts"], default=None)
        now = time.time()
        if not point or not 0 <= now - point["ts"] <= 300:
            return {}
        return {slot: 100 - window["used"] for slot in ("primary", "secondary")
                if (window := point.get(slot)) and window["reset"] > now and 0 <= window["used"] <= 100}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RoutingError):
        return {}


def build_command(prefix, model, effort, prompt, tail, manual_options, no_daemon=True):
    check_allowed_model(model)
    command = list(prefix)
    if "model" not in manual_options:
        command += ["--model", model]
    if effort and "model_reasoning_effort" not in manual_options:
        command += ["-c", "model_reasoning_effort=" + json.dumps(effort)]
    command += tail
    if no_daemon and "--no-daemon" not in tail:
        command.append("--no-daemon")
    command += ["--", prompt]
    return command


def print_remaining(remaining):
    if remaining:
        names = {"primary": "短期", "secondary": "週間"}
        summary = " / ".join(f"{names[key]} {value:g}%" for key, value in remaining.items())
        print(f"[codex-auto] 残量: {summary}", file=sys.stderr)


def main(argv=None):
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        if not sys.stdin.isatty() and hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8-sig")
    raw = list(sys.argv[1:] if argv is None else argv)
    tail = []
    if "--" in raw:
        separator = raw.index("--")
        raw, tail = raw[:separator], raw[separator + 1:]
    parser = argparse.ArgumentParser(prog="codex-auto", description="Ollayaで送信ごとにモデルを選び、標準Codex CLIを使います。")
    parser.add_argument("prompt", nargs="?", help="最初の依頼文。省略時は標準CLIの入力欄、- は標準入力")
    parser.add_argument("--model", "-m", help="手動指定。Ollayaによる選択を省略")
    parser.add_argument("--effort", choices=EFFORTS, help="推論設定の手動指定")
    parser.add_argument("--dry-run", action="store_true", help="選択結果だけ確認。Codexを起動しない")
    parser.add_argument("--json", action="store_true", help="--dry-runの結果をJSONで出力")
    parser.add_argument("--list-models", action="store_true", help="ローカルのCodexモデル一覧を表示")
    parser.add_argument("--timeout", type=float, default=15, help="Ollayaの応答待ち秒数 (1〜60)")
    args = parser.parse_args(raw)
    try:
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 60:
            raise RoutingError("--timeoutは1〜60秒で指定してください")
        if args.json and not args.dry_run:
            raise RoutingError("--jsonは--dry-runと一緒に指定してください")
        prefix = native_codex()
        if not args.prompt and not args.dry_run and not args.list_models and any(x in tail for x in ("--help", "-h", "--version", "-V")):
            return subprocess.call(prefix + tail)
        settings, manual = configuration(tail)
        if args.list_models:
            for name, record in catalog(settings).items():
                print(f"{name}: {', '.join(record['efforts']) or 'default'}")
            return 0
        explicit = args.model or manual.get("model")
        check_allowed_model(explicit)
        effort = args.effort or manual.get("model_reasoning_effort")
        if args.model and manual.get("model") and args.model != manual["model"]:
            raise RoutingError("モデルの手動指定が重複して食い違っています")
        if args.effort and manual.get("model_reasoning_effort") and args.effort != manual["model_reasoning_effort"]:
            raise RoutingError("推論設定の手動指定が重複して食い違っています")
        if effort and effort not in EFFORTS:
            raise RoutingError("推論設定を確認してください")
        prompt = args.prompt
        if prompt == "-":
            prompt = sys.stdin.read()
        elif prompt is None and args.dry_run:
            if not sys.stdin.isatty():
                raise RoutingError("依頼文を引数に指定するか、- で標準入力を渡してください")
            prompt = input("依頼: ")
        if prompt is not None and not prompt.strip():
            raise RoutingError("依頼文が空です")
        if not args.dry_run:
            import asyncio
            from .bridge import run_session
            from .routing import TurnRouter

            provider = manual.get("provider") or settings.get("model_provider") or "openai"
            if not explicit and provider not in ("openai", "openai_chatgpt_plan"):
                raise RoutingError("このプロバイダーでは--modelで指定してください")
            models = {} if explicit else catalog(settings)
            boot_model = explicit or choose_model(models, "implementation", effort)[0]
            if not boot_model or boot_model.startswith("-") or any(c.isspace() for c in boot_model):
                raise RoutingError("モデル名の形式が正しくありません")
            router = TurnRouter(models, args.timeout, explicit, effort)
            print("[codex-auto] 送信ごとに自動選択。共有daemonは使用しません。", file=sys.stderr)
            print_remaining(quota_summary())
            return asyncio.run(run_session(prefix, boot_model, effort, prompt, tail, manual, router))
        confidence = None
        if explicit:
            model = explicit
            reason, source, kind = "手動指定", "manual", None
        else:
            provider = manual.get("provider") or settings.get("model_provider") or "openai"
            if provider not in ("openai", "openai_chatgpt_plan"):
                raise RoutingError("このプロバイダーでは--modelで指定してください")
            models = catalog(settings)
            try:
                kind, confidence, reason, source = classify(prompt, Ollaya(args.timeout).decide)
            except (RoutingError, OSError, ValueError, KeyError, TypeError, AttributeError):
                kind, reason, source = "investigation", "Ollayaの判定を確認できないため高性能側を選択", "fallback"
            model, effort = choose_model(models, kind, effort)
        if not isinstance(model, str) or not model or model.startswith("-") or any(c.isspace() for c in model):
            raise RoutingError("モデル名の形式が正しくありません")
        result = {"model": model, "effort": effort, "kind": kind, "source": source,
                  "confidence": confidence, "reason": reason, "remaining_percent": quota_summary()}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"[codex-auto] {model} / {effort or '既定の推論設定'} · {reason}", file=sys.stderr)
            print_remaining(result["remaining_percent"])
        if args.dry_run:
            return 0
    except KeyboardInterrupt:
        return 130
    except (RoutingError, OSError, ValueError, TypeError) as exc:
        message = str(exc) if isinstance(exc, RoutingError) else f"設定または起動を確認してください ({type(exc).__name__})"
        print(f"codex-auto: {message}", file=sys.stderr)
        return 2
