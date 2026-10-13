"""Opt-in check: two tiny read-only model calls in one ephemeral thread."""
import asyncio
import json
import logging
import os
import re
import secrets

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from codex_auto.bridge import Bridge, MAX_MESSAGE
from codex_auto.cli import catalog, configuration, native_codex
from codex_auto.routing import TurnRouter


async def verify():
    settings, _ = configuration([])
    router = TurnRouter(catalog(settings))
    options = []
    for name in settings.get("mcp_servers", {}):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise RuntimeError("Cannot safely disable an MCP configuration; skip live test")
        options += ["-c", f"mcp_servers.{name}.enabled=false"]
    token = secrets.token_urlsafe(32)
    bridge = Bridge(native_codex() + options + ["app-server", "--stdio"], router, token)
    logger = logging.getLogger("live-turn-verifier")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    async with serve(bridge.handler, "127.0.0.1", 0, process_request=bridge.authenticate,
                     logger=logger, max_size=MAX_MESSAGE, close_timeout=2) as server:
        address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        async with connect(address, additional_headers={"Authorization": "Bearer " + token},
                           proxy=None, max_size=MAX_MESSAGE) as client:
            async def request(identifier, method, params):
                await client.send(json.dumps({"id": identifier, "method": method, "params": params}))
                while True:
                    message = json.loads(await asyncio.wait_for(client.recv(), 90))
                    if message.get("id") == identifier:
                        if "error" in message:
                            raise RuntimeError(f"{method}: {message['error']['message']}")
                        return message["result"]

            await request(1, "initialize", {"clientInfo": {"name": "codex_auto_live_verifier", "version": "0.2.0"},
                                              "capabilities": {"experimentalApi": True}})
            await client.send(json.dumps({"method": "initialized"}))
            result = await request(2, "thread/start", {
                "ephemeral": True, "cwd": os.getcwd(), "approvalPolicy": "never", "sandbox": "read-only",
                "developerInstructions": "This is a transport test. Do not call any tools. Reply with one short word only."})
            thread_id = result["thread"]["id"]
            selections = []
            for identifier, prompt in enumerate((
                    "この英語の一文を日本語に翻訳してください: hello.",
                    "Python関数に引数の型ヒントを追加し、既存の処理を読みやすく整理してください。今回は実装せず確認とだけ返してください。"), 3):
                await request(identifier, "turn/start", {"threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}], "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly"}})
                selections.append(router.current[thread_id][0])
                while True:
                    message = json.loads(await asyncio.wait_for(client.recv(), 90))
                    if "method" in message and "id" in message:
                        raise RuntimeError("Unexpected tool/approval request; stop the test")
                    if message.get("method") == "turn/completed":
                        if message["params"]["turn"]["status"] != "completed":
                            raise RuntimeError("Turn failed: " + str(message["params"]["turn"].get("error")))
                        break
            assert selections[0] != selections[1], "model did not change"
            print("Two completed turns, one thread: " + " -> ".join(selections))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-inference", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(verify())
