"""Smoke test: Cerebras inference (OpenAI SDK) + Backboard memory, together.

Mirrors Bench's real split:
  - Cerebras  = all LLM work (gate, generate alternatives, harness, rank)
  - Backboard = taste/memory persistence (free tier: memory + RAG only)
"""
import asyncio
import os

from dotenv import load_dotenv
from openai import OpenAI
from backboard import BackboardClient

load_dotenv()


def test_cerebras():
    client = OpenAI(
        base_url="https://api.cerebras.ai/v1",
        api_key=os.environ["CEREBRAS_API_KEY"],
    )

    # Pull the LIVE catalog — never hardcode (PRD deprecation warning).
    models = [m.id for m in client.models.list().data]
    print("LIVE MODELS:", models)

    # Prefer gpt-oss-120b for structured gate output; both live models are
    # REASONING models, so give a generous max_tokens (reasoning burns output).
    pref = "gpt-oss-120b" if "gpt-oss-120b" in models else models[0]
    print("USING MODEL:", pref)

    resp = client.chat.completions.create(
        model=pref,
        messages=[
            {"role": "system", "content": "You are Bench's worth-benching gate. Reply with one short JSON object only."},
            {"role": "user", "content": 'Given a 9-line merge_intervals sort-sweep, is it worth benching? Return {"worth_benching": bool, "axes": [..], "reason": ".."}'},
        ],
        temperature=0.3,
        max_tokens=800,
    )
    usage = resp.usage
    print("CEREBRAS REPLY:", (resp.choices[0].message.content or "").strip())
    print(f"TOKENS: in={usage.prompt_tokens} out={usage.completion_tokens}")
    return pref


async def test_backboard():
    client = BackboardClient(api_key=os.environ["BACKBOARD_API_KEY"])
    a = await client.create_assistant(name="bench-taste", system_prompt="Stores a dev's code taste.")
    aid = a["assistant_id"] if isinstance(a, dict) else a.assistant_id
    await client.add_memory(aid, "Dev prefers readable over clever; avoids new deps.",
                            metadata={"repo_id": "ricky"})
    hits = await client.search_memories(aid, "what does the dev prefer?", limit=3)
    print("BACKBOARD MEMORY OK · assistant", aid)
    print("  search hits:", hits)


if __name__ == "__main__":
    print("=== CEREBRAS ===")
    model = test_cerebras()
    print("\n=== BACKBOARD ===")
    asyncio.run(test_backboard())
    print("\n✓ both work together. Cerebras=LLM, Backboard=memory.")
