import asyncio
import json

from bench.clients.backboard import BackboardAdapter
from bench.clients.cerebras import CerebrasClient
from bench.clients.gemini import GeminiClient
from bench.config import get_settings


async def main() -> None:
    settings = get_settings()
    payload = {
        "primary_provider": settings.primary_llm_provider,
        "gemini": await GeminiClient(settings).check(),
        "cerebras": await CerebrasClient(settings).check(),
        "backboard": await BackboardAdapter(settings).check(),
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
