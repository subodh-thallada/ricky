import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");
const demoRoot = path.join(repoRoot, "bench-demo-workspace");
const srcRoot = path.join(demoRoot, "src");

const baselineFiles = {
  "demo.py": `def existing_demo_function():
    return 'demo'
`,
  "fibonacci_demo.py": `def fibonacci(n: int) -> int:
    """Bench demo: compare several correct Fibonacci implementations."""
    raise NotImplementedError("Ask Bench: implement fibonacci (test)")
`,
  "get_students_demo.py": `from dataclasses import dataclass


@dataclass
class Student:
    id: int
    name: str
    grade: int
    email: str


STUDENTS = [
    Student(1, "Ava Patel", 9, "ava@example.com"),
    Student(2, "Milo Chen", 10, "milo@example.com"),
    Student(3, "Sara Johnson", 9, "sara@example.com"),
    Student(4, "Leo Garcia", 11, "leo@example.com"),
]


def get_students(search: str | None = None, grade: int | None = None, limit: int = 50) -> list[dict[str, object]]:
    """Bench demo: there are several correct ways to implement this endpoint."""
    raise NotImplementedError("Ask Bench: implement a get students endpoint (test)")
`,
  "cache_profiles_demo.py": `PROFILE_SOURCE = {
    1: {"id": 1, "name": "Ava Patel", "role": "student"},
    2: {"id": 2, "name": "Milo Chen", "role": "student"},
    3: {"id": 3, "name": "Sara Johnson", "role": "admin"},
}


def fetch_profile_from_source(user_id: int) -> dict[str, object]:
    profile = PROFILE_SOURCE.get(user_id)
    if profile is None:
        raise KeyError(f"unknown user id: {user_id}")
    return dict(profile)


def get_user_profile(user_id: int) -> dict[str, object]:
    """Bench demo: compare a few correct caching strategies for this lookup."""
    raise NotImplementedError("Ask Bench: add caching to this profile loader (test)")
`,
  "validation_demo.py": `def validate_student_payload(payload: dict[str, object]) -> dict[str, object]:
    """Bench demo: several valid validation styles exist for the same payload."""
    raise NotImplementedError("Ask Bench: add validation to this payload parser (test)")
`,
  "student_enrollment_sync_demo.py": `from dataclasses import dataclass, field


@dataclass
class EnrollmentRecord:
    student_id: int
    section: str
    status: str
    advisor: str
    tags: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    active: list[EnrollmentRecord]
    added_ids: list[int]
    updated_ids: list[int]
    archived_ids: list[int]


def normalize_tags(tags: list[str]) -> list[str]:
    return sorted({tag.strip().lower() for tag in tags if tag.strip()})


def snapshot_record(record: EnrollmentRecord) -> tuple[str, str, tuple[str, ...]]:
    return (
        record.section.strip(),
        record.status.strip().lower(),
        tuple(normalize_tags(record.tags)),
    )


def sync_enrollments(
    existing: list[EnrollmentRecord],
    incoming: list[EnrollmentRecord],
    archive_missing: bool = False,
) -> SyncResult:
    """Bench demo: several correct sync strategies exist for this workflow."""
    raise NotImplementedError("Ask Bench: implement enrollment sync for this module (test)")


def summarize_sync(result: SyncResult) -> dict[str, int]:
    return {
        "active": len(result.active),
        "added": len(result.added_ids),
        "updated": len(result.updated_ids),
        "archived": len(result.archived_ids),
    }


def sample_existing_records() -> list[EnrollmentRecord]:
    return [
        EnrollmentRecord(101, "math-101", "active", "Dr. Singh", ["honors"]),
        EnrollmentRecord(102, "chem-201", "active", "Dr. Chen", ["lab"]),
        EnrollmentRecord(103, "hist-210", "waitlist", "Dr. Patel", []),
    ]


def sample_incoming_records() -> list[EnrollmentRecord]:
    return [
        EnrollmentRecord(101, "math-101", "active", "Dr. Singh", ["honors", "peer-tutor"]),
        EnrollmentRecord(102, "chem-201", "dropped", "Dr. Chen", ["lab"]),
        EnrollmentRecord(104, "art-110", "active", "Prof. Diaz", ["portfolio"]),
    ]
`,
};

mkdirSync(srcRoot, { recursive: true });
rmSync(path.join(demoRoot, "bench_preview"), { recursive: true, force: true });
rmSync(path.join(srcRoot, "__pycache__"), { recursive: true, force: true });

for (const [name, contents] of Object.entries(baselineFiles)) {
  writeFileSync(path.join(srcRoot, name), contents, "utf8");
}

console.log(`Reset Bench demo workspace at ${demoRoot}`);
