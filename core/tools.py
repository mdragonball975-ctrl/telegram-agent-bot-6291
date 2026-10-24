"""Tool registry exposed to the LLM. Every tool returns a string and never raises."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Awaitable, Callable

import httpx

from core.sandbox import Sandbox, SandboxError
from utils.text import truncate

log = logging.getLogger(__name__)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "write_file", "description": "Create or overwrite a text file in the sandbox workspace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file from the sandbox workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_files", "description": "List files in a sandbox directory (default root).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "delete_path", "description": "Delete a file or directory in the sandbox.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "run_python", "description": "Run a Python 3 script inside the sandbox directory and return stdout/stderr. Has a timeout.",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "http_get", "description": "Fetch a public URL (GET) and return the first part of the body as text.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]


class ToolExecutor:
    def __init__(self, sandbox: Sandbox, max_result_chars: int, python_timeout: int) -> None:
        self.sandbox = sandbox
        self.max_result_chars = max_result_chars
        self.python_timeout = python_timeout
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {
            "write_file": self._write_file,
            "read_file": self._read_file,
            "list_files": self._list_files,
            "delete_path": self._delete_path,
            "run_python": self._run_python,
            "http_get": self._http_get,
        }

    async def execute(self, name: str, raw_args: str | dict | None) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(self._handlers)}"
        try:
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except Exception as exc:
            return f"ERROR: invalid JSON arguments for {name}: {exc}"
        try:
            result = await handler(args)
        except SandboxError as exc:
            result = f"ERROR: {exc}"
        except KeyError as exc:
            result = f"ERROR: missing argument {exc}"
        except Exception as exc:  # never let a tool crash the agent
            log.exception("Tool %s crashed", name)
            result = f"ERROR: {type(exc).__name__}: {exc}"
        return truncate(str(result), self.max_result_chars)

    async def _write_file(self, a: dict) -> str:
        return await asyncio.to_thread(self.sandbox.write, str(a["path"]), str(a["content"]))

    async def _read_file(self, a: dict) -> str:
        return await asyncio.to_thread(self.sandbox.read, str(a["path"]), self.max_result_chars)

    async def _list_files(self, a: dict) -> str:
        return await asyncio.to_thread(self.sandbox.list, str(a.get("path") or "."))

    async def _delete_path(self, a: dict) -> str:
        return await asyncio.to_thread(self.sandbox.delete, str(a["path"]))

    async def _run_python(self, a: dict) -> str:
        code = str(a["code"])
        script = self.sandbox.root / "_agent_run.py"
        await asyncio.to_thread(script.write_text, code, "utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(script),
            cwd=str(self.sandbox.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.sandbox.root), "PYTHONIOENCODING": "utf-8"},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.python_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"ERROR: script timed out after {self.python_timeout}s"
        finally:
            try:
                script.unlink(missing_ok=True)
            except Exception:
                pass
        return (
            f"exit_code={proc.returncode}\n--- stdout ---\n{out.decode('utf-8', 'replace')}"
            f"\n--- stderr ---\n{err.decode('utf-8', 'replace')}"
        )

    async def _http_get(self, a: dict) -> str:
        url = str(a["url"]).strip()
        if not url.startswith(("http://", "https://")):
            return "ERROR: only http(s) URLs are allowed"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "TelegramAgent/1.0"})
        return f"HTTP {resp.status_code}\n{resp.text[: self.max_result_chars]}"
