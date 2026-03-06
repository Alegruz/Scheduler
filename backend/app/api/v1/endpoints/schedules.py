"""
Schedule generation, commit, and repair endpoints.

Flow:
  1. POST /schedules/generate  → creates a PROPOSED SchedulePlan with TimeBlocks
  2. GET  /schedules/{plan_id} → review the proposed plan
  3. POST /schedules/commit    → mark plan as committed (freeze_horizon then kicks in)
  4. POST /schedules/repair    → intraday repair for missed tasks
  5. GET  /schedules/today     → today's committed plan
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ....core.security import get_current_user_id
from ....db.models import (
    AuditEvent,
    AuditEventKind,
    BlockStatus,
    EnergyProfile,
    PolicyProfile,
    SchedulePlan,
    ScheduleRevision,
    SchedulingClass,
    TaskInstance,
    TaskStatus,
    TaskTemplate,
    TimeBlock,
)
from ....db.session import get_db
from ....engine.scheduler import (
    EnginePolicy,
    PlacedBlock,
    SchedulingEngine,
    SchedulingTask,
    SleepWindowConstraint,
    WorkWindowConstraint,
)
from ....schemas.schemas import (
    CommitScheduleRequest,
    GenerateScheduleRequest,
    RepairScheduleRequest,
    SchedulePlanResponse,
    TimeBlockResponse,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])
_engine = SchedulingEngine()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_policy(user_id: int, profile: Optional[PolicyProfile], ep: Optional[EnergyProfile], user_tz: str) -> EnginePolicy:
    """Assemble EnginePolicy from policy profile and energy profile."""
    base = EnginePolicy(user_timezone=user_tz)
    if ep:
        base.sleep = SleepWindowConstraint(
            earliest_wake=ep.preferred_sleep_end or "06:00",
            latest_sleep_start=ep.preferred_sleep_start or "00:30",
        )
        base.work = WorkWindowConstraint(
            work_start=ep.work_start,
            work_end=ep.work_end,
        )
    if profile and profile.policy_config:
        cfg = profile.policy_config
        if "freeze_horizon_minutes" in cfg:
            base.freeze_horizon_minutes = cfg["freeze_horizon_minutes"]
        if "prefer_earlier" in cfg:
            base.prefer_earlier = cfg["prefer_earlier"]
        if "min_buffer_minutes" in cfg:
            base.min_buffer_minutes = cfg["min_buffer_minutes"]
        if "sleep" in cfg:
            s = cfg["sleep"]
            base.sleep = SleepWindowConstraint(
                min_hours=s.get("min_hours", 7.5),
                latest_sleep_start=s.get("latest_sleep_start", "00:30"),
                earliest_wake=s.get("earliest_wake", "06:00"),
            )
    return base


def _template_to_task(tmpl: TaskTemplate, target_date: date, user_tz: str) -> SchedulingTask:
    tz = pytz.timezone(user_tz)

    preferred_windows = []
    for pw in (tmpl.preferred_windows or []):
        h0, m0 = map(int, pw["start_time"].split(":"))
        h1, m1 = map(int, pw["end_time"].split(":"))
        from ....engine.scheduler import TimeWindow
        preferred_windows.append(TimeWindow(
            start=tz.localize(datetime(target_date.year, target_date.month, target_date.day, h0, m0)),
            end=tz.localize(datetime(target_date.year, target_date.month, target_date.day, h1, m1)),
        ))

    avoid_windows = []
    for aw in (tmpl.avoid_windows or []):
        h0, m0 = map(int, aw["start_time"].split(":"))
        h1, m1 = map(int, aw["end_time"].split(":"))
        from ....engine.scheduler import TimeWindow
        avoid_windows.append(TimeWindow(
            start=tz.localize(datetime(target_date.year, target_date.month, target_date.day, h0, m0)),
            end=tz.localize(datetime(target_date.year, target_date.month, target_date.day, h1, m1)),
        ))

    pinned_start = None
    if tmpl.is_pinned and tmpl.pinned_start_time:
        pinned_start = tmpl.pinned_start_time

    return SchedulingTask(
        id=str(tmpl.id),  # will be replaced with instance id after creation
        name=tmpl.name,
        scheduling_class=tmpl.scheduling_class.value if hasattr(tmpl.scheduling_class, "value") else str(tmpl.scheduling_class),
        duration_minutes=tmpl.duration_minutes,
        priority=tmpl.priority,
        preferred_windows=preferred_windows,
        avoid_windows=avoid_windows,
        deadline=tmpl.deadline_time,
        pinned_start=pinned_start,
        is_protected=False,
        category=tmpl.category or "",
        min_duration_minutes=tmpl.min_duration_minutes,
    )


def _record_audit(
    db: Session,
    user_id: int,
    kind: AuditEventKind,
    explanation: str,
    actor: str = "engine",
    plan_id=None,
    block_id=None,
    task_id=None,
    metadata: dict = None,
):
    event = AuditEvent(
        user_id=user_id,
        kind=kind,
        actor=actor,
        plan_id=plan_id,
        time_block_id=block_id,
        task_instance_id=task_id,
        explanation=explanation,
        event_metadata=metadata or {},
    )
    db.add(event)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=SchedulePlanResponse, status_code=status.HTTP_201_CREATED)
def generate_schedule(
    body: GenerateScheduleRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """
    Generate a proposed schedule plan for the target date.
    Instantiates recurring templates, runs the deterministic scheduler,
    creates TimeBlocks, and returns the plan for user review.
    """
    try:
        target_date = date.fromisoformat(body.target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD")

    from ....db.models import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if a committed plan already exists for this date
    existing_committed = (
        db.query(SchedulePlan)
        .filter(
            SchedulePlan.user_id == user_id,
            SchedulePlan.plan_date >= datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc),
            SchedulePlan.plan_date < datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc) + timedelta(days=1),
            SchedulePlan.is_committed == True,
        )
        .first()
    )
    if existing_committed and not body.force_regenerate:
        raise HTTPException(
            status_code=409,
            detail="A committed plan already exists for this date. Use force_regenerate=true to overwrite.",
        )

    # Load policy
    profile = None
    if body.policy_profile_id:
        profile = db.query(PolicyProfile).filter(
            PolicyProfile.id == body.policy_profile_id,
            PolicyProfile.user_id == user_id,
        ).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Policy profile not found")
    else:
        profile = db.query(PolicyProfile).filter(
            PolicyProfile.user_id == user_id,
            PolicyProfile.is_default == True,
        ).first()

    ep = db.query(EnergyProfile).filter(EnergyProfile.user_id == user_id).first()
    policy = _build_policy(user_id, profile, ep, user.timezone)

    # Load active task templates → create TaskInstances for this date
    templates = (
        db.query(TaskTemplate)
        .filter(TaskTemplate.user_id == user_id, TaskTemplate.is_active == True)
        .all()
    )

    tasks: list[SchedulingTask] = []
    instance_map: dict[str, TaskInstance] = {}  # temp_id → TaskInstance

    for tmpl in templates:
        # For recurring tasks: always generate an instance
        # For non-recurring: only if no instance exists for this date
        sched_task = _template_to_task(tmpl, target_date, user.timezone)

        # Create TaskInstance
        instance = TaskInstance(
            user_id=user_id,
            template_id=tmpl.id,
            name=tmpl.name,
            description=tmpl.description,
            category=tmpl.category,
            scheduling_class=tmpl.scheduling_class,
            status=TaskStatus.PENDING,
            priority=tmpl.priority,
            duration_minutes=tmpl.duration_minutes,
            due_date=tmpl.deadline_time,
        )
        db.add(instance)
        db.flush()  # get the generated id

        sched_task.id = str(instance.id)
        tasks.append(sched_task)
        instance_map[str(instance.id)] = instance

    # Load existing frozen blocks for this date (from prior committed plan)
    existing_blocks: list[PlacedBlock] = []
    if existing_committed:
        tz = pytz.timezone(user.timezone)
        now = datetime.now(timezone.utc)
        freeze_cutoff = now + timedelta(minutes=policy.freeze_horizon_minutes)
        for tb in existing_committed.time_blocks:
            if tb.start_time <= freeze_cutoff or tb.is_frozen:
                existing_blocks.append(PlacedBlock(
                    task_id=str(tb.task_instance_id) if tb.task_instance_id else "",
                    task_name=tb.title,
                    scheduling_class=tb.scheduling_class.value if hasattr(tb.scheduling_class, "value") else str(tb.scheduling_class),
                    start=tb.start_time,
                    end=tb.end_time,
                    is_frozen=True,
                    is_protected=tb.is_protected,
                    reason="Frozen from prior committed plan",
                ))

    # Run deterministic scheduler
    result = _engine.plan_day(
        target_date=target_date,
        user_id=user_id,
        tasks=tasks,
        policy=policy,
        existing_blocks=existing_blocks,
        generation_reason="user_requested" if body.force_regenerate else "nightly_planning",
    )

    # Persist SchedulePlan
    tz = pytz.timezone(user.timezone)
    plan_date = tz.localize(datetime(target_date.year, target_date.month, target_date.day))

    plan = SchedulePlan(
        user_id=user_id,
        plan_date=plan_date,
        is_committed=False,
        generation_reason=result.generation_reason,
        score=result.score,
        score_breakdown=result.score_breakdown,
    )
    db.add(plan)
    db.flush()

    # Persist TimeBlocks
    for block in result.blocks:
        if block.is_frozen:
            continue  # frozen blocks already exist in prior plan
        scheduling_class_val = _str_to_scheduling_class(block.scheduling_class)
        time_block = TimeBlock(
            plan_id=plan.id,
            user_id=user_id,
            task_instance_id=uuid.UUID(block.task_id) if block.task_id and block.task_id != "" else None,
            title=block.task_name,
            start_time=block.start,
            end_time=block.end,
            status=BlockStatus.PROPOSED,
            is_frozen=block.is_frozen,
            is_protected=block.is_protected,
            scheduling_class=scheduling_class_val,
            move_reason=block.reason,
        )
        db.add(time_block)

    # Record audit event
    _record_audit(
        db, user_id, AuditEventKind.PLAN_GENERATED,
        explanation=f"Generated schedule for {target_date}. Score={result.score:.2f}. Feasible={result.is_feasible}. "
                    f"Violations: {len(result.violations)}",
        plan_id=plan.id,
        metadata={"score_breakdown": result.score_breakdown, "is_feasible": result.is_feasible},
    )

    db.commit()
    db.refresh(plan)
    return plan


@router.post("/commit", response_model=SchedulePlanResponse)
def commit_schedule(
    body: CommitScheduleRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Commit a proposed plan. Transitions all PROPOSED blocks to COMMITTED."""
    plan = db.query(SchedulePlan).filter(
        SchedulePlan.id == body.plan_id,
        SchedulePlan.user_id == user_id,
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Schedule plan not found")
    if plan.is_committed:
        raise HTTPException(status_code=409, detail="Plan already committed")

    plan.is_committed = True
    plan.committed_at = datetime.now(timezone.utc)

    for tb in plan.time_blocks:
        if tb.status == BlockStatus.PROPOSED:
            tb.status = BlockStatus.COMMITTED

    # Add revision record
    rev_count = db.query(ScheduleRevision).filter(ScheduleRevision.plan_id == plan.id).count()
    revision = ScheduleRevision(
        plan_id=plan.id,
        revision_number=rev_count + 1,
        author="user",
        reason="User committed plan",
        diff={"action": "committed"},
    )
    db.add(revision)

    _record_audit(
        db, user_id, AuditEventKind.PLAN_COMMITTED,
        explanation=f"Plan {plan.id} committed by user",
        plan_id=plan.id,
    )

    db.commit()
    db.refresh(plan)
    return plan


@router.post("/repair", response_model=SchedulePlanResponse, status_code=status.HTTP_201_CREATED)
def repair_schedule(
    body: RepairScheduleRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """
    Intraday repair: reschedule missed tasks in the remaining day.
    Creates a new PROPOSED repair plan.
    """
    try:
        target_date = date.fromisoformat(body.target_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD")

    from ....db.models import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    ep = db.query(EnergyProfile).filter(EnergyProfile.user_id == user_id).first()
    profile = db.query(PolicyProfile).filter(
        PolicyProfile.user_id == user_id, PolicyProfile.is_default == True
    ).first()
    policy = _build_policy(user_id, profile, ep, user.timezone)

    # Load current committed plan
    tz_obj = pytz.timezone(user.timezone)
    day_start = tz_obj.localize(datetime(target_date.year, target_date.month, target_date.day))
    current_plan = (
        db.query(SchedulePlan)
        .filter(
            SchedulePlan.user_id == user_id,
            SchedulePlan.plan_date >= day_start,
            SchedulePlan.plan_date < day_start + timedelta(days=1),
            SchedulePlan.is_committed == True,
        )
        .order_by(SchedulePlan.created_at.desc())
        .first()
    )

    current_blocks: list[PlacedBlock] = []
    if current_plan:
        for tb in current_plan.time_blocks:
            current_blocks.append(PlacedBlock(
                task_id=str(tb.task_instance_id) if tb.task_instance_id else "",
                task_name=tb.title,
                scheduling_class=tb.scheduling_class.value if hasattr(tb.scheduling_class, "value") else str(tb.scheduling_class),
                start=tb.start_time,
                end=tb.end_time,
                is_frozen=tb.is_frozen,
                is_protected=tb.is_protected,
            ))

    # Load missed tasks
    missed_tasks: list[SchedulingTask] = []
    for tid in body.missed_task_ids:
        instance = db.query(TaskInstance).filter(
            TaskInstance.id == tid, TaskInstance.user_id == user_id
        ).first()
        if instance:
            missed_tasks.append(SchedulingTask(
                id=str(instance.id),
                name=instance.name,
                scheduling_class="recovery",
                duration_minutes=instance.duration_minutes,
                priority=instance.priority,
                category=instance.category or "",
            ))

    now = datetime.now(timezone.utc)
    result = _engine.repair_day(
        target_date=target_date,
        user_id=user_id,
        missed_tasks=missed_tasks,
        current_blocks=current_blocks,
        policy=policy,
        now=now,
        generation_reason="intraday_repair",
    )

    # Persist repair plan
    plan = SchedulePlan(
        user_id=user_id,
        plan_date=day_start,
        is_committed=False,
        generation_reason="intraday_repair",
        score=result.score,
        score_breakdown=result.score_breakdown,
    )
    db.add(plan)
    db.flush()

    for block in result.blocks:
        if block.is_frozen:
            continue
        scheduling_class_val = _str_to_scheduling_class(block.scheduling_class)
        time_block = TimeBlock(
            plan_id=plan.id,
            user_id=user_id,
            task_instance_id=uuid.UUID(block.task_id) if block.task_id else None,
            title=block.task_name,
            start_time=block.start,
            end_time=block.end,
            status=BlockStatus.PROPOSED,
            is_frozen=block.is_frozen,
            is_protected=block.is_protected,
            scheduling_class=scheduling_class_val,
            move_reason=block.reason,
        )
        db.add(time_block)

    _record_audit(
        db, user_id, AuditEventKind.REPAIR_TRIGGERED,
        explanation=f"Intraday repair for {target_date}. Missed tasks: {len(missed_tasks)}. Score={result.score:.2f}",
        plan_id=plan.id,
        metadata={"missed_count": len(missed_tasks), "is_feasible": result.is_feasible},
    )

    db.commit()
    db.refresh(plan)
    return plan


@router.get("/today", response_model=Optional[SchedulePlanResponse])
def get_today_schedule(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    from ....db.models import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    tz_obj = pytz.timezone(user.timezone)
    today = datetime.now(tz_obj).date()
    day_start = tz_obj.localize(datetime(today.year, today.month, today.day))

    plan = (
        db.query(SchedulePlan)
        .filter(
            SchedulePlan.user_id == user_id,
            SchedulePlan.plan_date >= day_start,
            SchedulePlan.plan_date < day_start + timedelta(days=1),
            SchedulePlan.is_committed == True,
        )
        .order_by(SchedulePlan.created_at.desc())
        .first()
    )
    return plan


@router.get("/{plan_id}", response_model=SchedulePlanResponse)
def get_plan(
    plan_id: uuid.UUID,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    plan = db.query(SchedulePlan).filter(
        SchedulePlan.id == plan_id,
        SchedulePlan.user_id == user_id,
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _str_to_scheduling_class(s: str) -> SchedulingClass:
    mapping = {c.value: c for c in SchedulingClass}
    return mapping.get(s, SchedulingClass.OPPORTUNISTIC)
