# Private Telegram AI Agent (Railway-ready)

An autonomous, admin-only Telegram agent: multi-step reasoning with Groq (Llama 3.3 70B),
tool calling (sandboxed files, Python, HTTP fetch), a MongoDB Atlas lease-locked task queue,
live progress animations, and a FastAPI `/health` endpoint.

## Layout
```
main.py              entry point (polling + workers + health server, graceful shutdown)
config.py            all settings from env vars (no secrets in code)
healthcheck.py       Docker HEALTHCHECK probe
core/ai_client.py    Groq/OpenAI-compatible client: rate limiter + 429/5xx backoff (honours Retry-After)
core/rate_limiter.py token bucket (AI_MAX_RPM)
core/agent.py        bounded reason -> tool -> observe loop
core/tools.py        tool schemas + executor (never raises)
core/sandbox.py      per-chat workspace, path-escape proof, atomic writes
core/worker.py       worker pool: lease claim, heartbeat renewals, retries w/ backoff, dead-letter, cancel
db/mongo.py          Motor client, queue, history, stats
bot/handlers.py      /start /help /status /reset /cancel + task intake (admin-gated)
bot/sender.py        flood-wait-aware send/edit, HTML fallback, chunking, log channel
ui/animations.py     progress frames
health/server.py     FastAPI /health on $PORT (8080)
tests/               24 tests (sandbox, tools, backoff, agent loop, queue, worker e2e, health)
```

## Deploy on Railway
1. Push this folder to a **private** GitHub repo.
2. Railway -> New Project -> Deploy from GitHub repo.
3. Service -> **Variables**: add every key from `.env.example` with your real values.
4. Settings -> Networking -> Generate Domain (target port 8080) so `/health` is reachable.
5. Keep **1 replica**: Telegram polling allows only one consumer per bot token.
6. Optional: attach a Railway Volume at `/app/workspace` so sandbox files survive redeploys.

In MongoDB Atlas, Network Access must allow Railway (e.g. `0.0.0.0/0`, since Railway egress IPs are dynamic).

## Local
```
pip install -r requirements-dev.txt
cp .env.example .env   # fill it in
pytest
python main.py
```

## Notes
- `PROGRESS_INTERVAL_SECONDS` is clamped to >= 2s: Telegram throttles message edits harder below that.
- Groq's free tier allows ~30 requests/min on llama-3.3-70b-versatile; keep `AI_MAX_RPM` at or under your plan's limit.
- `run_python` runs code as the container user with a timeout, inside the chat's folder. It's admin-only;
  don't open the bot to untrusted users without stronger isolation.
- `API_ID`/`API_HASH` are read but unused (the Bot API doesn't need them).
