"""Add opt-in Tasks tables; existing rows and schemas are unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "b261121"
down_revision = "a172001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tasks",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("uuidv7()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="todo"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("parent_id", sa.UUID(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="task_title_nonblank"),
        sa.CheckConstraint("status IN ('todo','in_progress','completed')", name="task_status"),
        sa.CheckConstraint("priority IN ('low','normal','high','urgent')", name="task_priority"),
        sa.CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)", name="task_completion"),
        sa.CheckConstraint("version >= 1", name="task_version"),
        sa.CheckConstraint("parent_id <> id", name="task_no_self_parent"))
    op.create_index("task_status_due", "tasks", ["status", "due_on", "id"])
    op.create_index("ix_tasks_parent_id", "tasks", ["parent_id"])
    for table, target in (("task_projects", "projects"), ("task_tags", "tags")):
        column = "project_id" if target == "projects" else "tag_id"
        op.create_table(table,
            sa.Column("task_id", sa.UUID(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
            sa.Column(column, sa.UUID(), sa.ForeignKey(f"{target}.id"), primary_key=True))


def downgrade():
    # Administrative/destructive: never run against existing user data without authorization.
    op.drop_table("task_tags")
    op.drop_table("task_projects")
    op.drop_index("ix_tasks_parent_id", table_name="tasks")
    op.drop_index("task_status_due", table_name="tasks")
    op.drop_table("tasks")
