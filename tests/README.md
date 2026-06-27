# Smoke tests

Verify the two non-LLM-free sponsor APIs work.

```
python -m venv .venv && ./.venv/bin/pip install -r tests/requirements.txt
cp .env.example .env   # fill in real keys
./.venv/bin/python tests/test_cerebras.py
```

- `test_cerebras.py` — lists live models, fires the worth-benching gate call, then verifies Backboard memory.
- `test_backboard.py` — Backboard chat probe (chat is paywalled on free tier; memory/RAG is not).

Note: live Cerebras catalog = `gpt-oss-120b`, `zai-glm-4.7` (both reasoning models -> use max_tokens >= 600).
