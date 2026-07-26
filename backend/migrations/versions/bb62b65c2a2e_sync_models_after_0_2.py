"""sync models after 0.2 baseline

Revision ID: bb62b65c2a2e
Revises: 4c4d40c18081
Create Date: 2026-07-26 03:35:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "bb62b65c2a2e"
down_revision = "4c4d40c18081"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _columns(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    # Columns that were added to the ORM while the baseline migration still
    # represented the earlier 0.2 schema. Keep them nullable so existing local
    # teacher databases upgrade without data backfill. The existence checks make
    # the migration safe for pre-Alembic databases that may already have been
    # created via Base.metadata.create_all by the defensive fallback path.
    task_columns = _columns("tasks")
    task_missing = {"max_score", "rubric"} - task_columns
    if task_missing:
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            if "max_score" in task_missing:
                batch_op.add_column(sa.Column("max_score", sa.Float(), nullable=True))
            if "rubric" in task_missing:
                batch_op.add_column(sa.Column("rubric", sa.JSON(), nullable=True))

    sheet_columns = _columns("scanned_sheets")
    if "answer_crops_json" not in sheet_columns:
        with op.batch_alter_table("scanned_sheets", schema=None) as batch_op:
            batch_op.add_column(sa.Column("answer_crops_json", sa.JSON(), nullable=True))

    review_columns = _columns("review_decisions")
    review_missing = {"score", "rubric_result"} - review_columns
    if review_missing:
        with op.batch_alter_table("review_decisions", schema=None) as batch_op:
            if "score" in review_missing:
                batch_op.add_column(sa.Column("score", sa.Float(), nullable=True))
            if "rubric_result" in review_missing:
                batch_op.add_column(sa.Column("rubric_result", sa.JSON(), nullable=True))

    if not _table_exists("session_presets"):
        op.create_table(
            "session_presets",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("class_id", sa.Integer(), nullable=True),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("template_id", sa.Integer(), nullable=True),
            sa.Column("camera_profile_id", sa.Integer(), nullable=True),
            sa.Column("expected_sheet_count", sa.Integer(), nullable=False),
            sa.Column("config_override", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["camera_profile_id"], ["camera_profiles.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["class_id"], ["class_groups.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["template_id"], ["form_templates.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
    session_preset_indexes = _indexes("session_presets")
    missing_session_preset_indexes = {
        "ix_session_presets_class_id",
        "ix_session_presets_task_id",
    } - session_preset_indexes
    if missing_session_preset_indexes:
        with op.batch_alter_table("session_presets", schema=None) as batch_op:
            if "ix_session_presets_class_id" in missing_session_preset_indexes:
                batch_op.create_index(batch_op.f("ix_session_presets_class_id"), ["class_id"], unique=False)
            if "ix_session_presets_task_id" in missing_session_preset_indexes:
                batch_op.create_index(batch_op.f("ix_session_presets_task_id"), ["task_id"], unique=False)

    if not _table_exists("share_tokens"):
        op.create_table(
            "share_tokens",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.Column("student_id", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("note", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    share_token_indexes = _indexes("share_tokens")
    missing_share_token_indexes = {"ix_share_tokens_student_id", "ix_share_tokens_token"} - share_token_indexes
    if missing_share_token_indexes:
        with op.batch_alter_table("share_tokens", schema=None) as batch_op:
            if "ix_share_tokens_student_id" in missing_share_token_indexes:
                batch_op.create_index(batch_op.f("ix_share_tokens_student_id"), ["student_id"], unique=False)
            if "ix_share_tokens_token" in missing_share_token_indexes:
                batch_op.create_index(batch_op.f("ix_share_tokens_token"), ["token"], unique=True)


def downgrade() -> None:
    if _table_exists("share_tokens"):
        share_token_indexes = _indexes("share_tokens")
        share_token_drop_indexes = {"ix_share_tokens_token", "ix_share_tokens_student_id"} & share_token_indexes
        if share_token_drop_indexes:
            with op.batch_alter_table("share_tokens", schema=None) as batch_op:
                if "ix_share_tokens_token" in share_token_drop_indexes:
                    batch_op.drop_index(batch_op.f("ix_share_tokens_token"))
                if "ix_share_tokens_student_id" in share_token_drop_indexes:
                    batch_op.drop_index(batch_op.f("ix_share_tokens_student_id"))
        op.drop_table("share_tokens")

    if _table_exists("session_presets"):
        session_preset_indexes = _indexes("session_presets")
        session_preset_drop_indexes = {
            "ix_session_presets_task_id",
            "ix_session_presets_class_id",
        } & session_preset_indexes
        if session_preset_drop_indexes:
            with op.batch_alter_table("session_presets", schema=None) as batch_op:
                if "ix_session_presets_task_id" in session_preset_drop_indexes:
                    batch_op.drop_index(batch_op.f("ix_session_presets_task_id"))
                if "ix_session_presets_class_id" in session_preset_drop_indexes:
                    batch_op.drop_index(batch_op.f("ix_session_presets_class_id"))
        op.drop_table("session_presets")

    review_columns = _columns("review_decisions")
    review_existing = {"score", "rubric_result"} & review_columns
    if review_existing:
        with op.batch_alter_table("review_decisions", schema=None) as batch_op:
            if "rubric_result" in review_existing:
                batch_op.drop_column("rubric_result")
            if "score" in review_existing:
                batch_op.drop_column("score")

    if "answer_crops_json" in _columns("scanned_sheets"):
        with op.batch_alter_table("scanned_sheets", schema=None) as batch_op:
            batch_op.drop_column("answer_crops_json")

    task_columns = _columns("tasks")
    task_existing = {"max_score", "rubric"} & task_columns
    if task_existing:
        with op.batch_alter_table("tasks", schema=None) as batch_op:
            if "rubric" in task_existing:
                batch_op.drop_column("rubric")
            if "max_score" in task_existing:
                batch_op.drop_column("max_score")
