"""Default template and demo data seeding."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ClassGroup, FormTemplate, Student, Task

logger = logging.getLogger(__name__)

#: Geometry of the built-in standard form, expressed as fractions of the
#: rectified sheet. Matches the layout produced by ``form_generator``.
DEFAULT_TEMPLATE = {
    "name": "Стандартный бланк A4/3",
    "description": "Компактный бланк: QR слева сверху, три линии для ответа. 3 бланка на лист A4.",
    "page_width_mm": 190.0,
    "page_height_mm": 89.0,
    "qr_region": {"x": 0.005, "y": 0.02, "w": 0.20, "h": 0.42, "label": "QR"},
    "answer_regions": [{"x": 0.02, "y": 0.36, "w": 0.96, "h": 0.62, "label": "Ответ"}],
    "is_default": True,
}


def ensure_default_template(db: Session) -> FormTemplate:
    """Create the built-in form template on first run."""
    existing = db.execute(
        select(FormTemplate).where(FormTemplate.name == DEFAULT_TEMPLATE["name"])
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    template = FormTemplate(
        name=DEFAULT_TEMPLATE["name"],
        description=DEFAULT_TEMPLATE["description"],
        page_width_mm=DEFAULT_TEMPLATE["page_width_mm"],
        page_height_mm=DEFAULT_TEMPLATE["page_height_mm"],
        aspect_ratio=DEFAULT_TEMPLATE["page_width_mm"] / DEFAULT_TEMPLATE["page_height_mm"],
        qr_region=DEFAULT_TEMPLATE["qr_region"],
        answer_regions=DEFAULT_TEMPLATE["answer_regions"],
        is_default=True,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    logger.info("created default form template")
    return template


DEMO_STUDENTS = [
    ("9B-01", "Анна", "Авдеева"),
    ("9B-02", "Борис", "Белов"),
    ("9B-03", "Вера", "Виноградова"),
    ("9B-04", "Глеб", "Гаврилов"),
    ("9B-05", "Дарья", "Дмитриева"),
    ("9B-06", "Егор", "Ефимов"),
    ("9B-07", "Жанна", "Жукова"),
    ("9B-08", "Захар", "Зайцев"),
    ("9B-09", "Ирина", "Иванова"),
    ("9B-10", "Кирилл", "Ковалёв"),
    ("9B-11", "Лариса", "Лебедева"),
    ("9B-12", "Максим", "Морозов"),
    ("9B-13", "Нина", "Никитина"),
    ("9B-14", "Олег", "Орлов"),
    ("9B-15", "Полина", "Петрова"),
    ("9B-16", "Роман", "Романов"),
    ("9B-17", "Светлана", "Смирнова"),
    ("9B-18", "Тимур", "Тихонов"),
    ("9B-19", "Ульяна", "Уварова"),
    ("9B-20", "Фёдор", "Фомин"),
    ("9B-21", "Хава", "Хабибова"),
    ("9B-22", "Цветана", "Цветкова"),
    ("9B-23", "Чеслав", "Чернов"),
    ("9B-24", "Шамиль", "Шакиров"),
    ("9B-25", "Эдуард", "Эдуардов"),
    ("9B-26", "Юлия", "Юрьева"),
    ("9B-27", "Ярослав", "Яковлев"),
    ("9B-28", "Алиса", "Абрамова"),
    ("9B-29", "Виктор", "Волков"),
    ("9B-30", "Галина", "Громова"),
]

DEMO_TASKS = [
    {
        "external_id": "history-09-04",
        "title": "Причины отмены крепостного права",
        "subject": "История",
        "topic": "Реформы Александра II",
        "description": "Назовите причины отмены крепостного права в России.",
        "expected_answer": (
            "Экономическая отсталость, поражение в Крымской войне, крестьянские волнения, "
            "необходимость модернизации, давление общественного мнения"
        ),
    },
    {
        "external_id": "history-09-05",
        "title": "Земская реформа 1864 года",
        "subject": "История",
        "topic": "Реформы Александра II",
        "description": "Что изменила земская реформа?",
        "expected_answer": "Местное самоуправление, земские собрания, управы, школы и больницы",
    },
]


def seed_demo_data(db: Session, class_name: str = "9Б", school_year: str = "2025/2026") -> dict:
    """Create a demo class, 30 students and two tasks (idempotent)."""
    group = db.execute(select(ClassGroup).where(ClassGroup.name == class_name)).scalar_one_or_none()
    if group is None:
        group = ClassGroup(name=class_name, school_year=school_year)
        db.add(group)
        db.commit()
        db.refresh(group)

    created_students = 0
    for external_id, first_name, last_name in DEMO_STUDENTS:
        existing = db.execute(
            select(Student).where(func.lower(Student.external_id) == external_id.lower())
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Student(
                    external_id=external_id,
                    first_name=first_name,
                    last_name=last_name,
                    class_id=group.id,
                    is_active=True,
                )
            )
            created_students += 1

    created_tasks = 0
    for payload in DEMO_TASKS:
        existing_task = db.execute(
            select(Task).where(Task.external_id == payload["external_id"])
        ).scalar_one_or_none()
        if existing_task is None:
            db.add(Task(**payload))
            created_tasks += 1

    db.commit()
    ensure_default_template(db)

    return {
        "classId": group.id,
        "className": group.name,
        "studentsCreated": created_students,
        "tasksCreated": created_tasks,
    }
