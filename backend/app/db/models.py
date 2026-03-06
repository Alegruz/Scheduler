"""
SQLAlchemy ORM models for Life Scheduler.

Domain entities (canonical source of truth — not Google Calendar):
  User, CalendarAccount, Goal, PolicyProfile, TaskTemplate, TaskInstance,
  SchedulePlan, TimeBlock, Constraint, ScheduleRevision, AuditEvent,
  NotificationEvent, RecoveryRule, DomainExpert, ContextSignal,
  EnergyProfile, SyncMapping
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, relationship
import uuid as _uuid


# Make BigInteger work as autoincrement primary key in SQLite (for tests)
@compiles(BigInteger, "sqlite")
def compile_big_int_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "INTEGER"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


def _uuid_pk():
    return Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_uuid.uuid4,
        nullable=False,
    )


def _now():
    return Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated():
    return Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConstraintKind(str, enum.Enum):
    HARD = "hard"
    SOFT = "soft"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"
    MISSED = "missed"


class BlockStatus(str, enum.Enum):
    PROPOSED = "proposed"
    COMMITTED = "committed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class SchedulingClass(str, enum.Enum):
    HARD_REAL_TIME = "hard_real_time"       # fixed, cannot move (meetings, appointments)
    FIXED_RECURRING = "fixed_recurring"     # same slot every day/week
    DEADLINE_DRIVEN = "deadline_driven"     # must finish before deadline
    QUOTA_BASED = "quota_based"             # fill N hours per week, flexible when
    OPPORTUNISTIC = "opportunistic"         # fill any gap, low priority
    RECOVERY = "recovery"                   # repair missed tasks


class RecurrenceFrequency(str, enum.Enum):
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    CUSTOM = "custom"  # RRULE string


class AuditEventKind(str, enum.Enum):
    BLOCK_CREATED = "block_created"
    BLOCK_MOVED = "block_moved"
    BLOCK_DELETED = "block_deleted"
    BLOCK_DONE = "block_done"
    BLOCK_SKIPPED = "block_skipped"
    PLAN_GENERATED = "plan_generated"
    PLAN_COMMITTED = "plan_committed"
    POLICY_CHANGED = "policy_changed"
    CONSTRAINT_VIOLATED = "constraint_violated"
    REPAIR_TRIGGERED = "repair_triggered"
    GCAL_SYNCED = "gcal_synced"
    AI_SUGGESTION = "ai_suggestion"


class NotificationKind(str, enum.Enum):
    UPCOMING_BLOCK = "upcoming_block"
    BLOCK_START = "block_start"
    BLOCK_DONE_PROMPT = "block_done_prompt"
    MISSED_TASK = "missed_task"
    PLAN_READY = "plan_ready"
    REPAIR_COMPLETE = "repair_complete"


class SyncDirection(str, enum.Enum):
    PUSH = "push"    # internal → gcal
    PULL = "pull"    # gcal → internal (read-only context import)
    BIDIRECTIONAL = "bidirectional"


# ---------------------------------------------------------------------------
# User & Auth
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)
    email: str = Column(String(320), nullable=False, unique=True, index=True)
    hashed_password: str = Column(String(256), nullable=False)
    display_name: str = Column(String(128), nullable=False)
    timezone: str = Column(String(64), nullable=False, default="UTC")
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at = _now()
    updated_at = _updated()

    # Relationships
    policy_profiles = relationship("PolicyProfile", back_populates="user", cascade="all, delete-orphan")
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    task_templates = relationship("TaskTemplate", back_populates="user", cascade="all, delete-orphan")
    task_instances = relationship("TaskInstance", back_populates="user", cascade="all, delete-orphan")
    schedule_plans = relationship("SchedulePlan", back_populates="user", cascade="all, delete-orphan")
    calendar_accounts = relationship("CalendarAccount", back_populates="user", cascade="all, delete-orphan")
    energy_profile = relationship("EnergyProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    audit_events = relationship("AuditEvent", back_populates="user", cascade="all, delete-orphan")
    notification_events = relationship("NotificationEvent", back_populates="user", cascade="all, delete-orphan")
    context_signals = relationship("ContextSignal", back_populates="user", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Calendar account (Google Calendar OAuth tokens)
# ---------------------------------------------------------------------------

class CalendarAccount(Base):
    __tablename__ = "calendar_accounts"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: str = Column(String(32), nullable=False, default="google")
    external_account_email: str = Column(String(320), nullable=True)
    access_token: str = Column(Text, nullable=True)   # encrypted at rest in production
    refresh_token: str = Column(Text, nullable=True)  # encrypted at rest in production
    token_expiry = Column(DateTime(timezone=True), nullable=True)
    sync_direction: SyncDirection = Column(
        Enum(SyncDirection), nullable=False, default=SyncDirection.PUSH
    )
    target_calendar_id: str = Column(String(256), nullable=True)  # gcal calendar id to push into
    is_active: bool = Column(Boolean, nullable=False, default=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _now()
    updated_at = _updated()

    user = relationship("User", back_populates="calendar_accounts")
    sync_mappings = relationship("SyncMapping", back_populates="calendar_account", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_account_email", name="uq_calendar_account"),
    )


# ---------------------------------------------------------------------------
# Policy & Constraints
# ---------------------------------------------------------------------------

class PolicyProfile(Base):
    """
    A named policy profile (e.g. "Strict Coach", "Relaxed Weekend").
    Users can switch profiles or schedule automatic switches.
    """
    __tablename__ = "policy_profiles"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: str = Column(String(128), nullable=False)
    description: str = Column(Text, nullable=True)
    is_default: bool = Column(Boolean, nullable=False, default=False)
    # JSON blob for flexible policy configuration
    policy_config: dict = Column(JSON, nullable=False, default=dict)
    created_at = _now()
    updated_at = _updated()

    user = relationship("User", back_populates="policy_profiles")
    constraints = relationship("Constraint", back_populates="policy_profile", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_policy_profile_name"),
    )


class Constraint(Base):
    """
    A single scheduling constraint belonging to a PolicyProfile.
    Constraints are evaluated by the scheduling engine.
    """
    __tablename__ = "constraints"

    id = _uuid_pk()
    policy_profile_id = Column(UUID(as_uuid=True), ForeignKey("policy_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: ConstraintKind = Column(Enum(ConstraintKind), nullable=False)
    name: str = Column(String(128), nullable=False)
    description: str = Column(Text, nullable=True)
    # Constraint parameters as JSON for flexibility
    # e.g. {"type": "sleep_window", "min_hours": 7.5, "latest_start": "23:30"}
    parameters: dict = Column(JSON, nullable=False, default=dict)
    penalty_weight: float = Column(Float, nullable=False, default=1.0)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at = _now()
    updated_at = _updated()

    policy_profile = relationship("PolicyProfile", back_populates="constraints")


class RecoveryRule(Base):
    """
    Rules for how to handle missed tasks (reschedule, drop, carry-forward, etc.)
    """
    __tablename__ = "recovery_rules"

    id = _uuid_pk()
    policy_profile_id = Column(UUID(as_uuid=True), ForeignKey("policy_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    task_category: str = Column(String(64), nullable=True)  # null = applies to all
    # e.g. {"action": "reschedule_next_available", "max_attempts": 3, "drop_after_days": 2}
    rule_config: dict = Column(JSON, nullable=False, default=dict)
    priority: int = Column(Integer, nullable=False, default=0)
    created_at = _now()


# ---------------------------------------------------------------------------
# Goals & Tasks
# ---------------------------------------------------------------------------

class Goal(Base):
    """
    High-level life goal. Tasks and TaskTemplates are linked to Goals.
    Goals drive weekly quota constraints.
    """
    __tablename__ = "goals"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: str = Column(String(256), nullable=False)
    description: str = Column(Text, nullable=True)
    category: str = Column(String(64), nullable=True)  # e.g. "health", "career", "language"
    weekly_quota_minutes: Optional[int] = Column(Integer, nullable=True)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at = _now()
    updated_at = _updated()

    user = relationship("User", back_populates="goals")
    task_templates = relationship("TaskTemplate", back_populates="goal")


class TaskTemplate(Base):
    """
    Reusable task definition. Generates TaskInstances according to recurrence.
    This is what you configure once; the engine creates instances from it.
    """
    __tablename__ = "task_templates"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    goal_id = Column(UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL"), nullable=True, index=True)
    name: str = Column(String(256), nullable=False)
    description: str = Column(Text, nullable=True)
    category: str = Column(String(64), nullable=True)
    scheduling_class: SchedulingClass = Column(Enum(SchedulingClass), nullable=False)
    duration_minutes: int = Column(Integer, nullable=False)
    min_duration_minutes: int = Column(Integer, nullable=True)  # for splittable tasks
    priority: int = Column(Integer, nullable=False, default=50)  # 0-100
    # Recurrence
    is_recurring: bool = Column(Boolean, nullable=False, default=False)
    recurrence_frequency: Optional[RecurrenceFrequency] = Column(Enum(RecurrenceFrequency), nullable=True)
    recurrence_rrule: Optional[str] = Column(Text, nullable=True)  # RFC 5545 RRULE
    # Preferred scheduling windows (JSON array of {day_of_week, start_time, end_time})
    preferred_windows: list = Column(JSON, nullable=False, default=list)
    # Avoid windows (sleep, meals, etc.)
    avoid_windows: list = Column(JSON, nullable=False, default=list)
    # Whether to pin this task to exact time (hard_real_time)
    is_pinned: bool = Column(Boolean, nullable=False, default=False)
    pinned_start_time = Column(DateTime(timezone=True), nullable=True)
    # Deadline (for deadline_driven tasks)
    deadline_time = Column(DateTime(timezone=True), nullable=True)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at = _now()
    updated_at = _updated()

    user = relationship("User", back_populates="task_templates")
    goal = relationship("Goal", back_populates="task_templates")
    instances = relationship("TaskInstance", back_populates="template", cascade="all, delete-orphan")


class TaskInstance(Base):
    """
    A concrete occurrence of a task on a specific date.
    Generated by the nightly planning job from TaskTemplates.
    """
    __tablename__ = "task_instances"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("task_templates.id", ondelete="SET NULL"), nullable=True, index=True)
    name: str = Column(String(256), nullable=False)
    description: str = Column(Text, nullable=True)
    category: str = Column(String(64), nullable=True)
    scheduling_class: SchedulingClass = Column(Enum(SchedulingClass), nullable=False)
    status: TaskStatus = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.PENDING)
    priority: int = Column(Integer, nullable=False, default=50)
    duration_minutes: int = Column(Integer, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)
    created_at = _now()
    updated_at = _updated()

    user = relationship("User", back_populates="task_instances")
    template = relationship("TaskTemplate", back_populates="instances")
    time_blocks = relationship("TimeBlock", back_populates="task_instance", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_task_instances_user_status", "user_id", "status"),
        Index("ix_task_instances_user_due", "user_id", "due_date"),
    )


# ---------------------------------------------------------------------------
# Schedule Plan & Time Blocks
# ---------------------------------------------------------------------------

class SchedulePlan(Base):
    """
    A versioned snapshot of a full day's schedule for a user.
    The engine generates a PROPOSED plan; the user/system commits it.
    """
    __tablename__ = "schedule_plans"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_date = Column(DateTime(timezone=True), nullable=False)  # date this plan covers (midnight in user tz)
    is_committed: bool = Column(Boolean, nullable=False, default=False)
    committed_at = Column(DateTime(timezone=True), nullable=True)
    generation_reason: str = Column(String(256), nullable=True)
    score: float = Column(Float, nullable=True)  # objective function score
    score_breakdown: dict = Column(JSON, nullable=True)
    created_at = _now()
    updated_at = _updated()

    user = relationship("User", back_populates="schedule_plans")
    time_blocks = relationship("TimeBlock", back_populates="plan", cascade="all, delete-orphan")
    revisions = relationship("ScheduleRevision", back_populates="plan", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_schedule_plans_user_date", "user_id", "plan_date"),
    )


class TimeBlock(Base):
    """
    A concrete scheduled block: a task instance assigned to a time slot.
    The central schedulable unit. Every automatic change creates an AuditEvent.
    """
    __tablename__ = "time_blocks"

    id = _uuid_pk()
    plan_id = Column(UUID(as_uuid=True), ForeignKey("schedule_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_instance_id = Column(UUID(as_uuid=True), ForeignKey("task_instances.id", ondelete="SET NULL"), nullable=True, index=True)
    title: str = Column(String(256), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status: BlockStatus = Column(Enum(BlockStatus), nullable=False, default=BlockStatus.PROPOSED)
    # Whether this block is frozen (within freeze horizon or manually pinned)
    is_frozen: bool = Column(Boolean, nullable=False, default=False)
    is_protected: bool = Column(Boolean, nullable=False, default=False)  # never move/delete
    scheduling_class: SchedulingClass = Column(Enum(SchedulingClass), nullable=False)
    move_reason: str = Column(Text, nullable=True)  # why it was moved/created
    notes: str = Column(Text, nullable=True)
    created_at = _now()
    updated_at = _updated()

    plan = relationship("SchedulePlan", back_populates="time_blocks")
    task_instance = relationship("TaskInstance", back_populates="time_blocks")
    sync_mappings = relationship("SyncMapping", back_populates="time_block", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_time_blocks_user_start", "user_id", "start_time"),
        Index("ix_time_blocks_plan_start", "plan_id", "start_time"),
    )


class ScheduleRevision(Base):
    """
    Immutable record of every change to a SchedulePlan.
    Implements event sourcing / audit trail for plan changes.
    """
    __tablename__ = "schedule_revisions"

    id = _uuid_pk()
    plan_id = Column(UUID(as_uuid=True), ForeignKey("schedule_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_number: int = Column(Integer, nullable=False)
    author: str = Column(String(64), nullable=False, default="engine")  # "engine", "user", "ai_copilot"
    reason: str = Column(Text, nullable=True)
    diff: dict = Column(JSON, nullable=False, default=dict)  # before/after snapshot diff
    created_at = _now()

    plan = relationship("SchedulePlan", back_populates="revisions")

    __table_args__ = (
        UniqueConstraint("plan_id", "revision_number", name="uq_revision_number"),
    )


# ---------------------------------------------------------------------------
# Audit Events
# ---------------------------------------------------------------------------

class AuditEvent(Base):
    """
    Append-only event log. Every automatic or user-initiated scheduling action
    creates an AuditEvent with a human-readable explanation.
    """
    __tablename__ = "audit_events"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: AuditEventKind = Column(Enum(AuditEventKind), nullable=False)
    actor: str = Column(String(64), nullable=False, default="engine")
    # Reference to affected entities (nullable if not applicable)
    time_block_id = Column(UUID(as_uuid=True), ForeignKey("time_blocks.id", ondelete="SET NULL"), nullable=True, index=True)
    task_instance_id = Column(UUID(as_uuid=True), ForeignKey("task_instances.id", ondelete="SET NULL"), nullable=True, index=True)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("schedule_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    explanation: str = Column(Text, nullable=False)
    event_metadata: dict = Column("metadata", JSON, nullable=False, default=dict)
    created_at = _now()

    user = relationship("User", back_populates="audit_events")

    __table_args__ = (
        Index("ix_audit_events_user_created", "user_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class NotificationEvent(Base):
    """
    Tracks notification dispatch state. Enables idempotency and delivery tracking.
    """
    __tablename__ = "notification_events"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: NotificationKind = Column(Enum(NotificationKind), nullable=False)
    time_block_id = Column(UUID(as_uuid=True), ForeignKey("time_blocks.id", ondelete="SET NULL"), nullable=True, index=True)
    title: str = Column(String(256), nullable=False)
    body: str = Column(Text, nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    is_sent: bool = Column(Boolean, nullable=False, default=False)
    fcm_message_id: str = Column(String(256), nullable=True)
    # User action taken from the notification (snooze, done, skip)
    user_action: Optional[str] = Column(String(64), nullable=True)
    action_at = Column(DateTime(timezone=True), nullable=True)
    created_at = _now()

    user = relationship("User", back_populates="notification_events")

    __table_args__ = (
        Index("ix_notification_events_scheduled", "user_id", "scheduled_at", "is_sent"),
    )


# ---------------------------------------------------------------------------
# Domain Experts
# ---------------------------------------------------------------------------

class DomainExpert(Base):
    """
    A pluggable module that generates candidate tasks/policies for a domain.
    E.g. "LanguageLearningExpert", "FitnessExpert", "NutritionExpert".
    The central scheduler always makes final commit decisions.
    """
    __tablename__ = "domain_experts"

    id = _uuid_pk()
    name: str = Column(String(128), nullable=False, unique=True)
    description: str = Column(Text, nullable=True)
    module_path: str = Column(String(256), nullable=False)  # Python dotted module path
    config: dict = Column(JSON, nullable=False, default=dict)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at = _now()


# ---------------------------------------------------------------------------
# Context Signals (health / environment)
# ---------------------------------------------------------------------------

class ContextSignal(Base):
    """
    External signals from health sensors, location, etc.
    Treated as soft context unless explicitly configured as hard.
    Enables sleep-derived adaptation and activity/fatigue signals.
    """
    __tablename__ = "context_signals"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    signal_type: str = Column(String(64), nullable=False)
    # e.g. "sleep_end", "heart_rate", "steps", "hrv", "timezone_change"
    value: dict = Column(JSON, nullable=False, default=dict)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    source: str = Column(String(64), nullable=True)  # "health_connect", "manual", "gcal"
    created_at = _now()

    user = relationship("User", back_populates="context_signals")

    __table_args__ = (
        Index("ix_context_signals_user_type_recorded", "user_id", "signal_type", "recorded_at"),
    )


class EnergyProfile(Base):
    """
    Per-user energy/preference profile. Drives soft constraints on scheduling.
    E.g. morning person vs night owl, preferred workout time, cognitive peak.
    """
    __tablename__ = "energy_profiles"

    id = _uuid_pk()
    user_id: int = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    # JSON array of {hour_of_day: int, energy_level: float (0-1)}
    hourly_energy: list = Column(JSON, nullable=False, default=list)
    preferred_sleep_start: str = Column(String(5), nullable=True)  # "HH:MM"
    preferred_sleep_end: str = Column(String(5), nullable=True)    # "HH:MM"
    preferred_workout_window: str = Column(String(32), nullable=True)  # "morning"|"evening"|"any"
    work_start: str = Column(String(5), nullable=False, default="10:00")
    work_end: str = Column(String(5), nullable=False, default="19:00")
    created_at = _now()
    updated_at = _updated()

    user = relationship("User", back_populates="energy_profile")


# ---------------------------------------------------------------------------
# Google Calendar Sync Mapping
# ---------------------------------------------------------------------------

class SyncMapping(Base):
    """
    Maps internal TimeBlocks to Google Calendar event IDs.
    This is the projection layer — gcal is NOT source of truth.
    """
    __tablename__ = "sync_mappings"

    id = _uuid_pk()
    calendar_account_id = Column(UUID(as_uuid=True), ForeignKey("calendar_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    time_block_id = Column(UUID(as_uuid=True), ForeignKey("time_blocks.id", ondelete="CASCADE"), nullable=False, index=True)
    external_event_id: str = Column(String(256), nullable=False)  # gcal event id
    external_calendar_id: str = Column(String(256), nullable=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    sync_hash: str = Column(String(64), nullable=True)  # hash of last synced content for idempotency
    sync_error: str = Column(Text, nullable=True)
    created_at = _now()
    updated_at = _updated()

    calendar_account = relationship("CalendarAccount", back_populates="sync_mappings")
    time_block = relationship("TimeBlock", back_populates="sync_mappings")

    __table_args__ = (
        UniqueConstraint("calendar_account_id", "external_event_id", name="uq_sync_mapping_external"),
        UniqueConstraint("calendar_account_id", "time_block_id", name="uq_sync_mapping_block"),
    )
