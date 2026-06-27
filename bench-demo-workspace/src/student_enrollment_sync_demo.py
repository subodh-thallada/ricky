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
    # Build a lookup for existing records by (student_id, section)
    existing_lookup: dict[tuple[int, str], EnrollmentRecord] = {
        (rec.student_id, rec.section.strip()): rec for rec in existing
    }

    added_ids: list[int] = []
    updated_ids: list[int] = []
    seen_keys: set[tuple[int, str]] = set()

    for inc in incoming:
        key = (inc.student_id, inc.section.strip())
        seen_keys.add(key)
        if key not in existing_lookup:
            # New enrollment
            added_ids.append(inc.student_id)
            existing.append(inc)
            existing_lookup[key] = inc
        else:
            exist = existing_lookup[key]
            if snapshot_record(exist) != snapshot_record(inc):
                updated_ids.append(inc.student_id)
                # Update mutable fields in place
                exist.status = inc.status
                exist.advisor = inc.advisor
                exist.tags = inc.tags

    archived_ids: list[int] = []
    if archive_missing:
        # Identify records to archive (not seen in incoming)
        to_archive = [rec for rec in existing if (rec.student_id, rec.section.strip()) not in seen_keys]
        for rec in to_archive:
            archived_ids.append(rec.student_id)
            existing.remove(rec)

    return SyncResult(active=existing, added_ids=added_ids, updated_ids=updated_ids, archived_ids=archived_ids)def summarize_sync(result: SyncResult) -> dict[str, int]:
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
