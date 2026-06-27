from dataclasses import dataclass, field


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
