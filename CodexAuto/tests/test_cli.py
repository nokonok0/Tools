import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from codex_auto import cli


MODELS = {
    "gpt-6-luna": {"efforts": ["low", "medium", "high"], "default": "medium"},
    "gpt-6.1-sol": {"efforts": ["low", "medium", "high", "ultra"], "default": "low"},
    "gpt-6-astra": {"efforts": ["low", "medium", "high"], "default": "low"},
}


def response(kind="routine", confidence=.99, risk=.05, truncated=False):
    return {"answers": {"task_kind": {"choice": kind, "confidence": confidence},
                         "high_impact": {"noul": risk}}, "state_truncated": truncated}


class RoutingTests(unittest.TestCase):
    def test_signed_router_interactive_start_keeps_auto_routing_enabled(self):
        from codex_auto import bridge
        settings = {"model_provider": "codex-router-signed", "model_providers": {
            "codex-router-signed": {"base_url": "http://127.0.0.1:4202/v1",
                                    "wire_api": "responses", "requires_openai_auth": True}}}
        with patch.object(cli, "native_codex", return_value=["codex.exe"]), \
             patch.object(cli, "configuration", return_value=(settings, {})), \
             patch.object(cli, "catalog", return_value=MODELS), \
             patch.object(cli, "quota_summary", return_value={}), \
             patch.object(bridge, "run_session", new_callable=AsyncMock, return_value=0) as session, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([]), 0)
        router = session.call_args.args[-1]
        self.assertIsNone(router.fixed_model)
        self.assertEqual(router.select("test", "ファイルを検索して")["model"], "gpt-6-luna")

    def test_signed_router_can_automatically_select_subscription_models(self):
        settings = {"model_provider": "codex-router-signed", "model_providers": {
            "codex-router-signed": {"base_url": "http://127.0.0.1:4202/v1",
                                    "wire_api": "responses", "requires_openai_auth": True}}}
        cli.require_auto_provider(settings, {})
        output = io.StringIO()
        with patch.object(cli, "native_codex", return_value=["codex.exe"]), \
             patch.object(cli, "configuration", return_value=(settings, {})), \
             patch.object(cli, "catalog", return_value=MODELS), \
             patch.object(cli, "quota_summary", return_value={}), \
             patch.object(cli.Ollaya, "decide") as decide, contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["--dry-run", "--json", "ファイルを検索して"]), 0)
            decide.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual((result["model"], result["effort"]), ("gpt-6-luna", "low"))

    def test_signed_router_requires_local_authenticated_responses_endpoint(self):
        good = {"base_url": "http://localhost:4202/v1", "wire_api": "responses", "requires_openai_auth": True}
        for change in ({"base_url": "https://example.com/v1"}, {"requires_openai_auth": False},
                       {"wire_api": "chat"}, {"base_url": "http://user:secret@localhost/v1"}):
            settings = {"model_provider": "codex-router-signed",
                        "model_providers": {"codex-router-signed": good | change}}
            with self.assertRaises(cli.RoutingError):
                cli.require_auto_provider(settings, {})
        with self.assertRaises(cli.RoutingError):
            cli.require_auto_provider({"model_provider": "codex-router"}, {})
        with self.assertRaises(cli.RoutingError):
            cli.require_auto_provider({}, {"provider": "oss"})

    def test_banned_models_cannot_be_manual_or_fallback_choices(self):
        banned = {name: {"efforts": ["low", "medium", "high"], "default": "low"}
                  for name in cli.BANNED_MODELS}
        for kind in cli.KINDS:
            with self.assertRaises(cli.RoutingError):
                cli.choose_model(banned, kind)
        for model in cli.BANNED_MODELS:
            for tail in (["--model", model], ["--config", f'model="{model}"']):
                with patch.object(cli, "native_codex", return_value=["codex.exe"]), \
                     patch.object(cli, "configuration", return_value=({}, cli.overrides(tail))), \
                     patch.object(cli.subprocess, "call") as execute, \
                     contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(["--dry-run", "task", "--", *tail]), 2)
                    execute.assert_not_called()
            with self.assertRaises(cli.RoutingError):
                cli.build_command(["codex.exe"], model, "low", "task", [], {})

    def test_local_catalog_excludes_banned_models(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "models.json"
            path.write_text(json.dumps({"models": [{"slug": name} for name in
                            (*cli.BANNED_MODELS, "gpt-6.1-sol", "gpt-6-luna")]}))
            self.assertEqual(set(cli.catalog({"model_catalog_json": str(path)})),
                             {"gpt-6.1-sol", "gpt-6-luna"})

    def test_no_daemon_is_default_without_duplicate_flags(self):
        for tail in ([], ["--no-daemon"], ["--sandbox", "read-only"]):
            command = cli.build_command(["codex.exe"], "model", "high", "task", tail, {})
            self.assertEqual(command.count("--no-daemon"), 1)
            self.assertLess(command.index("--no-daemon"), command.index("--"))

    def test_npm_install_uses_original_node_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            entry = root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            native = entry.parent.parent / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
            native.parent.mkdir(parents=True)
            native.touch()
            paths = {"codex.cmd": str(root / "codex.cmd"), "node.exe": str(root / "node.exe")}
            with patch.dict(os.environ, {"CODEX_AUTO_CODEX_EXE": ""}), \
                 patch.object(cli.shutil, "which", side_effect=lambda name: paths.get(name)):
                self.assertEqual(cli.native_codex(), [paths["node.exe"], str(entry)])

    def test_three_roles_and_uncertainty_guard(self):
        for kind, expected in zip(cli.KINDS, ("gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-sol")):
            assessed = cli.assess(response(kind))
            self.assertEqual(cli.choose_model(MODELS, assessed[0])[0], expected)
        self.assertEqual(cli.assess(response(confidence=.4))[0], "investigation")
        self.assertEqual(cli.assess(response(risk=.8))[0], "investigation")

    def test_invalid_or_truncated_decisions_are_not_trusted(self):
        for answer in (response(truncated=True), response(confidence=float('nan')), {}, response(risk=2)):
            with self.assertRaises(cli.RoutingError):
                cli.assess(answer)

    def test_catalog_compatibility_and_high_model_fallback(self):
        models = {"gpt-6.1-sol": MODELS["gpt-6.1-sol"]}
        self.assertEqual(cli.choose_model(models, "investigation"), ("gpt-6.1-sol", "low"))
        self.assertEqual(cli.choose_model(MODELS, "routine", "ultra"), ("gpt-6.1-sol", "ultra"))
        with self.assertRaises(cli.RoutingError):
            cli.choose_model({}, "investigation")

    def test_nonlocal_servers_are_rejected(self):
        for host in ("https://example.com", "http://127.0.0.1.evil.example", "http://user:pass@localhost:11435", "http://localhost:11435/path"):
            with patch.dict(os.environ, {"OLLAYA_HOST": host}):
                with self.assertRaises(cli.RoutingError):
                    cli.Ollaya()

    def test_manual_selection_bypasses_ollaya_and_json_has_no_prompt(self):
        output = io.StringIO()
        with patch.object(cli, "native_codex", return_value=["codex.exe"]), \
             patch.object(cli, "configuration", return_value=({}, {})), \
             patch.object(cli, "quota_summary", return_value={}), \
             patch.object(cli.Ollaya, "decide") as decide, contextlib.redirect_stdout(output):
            result = cli.main(["--dry-run", "--json", "--model", "manual-model", "PRIVATE-PROMPT"])
        self.assertEqual(result, 0)
        decide.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["model"], "manual-model")
        self.assertNotIn("PRIVATE-PROMPT", output.getvalue())

    def test_offline_ollaya_selects_quality_fallback_without_codex_inference(self):
        output = io.StringIO()
        with patch.object(cli, "native_codex", return_value=["codex.exe"]), \
             patch.object(cli, "configuration", return_value=({}, {})), \
             patch.object(cli, "catalog", return_value=MODELS), \
             patch.object(cli, "quota_summary", return_value={}), \
             patch.object(cli.Ollaya, "decide", side_effect=TimeoutError), \
             patch.object(cli.subprocess, "call") as execute, contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["--dry-run", "--json", "task"]), 0)
        self.assertEqual(json.loads(output.getvalue())["model"], "gpt-6.1-sol")
        execute.assert_not_called()

    def test_passthrough_manual_config_and_conflicts(self):
        options = cli.overrides(["--config", 'model="custom/model"', "-c", 'model_reasoning_effort="high"'])
        self.assertEqual(options["model"], "custom/model")
        self.assertEqual(options["model_reasoning_effort"], "high")
        with patch.object(cli, "native_codex", return_value=["codex.exe"]), \
             patch.object(cli, "configuration", return_value=({}, {"model": "other"})), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--model", "first", "task"]), 2)

    def test_real_child_receives_literal_prompt_permissions_cwd_and_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            child = directory / "child.py"
            result_file = directory / "result.json"
            child.write_text("import json,os,sys\nfrom pathlib import Path\nPath(os.environ['TEST_OUTPUT']).write_text(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd()}))\nsys.exit(7)\n", encoding="utf-8")
            prompt = 'quote " & $(not-a-command) 日本語\nnext line'
            tail = ["--sandbox", "read-only", "--ask-for-approval", "on-request"]
            command = cli.build_command([sys.executable, str(child)], "model", "high", prompt, tail, {})
            with patch.dict(os.environ, {"TEST_OUTPUT": str(result_file)}):
                result = subprocess.call(command, cwd=directory)
            data = json.loads(result_file.read_text())
            self.assertEqual(result, 7)
            self.assertEqual(data["argv"][-1], prompt)
            self.assertEqual(data["argv"][-2], "--")
            self.assertIn("read-only", data["argv"])
            self.assertIn("on-request", data["argv"])
            self.assertIn("--no-daemon", data["argv"])
            self.assertEqual(Path(data["cwd"]), directory)

    def test_stale_and_expired_meter_data_are_ignored(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"LOCALAPPDATA": folder}):
            directory = Path(folder) / "CodexMeter"
            directory.mkdir()
            point = {"kind": "quota", "bucket": "codex", "ts": now - 600,
                     "primary": {"used": 90, "reset": now + 600}}
            file = directory / "observations.json"
            file.write_text(json.dumps({"events": [point]}))
            self.assertEqual(cli.quota_summary(), {})
            point["ts"] = now
            point["primary"]["reset"] = now - 1
            file.write_text(json.dumps({"events": [point]}))
            self.assertEqual(cli.quota_summary(), {})


if __name__ == "__main__":
    unittest.main()
