"""
Pydantic v2 schemas for API request/response payloads.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="UTC", max_length=64)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str
    timezone: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------

class GoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: Optional[str] = None
    category: Optional[str] = None
    weekly_quota_minutes: Optional[int] = Field(default=None, ge=0)


class GoalUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    description: Optional[str] = None
    category: Optional[str] = None
    weekly_quota_minutes: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class GoalResponse(BaseModel):
    id: uuid.UUID
    user_id: int
    name: str
    description: Optional[str]
    category: Optional[str]
    weekly_quota_minutes: Optional[int]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Policy Profile
# ---------------------------------------------------------------------------

class PolicyProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None
    is_default: bool = False
    policy_config: dict[str, Any] = Field(default_factory=dict)


class PolicyProfileUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None
    policy_config: Optional[dict[str, Any]] = None


class PolicyProfileResponse(BaseModel):
    id: uuid.UUID
    user_id: int
    name: str
    description: Optional[str]
    is_default: bool
    policy_config: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Task Template
# ---------------------------------------------------------------------------

class TaskTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: Optional[str] = None
    category: Optional[str] = None
    goal_id: Optional[uuid.UUID] = None
    scheduling_class: str
    duration_minutes: int = Field(ge=1, le=1440)
    min_duration_minutes: Optional[int] = Field(default=None, ge=1)
    priority: int = Field(default=50, ge=0, le=100)
    is_recurring: bool = False
    recurrence_frequency: Optional[str] = None
    recurrence_rrule: Optional[str] = None
    preferred_windows: list[dict[str, Any]] = Field(default_factory=list)
    avoid_windows: list[dict[str, Any]] = Field(default_factory=list)
    is_pinned: bool = False
    pinned_start_time: Optional[datetime] = None
    deadline_time: Optional[datetime] = None


class TaskTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    description: Optional[str] = None
    category: Optional[str] = None
    goal_id: Optional[uuid.UUID] = None
    scheduling_class: Optional[str] = None
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    priority: Optional[int] = Field(default=None, ge=0, le=100)
    is_recurring: Optional[bool] = None
    recurrence_frequency: Optional[str] = None
    recurrence_rrule: Optional[str] = None
    preferred_windows: Optional[list[dict[str, Any]]] = None
    avoid_windows: Optional[list[dict[str, Any]]] = None
    is_pinned: Optional[bool] = None
    pinned_start_time: Optional[datetime] = None
    deadline_time: Optional[datetime] = None
    is_active: Optional[bool] = None


class TaskTemplateResponse(BaseModel):
    id: uuid.UUID
    user_id: int
    name: str
    description: Optional[str]
    category: Optional[str]
    goal_id: Optional[uuid.UUID]
    scheduling_class: str
    duration_minutes: int
    min_duration_minutes: Optional[int]
    priority: int
    is_recurring: bool
    recurrence_frequency: Optional[str]
    recurrence_rrule: Optional[str]
    preferred_windows: list[dict[str, Any]]
    avoid_windows: list[dict[str, Any]]
    is_pinned: bool
    pinned_start_time: Optional[datetime]
    deadline_time: Optional[datetime]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Task Instance
# ---------------------------------------------------------------------------

class TaskInstanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: Optional[str] = None
    category: Optional[str] = None
    template_id: Optional[uuid.UUID] = None
    scheduling_class: str
    duration_minutes: int = Field(ge=1, le=1440)
    priority: int = Field(default=50, ge=0, le=100)
    due_date: Optional[datetime] = None


class TaskInstanceUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


class TaskInstanceResponse(BaseModel):
    id: uuid.UUID
    user_id: int
    template_id: Optional[uuid.UUID]
    name: str
    description: Optional[str]
    category: Optional[str]
    scheduling_class: str
    status: str
    priority: int
    duration_minutes: int
    due_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Schedule Plan & Time Blocks
# ---------------------------------------------------------------------------

class GenerateScheduleRequest(BaseModel):
    target_date: str = Field(description="ISO date YYYY-MM-DD")
    policy_profile_id: Optional[uuid.UUID] = None
    force_regenerate: bool = False


class CommitScheduleRequest(BaseModel):
    plan_id: uuid.UUID


class TimeBlockResponse(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    user_id: int
    task_instance_id: Optional[uuid.UUID]
    title: str
    start_time: datetime
    end_time: datetime
    status: str
    is_frozen: bool
    is_protected: bool
    scheduling_class: str
    move_reason: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SchedulePlanResponse(BaseModel):
    id: uuid.UUID
    user_id: int
    plan_date: datetime
    is_committed: bool
    committed_at: Optional[datetime]
    generation_reason: Optional[str]
    score: Optional[float]
    score_breakdown: Optional[dict[str, Any]]
    blocks: list[TimeBlockResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RepairScheduleRequest(BaseModel):
    target_date: str = Field(description="ISO date YYYY-MM-DD")
    missed_task_ids: list[uuid.UUID] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit Events
# ---------------------------------------------------------------------------

class AuditEventResponse(BaseModel):
    id: uuid.UUID
    user_id: int
    kind: str
    actor: str
    time_block_id: Optional[uuid.UUID]
    task_instance_id: Optional[uuid.UUID]
    plan_id: Optional[uuid.UUID]
    explanation: str
    event_metadata: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditQueryParams(BaseModel):
    kind: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Context Signal (health)
# ---------------------------------------------------------------------------

class ContextSignalIngest(BaseModel):
    signal_type: str
    value: dict[str, Any]
    recorded_at: datetime
    source: Optional[str] = None


class ContextSignalResponse(BaseModel):
    id: uuid.UUID
    user_id: int
    signal_type: str
    value: dict[str, Any]
    recorded_at: datetime
    source: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Notification action (from mobile / watch)
# ---------------------------------------------------------------------------

class NotificationActionRequest(BaseModel):
    notification_id: uuid.UUID
    action: str  # "done", "skip", "snooze"
    snooze_minutes: Optional[int] = Field(default=None, ge=1, le=120)


# ---------------------------------------------------------------------------
# Google Calendar sync
# ---------------------------------------------------------------------------

class GCalSyncRequest(BaseModel):
    calendar_account_id: uuid.UUID
    force_full_sync: bool = False


class GCalSyncResponse(BaseModel):
    synced_count: int
    failed_count: int
    errors: list[str]
