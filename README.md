# Bench Orchestrator Bootstrap

Minimal backend scaffold for the Bench hackathon build.

## What is included

- FastAPI app entrypoint
- environment-driven config
- Gemini client wrapper (primary)
- Cerebras client wrapper
- Backboard client wrapper
- provider connectivity check script
- benchmark preview generation endpoint

## Quick start

1. Create a virtual environment.
2. Install dependencies with `pip install -e .`
3. Copy `.env.example` to `.env` and fill in your keys.
4. Run `uvicorn bench.main:app --reload`
5. Run `python -m bench.scripts.check_providers`
6. Run `python -m bench.scripts.preview_bench`

## Current note

The backend currently uses Gemini as the primary text-generation provider with small context defaults to stay within free-tier limits.
