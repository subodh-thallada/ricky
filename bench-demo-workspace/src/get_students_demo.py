from dataclasses import dataclass


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
