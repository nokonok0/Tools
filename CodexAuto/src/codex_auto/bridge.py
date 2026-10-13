import asyncio
from contextlib import suppress
from http import HTTPStatus
import json
import logging
import os
import secrets
import subprocess
import sys

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .cli import RoutingError, build_command, hidden_flags

MAX_MESSAGE = 16 * 1024 * 1024
TOKEN_ENV = "CODEX_AUTO_SESSION_TOKEN"


def server_options(tail):
    result = []
    index = 0
    while index < len(tail):
        arg = tail[index]
        if arg in ("-c", "--config", "-p", "--profile", "--enable", "--disable"):
            if index + 1 >= len(tail):
                raise RoutingError(f"{arg} の値がありません")
            result.extend(tail[index:index + 2])
            index += 2
            continue
        if arg.startswith(("--config=", "--profile=", "--enable=", "--disable=")) or (
                arg.startswith(("-c", "-p")) and not arg.startswith("--") and len(arg) > 2):
            result.append(arg)
        if arg in ("--remote", "--remote-auth-token-env") or arg.startswith(("--remote=", "--remote-auth-token-env=")):
            raise RoutingError("codex-autoは専用のローカル接続を使うため--remoteは指定できません")
        index += 1
    return result


async def stop_process(process):
    if process.returncode is not None:
        return
    if process.stdin:
        process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), 3)
        return
    except asyncio.TimeoutError:
        pass
    # Only terminate the process tree created for this session, never other Codex instances.
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill.exe", "/PID", str(process.pid), "/T", "/F",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden_flags())
        await killer.wait()
    else:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def frontend_command(prefix, boot_model, effort, prompt, tail, manual, port):
    connection_flags = ["--remote", f"ws://127.0.0.1:{port}", "--remote-auth-token-env", TOKEN_ENV]
    # --remote and --no-daemon conflict in Codex. This explicit private server
    # already bypasses the shared daemon, including when the flag is supplied.
    frontend_tail = [arg for arg in tail if arg != "--no-daemon"]
    command = build_command(prefix, boot_model, effort, prompt, connection_flags + frontend_tail,
                            manual, no_daemon=False)
    return command[:-2] if prompt is None else command


class Bridge:
    def __init__(self, backend_command, router, token):
        self.backend_command = backend_command
        self.router = router
        self.token = token
        self.claimed = False

    def authenticate(self, connection, request):
        authorization = request.headers.get_all("Authorization")
        if request.headers.get_all("Origin") or request.path != "/":
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")
        if len(authorization) != 1 or not secrets.compare_digest(authorization[0], "Bearer " + self.token):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        if self.claimed:
            return connection.respond(HTTPStatus.CONFLICT, "Session already connected\n")
        self.claimed = True

    async def handler(self, connection):
        backend = None
        tasks = []
        try:
            env = os.environ.copy()
            env.pop(TOKEN_ENV, None)
            backend = await asyncio.create_subprocess_exec(
                *self.backend_command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, limit=MAX_MESSAGE, **hidden_flags())

            async def inbound():
                async for raw in connection:
                    if not isinstance(raw, str):
                        await connection.close(1003, "Text JSON required")
                        return
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise RoutingError("JSON-RPCメッセージの形式が正しくありません")
                    try:
                        changed, decision = await asyncio.to_thread(self.router.client_message, message)
                    except RoutingError as exc:
                        if "id" in message:
                            self.router.pending.pop(message["id"], None)
                            await connection.send(json.dumps({"id": message["id"], "error": {
                                "code": -32602, "message": str(exc)}}))
                            continue
                        raise
                    if decision and decision["source"] == "fallback":
                        await connection.send(json.dumps({"method": "warning", "params": {
                            "threadId": message["params"].get("threadId"),
                            "message": f"Codex Auto: {decision['reason']} ({decision['model']})"}}, ensure_ascii=False))
                    backend.stdin.write((json.dumps(changed, ensure_ascii=False) + "\n").encode("utf-8"))
                    await backend.stdin.drain()

            async def outbound():
                while raw := await backend.stdout.readline():
                    message = json.loads(raw)
                    self.router.server_message(message)
                    await connection.send(json.dumps(message, ensure_ascii=False))

            tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except ConnectionClosed:
            pass
        except (OSError, ValueError, RoutingError):
            await connection.close(1011, "Codex Auto bridge failed")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if backend:
                await stop_process(backend)


async def run_session(prefix, boot_model, effort, prompt, tail, manual, router):
    options = server_options(tail)
    token = secrets.token_urlsafe(32)
    bridge = Bridge(prefix + options + ["app-server", "--stdio"], router, token)
    logger = logging.getLogger("codex_auto.transport")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    frontend = None
    async with serve(bridge.handler, "127.0.0.1", 0, process_request=bridge.authenticate,
                     origins=[None], compression=None, max_size=MAX_MESSAGE,
                     close_timeout=2, logger=logger) as server:
        port = server.sockets[0].getsockname()[1]
        command = frontend_command(prefix, boot_model, effort, prompt, tail, manual, port)
        env = os.environ.copy()
        env[TOKEN_ENV] = token
        try:
            frontend = await asyncio.create_subprocess_exec(*command, env=env)
            return await frontend.wait()
        finally:
            if frontend:
                await stop_process(frontend)
