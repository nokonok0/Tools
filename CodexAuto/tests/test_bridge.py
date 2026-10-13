import asyncio
import json
import logging
import sys
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from codex_auto.bridge import Bridge, frontend_command, server_options
from codex_auto.routing import TurnRouter
from codex_auto import cli
from test_cli import MODELS, response
from test_routing import turn

ECHO = "import sys\nfor line in sys.stdin:\n sys.stdout.write(line)\n sys.stdout.flush()\n"


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_picker_can_connect_repeatedly_without_closing_main(self):
        router = TurnRouter(MODELS)
        router.remember("main", "user", "main history")
        bridge = Bridge([sys.executable, "-u", "-c", ECHO], router, "secret")
        async with serve(bridge.handler, "127.0.0.1", 0, process_request=bridge.authenticate,
                         close_timeout=1) as server:
            address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            headers = {"Authorization": "Bearer secret"}
            async with connect(address, additional_headers=headers, proxy=None) as main:
                request = {"id": 1, "method": "initialize", "params": {}}
                await main.send(json.dumps(request))
                self.assertEqual(json.loads(await main.recv()), request)
                for _ in range(2):
                    async with connect(address, additional_headers=headers, proxy=None) as picker:
                        # Native clients independently reuse the same RPC IDs.
                        await picker.send(json.dumps(request))
                        self.assertEqual(json.loads(await picker.recv()), request)
                        listing = {"id": 2, "method": "thread/list", "params": {}}
                        await picker.send(json.dumps(listing))
                        self.assertEqual(json.loads(await picker.recv()), listing)
                    await main.send(json.dumps(request))
                    self.assertEqual(json.loads(await main.recv()), request)
                self.assertEqual(list(router.history("main")), [("user", "main history")])
        self.assertFalse(bridge.connections)

    async def test_actual_transport_routes_multiple_turns_and_forwards_approvals(self):
        router = TurnRouter(MODELS)
        bridge = Bridge([sys.executable, "-u", "-c", ECHO], router, "test-secret")
        async with serve(bridge.handler, "127.0.0.1", 0, process_request=bridge.authenticate,
                         close_timeout=1) as server:
            address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            with patch.object(cli.Ollaya, "decide", side_effect=[response("routine"), response("investigation")]):
                async with connect(address, additional_headers={"Authorization": "Bearer test-secret"}, proxy=None) as client:
                    for identifier, expected in ((1, "gpt-6-luna"), (2, "gpt-6.1-sol")):
                        await client.send(json.dumps(turn(identifier, "task")))
                        received = json.loads(await asyncio.wait_for(client.recv(), 3))
                        self.assertEqual(received["params"]["model"], expected)
                    approval = {"id": "approval-1", "result": {"decision": "decline"}}
                    await client.send(json.dumps(approval))
                    self.assertEqual(json.loads(await client.recv()), approval)

    async def test_backend_exits_after_connection_closes(self):
        bridge = Bridge([sys.executable, "-u", "-c", ECHO], TurnRouter(MODELS), "secret")
        original = asyncio.create_subprocess_exec
        children = []

        async def capture(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            return child

        with patch("codex_auto.bridge.asyncio.create_subprocess_exec", side_effect=capture):
            async with serve(bridge.handler, "127.0.0.1", 0, process_request=bridge.authenticate,
                             close_timeout=1) as server:
                address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
                async with connect(address, additional_headers={"Authorization": "Bearer secret"}, proxy=None) as client:
                    await client.send(json.dumps({"id": 1, "method": "initialize", "params": {}}))
                    await asyncio.wait_for(client.recv(), 3)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].returncode, 0)

    async def test_unauthenticated_and_browser_connections_do_not_start_backend(self):
        bridge = Bridge(["must-not-run.exe"], TurnRouter(MODELS), "secret")
        logger = logging.getLogger("bridge-test")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        async with serve(bridge.handler, "127.0.0.1", 0, process_request=bridge.authenticate,
                         logger=logger, close_timeout=1) as server:
            address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            for headers in ({}, {"Authorization": "Bearer wrong"},
                            {"Authorization": "Bearer secret", "Origin": "https://example.com"}):
                with self.assertRaises(InvalidStatus):
                    async with connect(address, additional_headers=headers, proxy=None):
                        self.fail("connection should be rejected")
            self.assertFalse(bridge.claimed)

    def test_backend_configuration_and_external_remote_rejection(self):
        tail = ["-c", "x=1", "--profile=work", "--enable", "test", "--sandbox", "read-only", "--no-daemon"]
        self.assertEqual(server_options(tail), ["-c", "x=1", "--profile=work", "--enable", "test"])
        with self.assertRaises(cli.RoutingError):
            server_options(["--remote", "ws://example.com"])

    def test_native_tui_uses_private_remote_without_conflicting_daemon_flag(self):
        for prompt in (None, "literal & 日本語"):
            command = frontend_command(["codex.exe"], "model", "high", prompt,
                                       ["--no-daemon", "--sandbox", "read-only"], {}, 12345)
            self.assertNotIn("--no-daemon", command)
            self.assertIn("ws://127.0.0.1:12345", command)
            self.assertIn("read-only", command)
            if prompt is None:
                self.assertNotIn("--", command)
            else:
                self.assertEqual(command[-2:], ["--", prompt])
