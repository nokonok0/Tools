"""Check the installed Codex protocol through the bridge without model inference."""
import asyncio
import json
import logging
import os
import secrets
import tempfile

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from codex_auto.bridge import Bridge
from codex_auto.cli import native_codex
from codex_auto.routing import TurnRouter


async def verify():
    token = secrets.token_urlsafe(32)
    bridge = Bridge(native_codex() + ["app-server", "--stdio"], TurnRouter({}), token)
    logger = logging.getLogger("protocol-verifier")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    async with serve(bridge.handler, "127.0.0.1", 0, process_request=bridge.authenticate,
                     logger=logger, close_timeout=2) as server:
        address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        async with connect(address, additional_headers={"Authorization": "Bearer " + token},
                           proxy=None, max_size=16 * 1024 * 1024) as client:
            async def request(identifier, method, params):
                await client.send(json.dumps({"id": identifier, "method": method, "params": params}))
                while True:
                    message = json.loads(await asyncio.wait_for(client.recv(), 30))
                    if message.get("id") == identifier:
                        if "error" in message:
                            raise RuntimeError(f"{method}: {message['error']['message']}")
                        return message["result"]

            await request(1, "initialize", {"clientInfo": {"name": "codex_auto_verifier", "version": "0.2.0"},
                                              "capabilities": {"experimentalApi": True}})
            await client.send(json.dumps({"method": "initialized"}))
            models = await request(2, "model/list", {})
            assert models.get("data"), "model list is empty"
            thread = await request(3, "thread/start", {"ephemeral": True, "cwd": os.getcwd(),
                                                       "approvalPolicy": "never", "sandbox": "read-only"})
            assert thread["thread"]["id"], "missing thread id"
            print("Installed Codex: initialize, model/list and ephemeral thread/start passed; no inference.")


if __name__ == "__main__":
    # An empty temporary home isolates the check from credentials, hooks and MCP configuration.
    original = os.environ.get("CODEX_HOME")
    with tempfile.TemporaryDirectory(prefix="codex-auto-protocol-") as home:
        os.environ["CODEX_HOME"] = home
        try:
            asyncio.run(verify())
        finally:
            if original is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = original
