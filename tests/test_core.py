import asyncio
import json

import httpx
import pytest

from core.agent import Agent
from core.ai_client import AIClient, AIClientError
from core.rate_limiter import RateLimiter
from core.sandbox import Sandbox, SandboxError
from core.tools import ToolExecutor
from ui.animations import ProgressAnimator
from utils.logging_setup import redact
from utils.text import chunk_text, truncate


# ---------------- sandbox ----------------
def test_sandbox_blocks_escape(tmp_path):
    sb = Sandbox(tmp_path, 1, 1000)
    for bad in ["../x", "../../etc/passwd", "a/../../b", "/../../root"]:
        with pytest.raises(SandboxError):
            sb.resolve(bad)
    with pytest.raises(SandboxError):
        sb.resolve("a\x00b")


def test_sandbox_roundtrip_and_limits(tmp_path):
    sb = Sandbox(tmp_path, 7, 50)
    sb.write("d/f.txt", "hello")
    assert sb.read("d/f.txt", 100) == "hello"
    assert "f.txt" in sb.list("d")
    with pytest.raises(SandboxError):
        sb.write("big.txt", "x" * 51)
    sb.delete("d")
    assert sb.list() == "(empty)"
    with pytest.raises(SandboxError):
        sb.delete(".")


def test_sandbox_chats_isolated(tmp_path):
    a, b = Sandbox(tmp_path, 1, 100), Sandbox(tmp_path, 2, 100)
    a.write("s.txt", "secret")
    with pytest.raises(SandboxError):
        b.read("s.txt", 100)


# ---------------- tools ----------------
@pytest.mark.asyncio
async def test_tools_never_raise(tmp_path):
    t = ToolExecutor(Sandbox(tmp_path, 1, 1000), 500, 5)
    assert (await t.execute("nope", "{}")).startswith("ERROR")
    assert (await t.execute("read_file", "not json")).startswith("ERROR")
    assert (await t.execute("read_file", "[]")).startswith("ERROR")
    assert (await t.execute("read_file", "{}")).startswith("ERROR")
    assert (await t.execute("read_file", '{"path":"../x"}')).startswith("ERROR")
    assert (await t.execute("http_get", '{"url":"file:///etc/passwd"}')).startswith("ERROR")


@pytest.mark.asyncio
async def test_run_python_and_timeout(tmp_path):
    t = ToolExecutor(Sandbox(tmp_path, 1, 1000), 2000, 2)
    out = await t.execute("run_python", json.dumps({"code": "print(6*7)"}))
    assert "exit_code=0" in out and "42" in out
    out = await t.execute("run_python", json.dumps({"code": "import time; time.sleep(10)"}))
    assert "timed out" in out
    out = await t.execute("run_python", json.dumps({"code": "raise SystemExit(3)"}))
    assert "exit_code=3" in out


@pytest.mark.asyncio
async def test_tool_output_truncated(tmp_path):
    t = ToolExecutor(Sandbox(tmp_path, 1, 10**6), 600, 5)
    out = await t.execute("run_python", json.dumps({"code": "print('x'*5000)"}))
    assert len(out) <= 600


# ---------------- text / redaction / ui ----------------
def test_chunk_text():
    assert chunk_text("") == ["(empty response)"]
    text = ("line " * 50 + "\n") * 100
    chunks = chunk_text(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(c.replace("\n", "").replace(" ", "") for c in chunks) == text.replace("\n", "").replace(" ", "")
    assert all(len(c) <= 100 for c in chunk_text("x" * 1000, 100))


def test_truncate():
    assert truncate("abc", 10) == "abc"
    assert len(truncate("a" * 100, 20)) == 20


def test_redact():
    s = redact("tok 1234567890:AAFakeTokenFakeTokenFakeTokenFake123 key gsk_abcdefghijklmnopqrstuvwxyz mongodb+srv://u:pass@h/")
    assert "AAFake" not in s and "gsk_abc" not in s and "pass@" not in s and "u:***@" in s


def test_animator():
    a = ProgressAnimator()
    f1 = a.frame()
    a.advance_stage(); a.advance_stage(); a.advance_stage(); a.advance_stage()
    assert "Verifying" in a.frame() and f1 != a.frame()
    assert "Verified" in ProgressAnimator.done_frame(1.0)


# ---------------- AI client ----------------
def _client(handler, retries=4):
    c = AIClient("k", "https://x/v1", "m", max_rpm=6000, max_retries=retries)
    c._client = httpx.AsyncClient(base_url="https://x/v1", transport=httpx.MockTransport(handler))
    async def no_sleep(*a, **k):
        return None
    c._sleep_backoff = no_sleep
    return c


@pytest.mark.asyncio
async def test_ai_retries_429_then_succeeds():
    calls = {"n": 0}
    def h(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, text="slow down")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]})
    c = _client(h)
    msg = await c.chat([{"role": "user", "content": "x"}])
    assert msg["content"] == "hi" and calls["n"] == 3


@pytest.mark.asyncio
async def test_ai_gives_up_and_fails_fast_on_4xx():
    c = _client(lambda r: httpx.Response(503, text="down"), retries=3)
    with pytest.raises(AIClientError):
        await c.chat([])
    calls = {"n": 0}
    def h(r):
        calls["n"] += 1
        return httpx.Response(401, text="bad key")
    c = _client(h)
    with pytest.raises(AIClientError):
        await c.chat([])
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_rate_limiter_throttles():
    rl = RateLimiter(120)  # 2/s
    rl.tokens = 0
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await rl.acquire()
    assert loop.time() - t0 >= 0.4


# ---------------- agent loop ----------------
class FakeAI:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []
    async def chat(self, messages, tools=None, temperature=None):
        self.seen.append(list(messages))
        return self.replies.pop(0)


@pytest.mark.asyncio
async def test_agent_uses_tool_then_answers(tmp_path):
    ai = FakeAI([
        {"content": None, "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "write_file", "arguments": json.dumps({"path": "a.txt", "content": "yo"})}}]},
        {"content": "Done!"},
    ])
    tools = ToolExecutor(Sandbox(tmp_path, 1, 100), 500, 5)
    events = []
    async def prog(d): events.append(d)
    out = await Agent(ai, tools, 5).run("make file", [{"role": "user", "content": "earlier"}], prog)
    assert out == "Done!"
    assert (tmp_path / "chat_1" / "a.txt").read_text() == "yo"
    assert ai.seen[1][-1]["role"] == "tool" and ai.seen[1][-1]["tool_call_id"] == "c1"
    assert events


@pytest.mark.asyncio
async def test_agent_step_cap(tmp_path):
    loop_reply = {"content": "", "tool_calls": [{"id": "c", "function": {"name": "list_files", "arguments": "{}"}}]}
    ai = FakeAI([loop_reply] * 3)
    out = await Agent(ai, ToolExecutor(Sandbox(tmp_path, 1, 100), 500, 5), 3).run("x", [])
    assert "maximum number of reasoning steps" in out


@pytest.mark.asyncio
async def test_agent_ai_error_is_graceful(tmp_path):
    class Boom:
        async def chat(self, *a, **k):
            raise AIClientError("nope")
    out = await Agent(Boom(), ToolExecutor(Sandbox(tmp_path, 1, 100), 500, 5), 3).run("x", [])
    assert "unavailable" in out
