"""CRUD endpoints for classes, students, tasks and form templates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession, serialize_class, serialize_student
from app.models import ClassGroup, FormTemplate, ScannedSheet, Student, Task
from app.schemas import (
    ClassGroupCreate,
    ClassGroupOut,
    ClassGroupUpdate,
    FormTemplateCreate,
    FormTemplateOut,
    FormTemplateUpdate,
    StudentBulkCreate,
    StudentCreate,
    StudentOut,
    StudentUpdate,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)

router = APIRouter(tags=["catalog"])


# ------------------------------------------------------------------- classes


@router.get("/classes", response_model=list[ClassGroupOut])
def list_classes(db: DbSession) -> list[ClassGroupOut]:
    counts = dict(
        db.execute(select(Student.class_id, func.count(Student.id)).group_by(Student.class_id)).all()
    )
    groups = db.execute(select(ClassGroup).order_by(ClassGroup.name)).scalars().all()
    return [serialize_class(g, counts.get(g.id, 0)) for g in groups]


@router.post("/classes", response_model=ClassGroupOut, status_code=status.HTTP_201_CREATED)
def create_class(payload: ClassGroupCreate, db: DbSession) -> ClassGroupOut:
    group = ClassGroup(name=payload.name.strip(), school_year=payload.school_year.strip())
    db.add(group)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Класс «{payload.name}» уже существует") from exc
    db.refresh(group)
    return serialize_class(group, 0)


@router.get("/classes/{class_id}", response_model=ClassGroupOut)
def get_class(class_id: int, db: DbSession) -> ClassGroupOut:
    group = db.get(ClassGroup, class_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Класс не найден")
    return serialize_class(group)


@router.patch("/classes/{class_id}", response_model=ClassGroupOut)
def update_class(class_id: int, payload: ClassGroupUpdate, db: DbSession) -> ClassGroupOut:
    group = db.get(ClassGroup, class_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Класс не найден")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Класс с таким именем уже существует") from exc
    db.refresh(group)
    return serialize_class(group)


@router.delete("/classes/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_class(class_id: int, db: DbSession) -> None:
    group = db.get(ClassGroup, class_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Класс не найден")
    db.delete(group)
    db.commit()


# ------------------------------------------------------------------ students


@router.get("/students", response_model=list[StudentOut])
def list_students(
    db: DbSession,
    class_id: int | None = None,
    search: str | None = None,
    active_only: bool = False,
) -> list[StudentOut]:
    query = select(Student)
    if class_id is not None:
        query = query.where(Student.class_id == class_id)
    if active_only:
        query = query.where(Student.is_active.is_(True))
    if search:
        pattern = f"%{search.strip().lower()}%"
        query = query.where(
            func.lower(Student.external_id).like(pattern)
            | func.lower(Student.first_name).like(pattern)
            | func.lower(Student.last_name).like(pattern)
        )
    students = db.execute(query.order_by(Student.last_name, Student.first_name, Student.external_id)).scalars().all()
    return [serialize_student(s) for s in students]


@router.post("/students", response_model=StudentOut, status_code=status.HTTP_201_CREATED)
def create_student(payload: StudentCreate, db: DbSession) -> StudentOut:
    if payload.class_id and db.get(ClassGroup, payload.class_id) is None:
        raise HTTPException(status_code=400, detail="Указанный класс не существует")
    student = Student(**payload.model_dump())
    student.external_id = student.external_id.strip()
    db.add(student)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Ученик «{payload.external_id}» уже существует") from exc
    db.refresh(student)
    return serialize_student(student)


@router.post("/students/bulk", response_model=list[StudentOut], status_code=status.HTTP_201_CREATED)
def bulk_create_students(payload: StudentBulkCreate, db: DbSession) -> list[StudentOut]:
    """Import a whole class at once; existing external_ids are updated."""
    if db.get(ClassGroup, payload.class_id) is None:
        raise HTTPException(status_code=400, detail="Указанный класс не существует")

    result: list[Student] = []
    for item in payload.students:
        external_id = item.external_id.strip()
        if not external_id:
            continue
        existing = db.execute(
            select(Student).where(func.lower(Student.external_id) == external_id.lower())
        ).scalar_one_or_none()
        if existing is not None:
            existing.first_name = item.first_name or existing.first_name
            existing.last_name = item.last_name or existing.last_name
            existing.class_id = payload.class_id
            existing.is_active = item.is_active
            result.append(existing)
        else:
            student = Student(
                external_id=external_id,
                first_name=item.first_name,
                last_name=item.last_name,
                class_id=payload.class_id,
                is_active=item.is_active,
            )
            db.add(student)
            result.append(student)
    db.commit()
    for student in result:
        db.refresh(student)
    return [serialize_student(s) for s in result]


@router.patch("/students/{student_id}", response_model=StudentOut)
def update_student(student_id: int, payload: StudentUpdate, db: DbSession) -> StudentOut:
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(student, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ученик с таким ID уже существует") from exc
    db.refresh(student)
    return serialize_student(student)


@router.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(student_id: int, db: DbSession) -> None:
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    linked = db.execute(
        select(func.count(ScannedSheet.id)).where(ScannedSheet.student_id == student_id)
    ).scalar_one()
    if linked:
        # keep the archive intact – deactivate instead of destroying history
        student.is_active = False
        db.commit()
        return
    db.delete(student)
    db.commit()


# --------------------------------------------------------------------- tasks


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: DbSession, search: str | None = None) -> list[TaskOut]:
    query = select(Task)
    if search:
        pattern = f"%{search.strip().lower()}%"
        query = query.where(
            func.lower(Task.title).like(pattern)
            | func.lower(Task.external_id).like(pattern)
            | func.lower(Task.topic).like(pattern)
        )
    tasks = db.execute(query.order_by(Task.created_at.desc())).scalars().all()
    return [TaskOut.model_validate(t) for t in tasks]


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, db: DbSession) -> TaskOut:
    task = Task(**payload.model_dump())
    task.external_id = task.external_id.strip()
    db.add(task)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Задание «{payload.external_id}» уже существует") from exc
    db.refresh(task)
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: DbSession) -> TaskOut:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    return TaskOut.model_validate(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate, db: DbSession) -> TaskOut:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Задание с таким ID уже существует") from exc
    db.refresh(task)
    return TaskOut.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: DbSession) -> None:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    db.delete(task)
    db.commit()


# ----------------------------------------------------------------- templates


def _template_payload(payload: FormTemplateCreate | FormTemplateUpdate) -> dict:
    data = payload.model_dump(exclude_unset=True)
    if data.get("qr_region") is not None:
        data["qr_region"] = dict(data["qr_region"])
    if data.get("answer_regions") is not None:
        data["answer_regions"] = [dict(r) for r in data["answer_regions"]]
    return data


@router.get("/templates", response_model=list[FormTemplateOut])
def list_templates(db: DbSession) -> list[FormTemplateOut]:
    templates = db.execute(select(FormTemplate).order_by(FormTemplate.name)).scalars().all()
    return [FormTemplateOut.model_validate(t) for t in templates]


@router.post("/templates", response_model=FormTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(payload: FormTemplateCreate, db: DbSession) -> FormTemplateOut:
    data = _template_payload(payload)
    ratio = data.pop("aspect_ratio", None)
    template = FormTemplate(**data)
    template.aspect_ratio = float(ratio) if ratio else (template.page_width_mm / max(template.page_height_mm, 1e-6))
    if template.is_default:
        for other in db.execute(select(FormTemplate).where(FormTemplate.is_default.is_(True))).scalars():
            other.is_default = False
    db.add(template)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Шаблон с таким названием уже существует") from exc
    db.refresh(template)
    return FormTemplateOut.model_validate(template)


@router.get("/templates/{template_id}", response_model=FormTemplateOut)
def get_template(template_id: int, db: DbSession) -> FormTemplateOut:
    template = db.get(FormTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    return FormTemplateOut.model_validate(template)


@router.patch("/templates/{template_id}", response_model=FormTemplateOut)
def update_template(template_id: int, payload: FormTemplateUpdate, db: DbSession) -> FormTemplateOut:
    template = db.get(FormTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    data = _template_payload(payload)
    if data.pop("is_default", False):
        for other in db.execute(select(FormTemplate).where(FormTemplate.is_default.is_(True))).scalars():
            other.is_default = False
        template.is_default = True
    for field, value in data.items():
        setattr(template, field, value)
    if "page_width_mm" in data or "page_height_mm" in data:
        if not data.get("aspect_ratio"):
            template.aspect_ratio = template.page_width_mm / max(template.page_height_mm, 1e-6)
    db.commit()
    db.refresh(template)
    return FormTemplateOut.model_validate(template)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, db: DbSession) -> None:
    template = db.get(FormTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    db.delete(template)
    db.commit()
