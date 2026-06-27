from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

from bench.schemas import ConversationMessage, RepoContextConfig, StoredThread


class ThreadStore:
    def __init__(self, storage_path: str = ".bench_threads.json"):
        self.storage_path = Path(storage_path)
        self._lock = Lock()
        self._threads: dict[str, StoredThread] = {}
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._threads = {
            item["thread_id"]: StoredThread.model_validate(item)
            for item in data.get("threads", [])
        }

    def _save(self) -> None:
        payload = {"threads": [thread.model_dump() for thread in self._threads.values()]}
        self.storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def create_thread(
        self,
        *,
        title: str | None = None,
        repo_context: RepoContextConfig | None = None,
    ) -> StoredThread:
        with self._lock:
            thread = StoredThread(
                thread_id=str(uuid4()),
                title=title,
                repo_context=repo_context,
            )
            self._threads[thread.thread_id] = thread
            self._save()
            return thread

    def get_thread(self, thread_id: str) -> StoredThread | None:
        return self._threads.get(thread_id)

    def append_message(self, thread_id: str, message: ConversationMessage) -> StoredThread:
        with self._lock:
            thread = self._threads[thread_id]
            thread.messages.append(message)
            self._save()
            return thread

    def append_messages(
        self,
        thread_id: str,
        messages: list[ConversationMessage],
    ) -> StoredThread:
        with self._lock:
            thread = self._threads[thread_id]
            thread.messages.extend(messages)
            self._save()
            return thread

    def update_repo_context(
        self,
        thread_id: str,
        repo_context: RepoContextConfig | None,
    ) -> StoredThread:
        with self._lock:
            thread = self._threads[thread_id]
            thread.repo_context = repo_context
            self._save()
            return thread
