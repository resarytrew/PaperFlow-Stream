"""Command line helpers: ``python -m app.cli <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal, init_db


def cmd_init(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    init_db()
    with SessionLocal() as db:
        from app.services.seed import ensure_default_template

        template = ensure_default_template(db)
    print(f"Database ready: {settings.resolved_database_url()}")
    print(f"Storage:        {settings.storage_dir}")
    print(f"Template:       {template.name}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    init_db()
    with SessionLocal() as db:
        from app.services.seed import seed_demo_data

        result = seed_demo_data(db, args.class_name, args.school_year)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_forms(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models import ClassGroup, Student, Task
    from app.services.form_generator import FormSpec, build_sheet_uid, generate_forms_pdf

    init_db()
    with SessionLocal() as db:
        group = db.execute(select(ClassGroup).where(ClassGroup.name == args.class_name)).scalar_one_or_none()
        if group is None:
            print(f"class not found: {args.class_name}", file=sys.stderr)
            return 1
        task = db.execute(select(Task).where(Task.external_id == args.task)).scalar_one_or_none()
        if task is None:
            print(f"task not found: {args.task}", file=sys.stderr)
            return 1
        students = db.execute(
            select(Student).where(Student.class_id == group.id, Student.is_active.is_(True)).order_by(Student.external_id)
        ).scalars().all()
        if not students:
            print("no active students in class", file=sys.stderr)
            return 1

        specs = [
            FormSpec(
                student_external_id=s.external_id,
                student_name=s.display_name,
                class_name=group.name,
                task_external_id=task.external_id,
                task_title=task.title,
                sheet_uid=build_sheet_uid(s.external_id, task.external_id),
            )
            for s in students
        ]

    pdf = generate_forms_pdf(specs, forms_per_page=args.per_page)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pdf)
    print(f"wrote {len(specs)} forms to {output}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay a folder of frames (or synthetic scenario) without a camera."""
    from app.testing.replay import replay_directory, replay_scenario

    if args.scenario:
        report = replay_scenario(args.scenario, repeats=args.repeats)
    else:
        report = replay_directory(Path(args.frames))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_retention(args: argparse.Namespace) -> int:
    from app.services.settings_service import load_config
    from app.services.storage import get_storage

    init_db()
    with SessionLocal() as db:
        config = load_config(db)
    removed = get_storage().apply_retention(config.privacy.file_retention_days)
    print(f"retention {config.privacy.file_retention_days} days → removed {removed} files")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paperflow", description="PaperFlow Stream utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create database and folders")
    p_init.set_defaults(func=cmd_init)

    p_seed = sub.add_parser("seed", help="insert demo class, students and tasks")
    p_seed.add_argument("--class-name", default="9Б")
    p_seed.add_argument("--school-year", default="2025/2026")
    p_seed.set_defaults(func=cmd_seed)

    p_forms = sub.add_parser("forms", help="generate a PDF with QR forms")
    p_forms.add_argument("--class-name", default="9Б")
    p_forms.add_argument("--task", default="history-09-04")
    p_forms.add_argument("--per-page", type=int, default=3)
    p_forms.add_argument("--output", default="forms.pdf")
    p_forms.set_defaults(func=cmd_forms)

    p_replay = sub.add_parser("replay", help="replay frames through the pipeline (no camera)")
    p_replay.add_argument("--frames", help="directory with ordered image files")
    p_replay.add_argument("--scenario", help="synthetic scenario name")
    p_replay.add_argument("--repeats", type=int, default=1)
    p_replay.set_defaults(func=cmd_replay)

    p_ret = sub.add_parser("retention", help="apply the file retention policy")
    p_ret.set_defaults(func=cmd_retention)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
