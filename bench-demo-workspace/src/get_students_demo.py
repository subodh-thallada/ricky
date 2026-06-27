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


from dataclasses import asdict

from dataclasses import asdict

from dataclasses import asdict

def query_students(search: str | None, grade: int | None, limit: int) -> list[Student]:
    results = STUDENTS
    if search:
        lowered = search.lower()
        results = [student for student in results if lowered in student.name.lower()]
    if grade is not None:
        results = [student for student in results if student.grade == grade]
    return results[:limit]

from dataclasses import asdict

def get_students(search: str | None = None, grade: int | None = None, limit: int = 50) -> list[dict[str, object]]:
    items = STUDENTS
    if search:
        lowered = search.lower()
        items = [student for student in items if lowered in student.name.lower()]
    if grade is not None:
        items = [student for student in items if student.grade == grade]
    return [asdict(student) for student in items[:limit]]