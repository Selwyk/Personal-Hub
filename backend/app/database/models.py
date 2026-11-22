import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Table,
    Column, Text, UniqueConstraint, text, Computed, Index,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Identified:
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("uuidv7()"))


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class User(Identified, Created, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("singleton = 1", name="user_singleton_one"),)
    singleton: Mapped[int] = mapped_column(Integer, default=1, unique=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    security_version: Mapped[int] = mapped_column(Integer, default=1)
    mfa_initialized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Device(Identified, Created, Base):
    __tablename__ = "trusted_devices"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    security_version: Mapped[int] = mapped_column(Integer)


class Session(Identified, Created, Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trusted_devices.id"))
    level: Mapped[str] = mapped_column(String(20), default="anonymous")
    security_version: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    step_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TotpFactor(Identified, Created, Base):
    __tablename__ = "totp_factors"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    secret_encrypted: Mapped[str] = mapped_column(Text)
    last_step: Mapped[int] = mapped_column(Integer, default=-1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Credential(Identified, Created, Base):
    __tablename__ = "webauthn_credentials"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    credential_id: Mapped[str] = mapped_column(Text, unique=True)
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(100))
    transports: Mapped[list] = mapped_column(JSONB, default=list)
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Challenge(Identified, Created, Base):
    __tablename__ = "challenges"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"))
    purpose: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryCode(Identified, Created, Base):
    __tablename__ = "recovery_codes"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollectorToken(Identified, Created, Base):
    __tablename__ = "collector_tokens"
    __table_args__ = (CheckConstraint("scope = 'journal:candidate:create'", name="token_scope"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(50), default="journal:candidate:create")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Identified, Created, Base):
    __tablename__ = "security_events"
    kind: Mapped[str] = mapped_column(String(60))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class RateLimit(Base):
    __tablename__ = "auth_rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Project(Identified, Created, Base):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120), unique=True)


class Tag(Identified, Created, Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120), unique=True)


entry_projects = Table("entry_projects", Base.metadata,
    Column("entry_id", ForeignKey("journal_entries.id", ondelete="CASCADE"), primary_key=True),
    Column("project_id", ForeignKey("projects.id"), primary_key=True))
entry_tags = Table("entry_tags", Base.metadata,
    Column("entry_id", ForeignKey("journal_entries.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True))


class Entry(Identified, Created, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint("status IN ('draft','published')", name="entry_status"),
        CheckConstraint("evidence_level IN ('confirmed','inferred','unverified')", name="entry_evidence"),
        CheckConstraint("activity_type IN ('research','proposal','implementation','deployment','test','documentation','incident')", name="entry_activity"),
        CheckConstraint("(status = 'published') = (published_at IS NOT NULL)", name="publication_consistency"),
        Index("entry_search_es", "search_es", postgresql_using="gin"),
        Index("entry_search_en", "search_en", postgresql_using="gin"),
        Index("entry_search_trgm", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
    )
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(240))
    content_markdown: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    evidence_level: Mapped[str] = mapped_column(String(20), default="unverified")
    activity_type: Mapped[str] = mapped_column(String(30), default="documentation")
    technologies: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    search_es: Mapped[str] = mapped_column(TSVECTOR, Computed("setweight(to_tsvector('spanish', coalesce(title,'')), 'A') || setweight(to_tsvector('spanish', coalesce(summary,'')), 'B') || setweight(to_tsvector('spanish', coalesce(content_markdown,'')), 'C')", persisted=True))
    search_en: Mapped[str] = mapped_column(TSVECTOR, Computed("setweight(to_tsvector('english', coalesce(title,'')), 'A') || setweight(to_tsvector('english', coalesce(summary,'')), 'B') || setweight(to_tsvector('english', coalesce(content_markdown,'')), 'C')", persisted=True))
    search_text: Mapped[str] = mapped_column(Text, Computed("lower(title || ' ' || summary || ' ' || content_markdown)", persisted=True))
    projects: Mapped[list[Project]] = relationship(secondary=entry_projects, lazy="selectin")
    tags: Mapped[list[Tag]] = relationship(secondary=entry_tags, lazy="selectin")
    references: Mapped[list["SourceReference"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


task_projects = Table("task_projects", Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("project_id", ForeignKey("projects.id"), primary_key=True))
task_tags = Table("task_tags", Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True))


class Task(Identified, Created, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="task_title_nonblank"),
        CheckConstraint("status IN ('todo','in_progress','completed')", name="task_status"),
        CheckConstraint("priority IN ('low','normal','high','urgent')", name="task_priority"),
        CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)", name="task_completion"),
        CheckConstraint("version >= 1", name="task_version"),
        CheckConstraint("parent_id <> id", name="task_no_self_parent"),
        Index("task_status_due", "status", "due_on", "id"),
    )
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="todo", server_default="todo")
    priority: Mapped[str] = mapped_column(String(20), default="normal", server_default="normal")
    due_on: Mapped[date | None] = mapped_column(Date)
    # Reserved for a future validated subtask API; not writable in V0.1.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    projects: Mapped[list[Project]] = relationship(secondary=task_projects, lazy="selectin")
    tags: Mapped[list[Tag]] = relationship(secondary=task_tags, lazy="selectin")


class SourceReference(Identified, Base):
    __tablename__ = "source_references"
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(100))
    source_reference: Mapped[str] = mapped_column(Text)
    source_date: Mapped[date | None] = mapped_column(Date)
    external_id: Mapped[str | None] = mapped_column(String(240))
    evidence_level: Mapped[str] = mapped_column(String(20))


class EntryRelation(Base):
    __tablename__ = "entry_relations"
    __table_args__ = (CheckConstraint("entry_id <> related_id", name="no_self_relation"),)
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), primary_key=True)
    related_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), primary_key=True)


class Batch(Identified, Created, Base):
    __tablename__ = "ingest_batches"
    import_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    collector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("collector_tokens.id"))
    results: Mapped[list] = mapped_column(JSONB, default=list)


class Candidate(Identified, Created, Base):
    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint("source_type", "source_reference", "external_id", name="candidate_identity"),
        UniqueConstraint("source_type", "source_reference", "content_hash", name="candidate_content"),
        CheckConstraint("status IN ('pending_review','accepted','discarded')", name="candidate_status"),
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingest_batches.id"))
    source_type: Mapped[str] = mapped_column(String(100))
    source_reference: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(240))
    content_hash: Mapped[str] = mapped_column(String(64))
    original_payload: Mapped[dict] = mapped_column(JSONB)
    review_payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=1)
