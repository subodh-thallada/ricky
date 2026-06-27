import json

from fastapi.testclient import TestClient

from bench.main import app


def main() -> None:
    client = TestClient(app)
    thread = client.post(
        "/threads",
        json={
            "title": "router demo",
            "repo_context": {
                "root_path": ".",
                "focus_paths": ["bench"],
                "query": "thread router code sidebar",
                "max_files": 6,
                "max_file_chars": 1000,
                "max_total_chars": 6000,
            },
        },
    ).json()

    text_reply = client.post(
        f"/threads/{thread['thread_id']}/reply",
        json={"prompt": "What does this backend do right now?"},
    ).json()
    code_reply = client.post(
        f"/threads/{thread['thread_id']}/reply",
        json={"prompt": "Write a small Python helper function that formats provider status labels."},
    ).json()

    print(json.dumps({"text_reply": text_reply, "code_reply": code_reply}, indent=2))


if __name__ == "__main__":
    main()
