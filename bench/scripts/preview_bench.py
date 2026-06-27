import asyncio
import json

from bench.config import get_settings
from bench.schemas import ConversationMessage, RepoContextConfig
from bench.services.bench_preview import BenchPreviewService
from bench.clients.cerebras import CerebrasClient


async def main() -> None:
    settings = get_settings()
    service = BenchPreviewService(CerebrasClient(settings))
    result = await service.generate_candidates(
        function_name="merge_intervals",
        language="python",
        agent_code=(
            "def merge_intervals(intervals):\n"
            "    if not intervals:\n"
            "        return []\n"
            "    intervals = sorted(intervals)\n"
            "    merged = [intervals[0]]\n"
            "    for start, end in intervals[1:]:\n"
            "        last = merged[-1]\n"
            "        if start <= last[1]:\n"
            "            last[1] = max(last[1], end)\n"
            "        else:\n"
            "            merged.append([start, end])\n"
            "    return merged\n"
        ),
        surrounding_context="Return merged closed intervals in ascending order.",
        conversation_history=[
            ConversationMessage(
                role="user",
                content="Prefer implementations that keep the current function signature and fit the existing project style.",
            )
        ],
        repo_context=RepoContextConfig(
            root_path=".",
            query="merge intervals function style project",
            focus_paths=["bench"],
        ),
    )
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
