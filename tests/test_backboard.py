"""Smoke test: confirm the Backboard API key + SDK work end-to-end."""
import asyncio
import os

from dotenv import load_dotenv
from backboard import BackboardClient

load_dotenv()


async def main():
    api_key = os.environ["BACKBOARD_API_KEY"]
    client = BackboardClient(api_key=api_key)

    # Send a message — thread and assistant are auto-created
    response = await client.send_message(
        "Hello! I'm excited to get started.",
        memory="Auto",
    )
    print("[1]", response.content)
    print("    thread_id:", response.thread_id)

    # Continue the conversation using the returned thread_id
    response = await client.send_message(
        "What can you help me with?",
        thread_id=response.thread_id,
    )
    print("[2]", response.content)


if __name__ == "__main__":
    asyncio.run(main())
