"""
Background job workers.

Jobs:
  1. nightly_planning_job   – run at ~22:00 user local time, generate tomorrow's plan
  2. intraday_repair_job    – run every 15min, detect missed tasks, trigger repair
  3. gcal_sync_job          – run every 10min, push committed blocks to GCal
  4. notification_dispatch_job – run every minute, send due notifications via FCM
  5. missed_task_reconcile_job – run at midnight, mark missed tasks
  6. stale_sync_repair_job  – run hourly, retry failed GCal syncs
"""

import logging
from datetime import date, datetime, timedelta, timezone

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from ..db.models import (
    AuditEventKind,
    BlockStatus,
    NotificationEvent,
    NotificationKind,
    SchedulePlan,
    TaskInstance,
    TaskStatus,
    TimeBlock,
    User,
)
from ..db.session import SessionLocal
from ..engine.scheduler import EnginePolicy, SchedulingEngine, SchedulingTask, SleepWindowConstraint, WorkWindowConstraint
from ..api.v1.endpoints.schedules import _build_policy, _record_audit, _str_to_scheduling_class

logger = logging.getLogger(__name__)
_engine = SchedulingEngine()


# ---------------------------------------------------------------------------
# Job: Nightly Planning
# ---------------------------------------------------------------------------

def nightly_planning_job():
    """
    Generate proposed schedule plans for all active users for tomorrow.
    Runs at 22:00 local time (approximated with UTC cron).
    """
    logger.info("nightly_planning_job: starting")
    db: Session = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
        for user in users:
            try:
                _plan_for_user(db, user, days_ahead=1)
            except Exception as e:  # noqa: BLE001
                logger.error("nightly_planning_job: failed for user %d: %s", user.id, e)
    finally:
        db.close()
    logger.info("nightly_planning_job: done")


def _plan_for_user(db: Session, user: User, days_ahead: int = 1):
    from ..db.models import EnergyProfile, PolicyProfile, TaskTemplate, SchedulePlan as SP
    tz_obj = pytz.timezone(user.timezone)
    today = datetime.now(tz_obj).date()
    target_date = today + timedelta(days=days_ahead)

    # Skip if committed plan already exists
    day_start = tz_obj.localize(datetime(target_date.year, target_date.month, target_date.day))
    existing = db.query(SP).filter(
        SP.user_id == user.id,
        SP.plan_date >= day_start,
        SP.plan_date < day_start + timedelta(days=1),
        SP.is_committed == True,
    ).first()
    if existing:
        logger.info("nightly_planning_job: committed plan already exists for user %d on %s", user.id, target_date)
        return

    ep = db.query(EnergyProfile).filter(EnergyProfile.user_id == user.id).first()
    profile = db.query(PolicyProfile).filter(
        PolicyProfile.user_id == user.id, PolicyProfile.is_default == True
    ).first()
    policy = _build_policy(user.id, profile, ep, user.timezone)

    templates = db.query(TaskTemplate).filter(
        TaskTemplate.user_id == user.id, TaskTemplate.is_active == True
    ).all()

    tasks = []
    for tmpl in templates:
        from ..api.v1.endpoints.schedules import _template_to_task
        st = _template_to_task(tmpl, target_date, user.timezone)
        instance = TaskInstance(
            user_id=user.id,
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
        db.flush()
        st.id = str(instance.id)
        tasks.append(st)

    result = _engine.plan_day(
        target_date=target_date,
        user_id=user.id,
        tasks=tasks,
        policy=policy,
        generation_reason="nightly_planning",
    )

    plan = SP(
        user_id=user.id,
        plan_date=day_start,
        is_committed=False,
        generation_reason="nightly_planning",
        score=result.score,
        score_breakdown=result.score_breakdown,
    )
    db.add(plan)
    db.flush()

    from ..db.models import TimeBlock as TB
    for block in result.blocks:
        sc = _str_to_scheduling_class(block.scheduling_class)
        tb = TB(
            plan_id=plan.id,
            user_id=user.id,
            task_instance_id=None,
            title=block.task_name,
            start_time=block.start,
            end_time=block.end,
            status=BlockStatus.PROPOSED,
            is_frozen=block.is_frozen,
            is_protected=block.is_protected,
            scheduling_class=sc,
            move_reason=block.reason,
        )
        db.add(tb)

    _record_audit(
        db, user.id, AuditEventKind.PLAN_GENERATED,
        explanation=f"Nightly plan for {target_date}: score={result.score:.2f} feasible={result.is_feasible}",
        plan_id=plan.id,
        event_metadata={"is_feasible": result.is_feasible, "score": result.score},
    )
    db.commit()
    logger.info("nightly_planning_job: created plan %s for user %d on %s", plan.id, user.id, target_date)


# ---------------------------------------------------------------------------
# Job: Intraday Repair
# ---------------------------------------------------------------------------

def intraday_repair_job():
    """
    Every 15 minutes: detect missed task blocks and trigger repair planning.
    A block is considered missed if:
      - it was COMMITTED, start_time is past, and status is still COMMITTED
    """
    logger.info("intraday_repair_job: starting")
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        # Find committed time blocks that started > 15min ago but are still COMMITTED
        cutoff = now - timedelta(minutes=15)
        missed_blocks = (
            db.query(TimeBlock)
            .filter(
                TimeBlock.status == BlockStatus.COMMITTED,
                TimeBlock.end_time < now,
                TimeBlock.start_time < cutoff,
            )
            .all()
        )

        if not missed_blocks:
            return

        # Group by user
        by_user: dict[int, list[TimeBlock]] = {}
        for tb in missed_blocks:
            by_user.setdefault(tb.user_id, []).append(tb)

        for uid, tbs in by_user.items():
            try:
                _repair_for_user(db, uid, tbs, now)
            except Exception as e:  # noqa: BLE001
                logger.error("intraday_repair_job: failed for user %d: %s", uid, e)
    finally:
        db.close()


def _repair_for_user(db: Session, user_id: int, missed_blocks: list, now: datetime):
    from ..db.models import User, EnergyProfile, PolicyProfile
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return

    ep = db.query(EnergyProfile).filter(EnergyProfile.user_id == user_id).first()
    profile = db.query(PolicyProfile).filter(
        PolicyProfile.user_id == user_id, PolicyProfile.is_default == True
    ).first()
    policy = _build_policy(user_id, profile, ep, user.timezone)

    tz_obj = pytz.timezone(user.timezone)
    today = datetime.now(tz_obj).date()
    day_start = tz_obj.localize(datetime(today.year, today.month, today.day))

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

    from ..engine.scheduler import PlacedBlock
    current_blocks: list[PlacedBlock] = []
    if current_plan:
        for tb in current_plan.time_blocks:
            if tb.status not in (BlockStatus.DONE, BlockStatus.SKIPPED):
                current_blocks.append(PlacedBlock(
                    task_id=str(tb.task_instance_id) if tb.task_instance_id else "",
                    task_name=tb.title,
                    scheduling_class=tb.scheduling_class.value if hasattr(tb.scheduling_class, "value") else str(tb.scheduling_class),
                    start=tb.start_time,
                    end=tb.end_time,
                    is_frozen=tb.is_frozen,
                    is_protected=tb.is_protected,
                ))

    missed_tasks = []
    for tb in missed_blocks:
        tb.status = BlockStatus.CANCELLED  # mark original as cancelled
        if tb.task_instance_id:
            instance = db.query(TaskInstance).filter(TaskInstance.id == tb.task_instance_id).first()
            if instance:
                instance.status = TaskStatus.MISSED
                missed_tasks.append(SchedulingTask(
                    id=str(instance.id),
                    name=instance.name,
                    scheduling_class="recovery",
                    duration_minutes=instance.duration_minutes,
                    priority=instance.priority,
                    category=instance.category or "",
                ))

    if not missed_tasks:
        db.commit()
        return

    result = _engine.repair_day(
        target_date=today,
        user_id=user_id,
        missed_tasks=missed_tasks,
        current_blocks=current_blocks,
        policy=policy,
        now=now,
    )

    from ..db.models import SchedulePlan as SP, TimeBlock as TB
    plan = SP(
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
        sc = _str_to_scheduling_class(block.scheduling_class)
        new_tb = TB(
            plan_id=plan.id,
            user_id=user_id,
            title=block.task_name,
            start_time=block.start,
            end_time=block.end,
            status=BlockStatus.PROPOSED,
            is_frozen=False,
            is_protected=block.is_protected,
            scheduling_class=sc,
            move_reason=block.reason,
        )
        db.add(new_tb)

    _record_audit(
        db, user_id, AuditEventKind.REPAIR_TRIGGERED,
        explanation=f"Intraday repair: {len(missed_tasks)} missed tasks rescheduled",
        plan_id=plan.id,
        event_metadata={"missed_count": len(missed_tasks)},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Job: Notification Dispatch
# ---------------------------------------------------------------------------

def notification_dispatch_job():
    """
    Every minute: send due notifications that haven't been sent yet.
    In production: calls FCM API. In MVP: just marks as sent.
    """
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(NotificationEvent)
            .filter(
                NotificationEvent.is_sent == False,
                NotificationEvent.scheduled_at <= now,
            )
            .limit(100)
            .all()
        )
        for notif in due:
            try:
                # In production: send FCM push notification
                # fcm_response = send_fcm(notif.user_id, notif.title, notif.body)
                notif.is_sent = True
                notif.sent_at = now
                logger.debug("Dispatched notification %s to user %d", notif.id, notif.user_id)
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to send notification %s: %s", notif.id, e)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Job: GCal Sync
# ---------------------------------------------------------------------------

def gcal_sync_job():
    """
    Every 10 minutes: push all unsynced committed blocks to GCal for all
    users who have an active GCal account.
    """
    from ..db.models import CalendarAccount, SyncMapping
    db: Session = SessionLocal()
    try:
        accounts = db.query(CalendarAccount).filter(
            CalendarAccount.is_active == True,
        ).all()
        for account in accounts:
            try:
                _sync_account(db, account)
            except Exception as e:  # noqa: BLE001
                logger.error("gcal_sync_job: failed for account %s: %s", account.id, e)
    finally:
        db.close()


def _sync_account(db: Session, account):
    import hashlib, json
    from ..db.models import SyncMapping, SchedulePlan

    blocks = (
        db.query(TimeBlock)
        .join(SchedulePlan)
        .filter(
            TimeBlock.user_id == account.user_id,
            SchedulePlan.is_committed == True,
            TimeBlock.status.in_([BlockStatus.COMMITTED, BlockStatus.IN_PROGRESS, BlockStatus.DONE]),
        )
        .all()
    )

    for tb in blocks:
        data = {
            "title": tb.title,
            "start": tb.start_time.isoformat(),
            "end": tb.end_time.isoformat(),
            "status": tb.status.value if hasattr(tb.status, "value") else str(tb.status),
        }
        current_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        mapping = db.query(SyncMapping).filter(
            SyncMapping.calendar_account_id == account.id,
            SyncMapping.time_block_id == tb.id,
        ).first()

        if mapping and mapping.sync_hash == current_hash:
            continue

        try:
            # Production: call GCal API
            event_id = f"lifeos_{tb.id}"
            if mapping:
                mapping.external_event_id = event_id
                mapping.last_synced_at = datetime.now(timezone.utc)
                mapping.sync_hash = current_hash
                mapping.sync_error = None
            else:
                mapping = SyncMapping(
                    calendar_account_id=account.id,
                    time_block_id=tb.id,
                    external_event_id=event_id,
                    external_calendar_id=account.target_calendar_id or "primary",
                    last_synced_at=datetime.now(timezone.utc),
                    sync_hash=current_hash,
                )
                db.add(mapping)
        except Exception as e:  # noqa: BLE001
            if mapping:
                mapping.sync_error = str(e)
            logger.error("gcal_sync_job: block %s sync failed: %s", tb.id, e)

    account.last_synced_at = datetime.now(timezone.utc)
    db.commit()


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()

    # Nightly planning at 22:00 UTC (approximate; in production use per-user timezone)
    scheduler.add_job(nightly_planning_job, CronTrigger(hour=22, minute=0), id="nightly_planning", replace_existing=True)

    # Intraday repair every 15 minutes
    scheduler.add_job(intraday_repair_job, IntervalTrigger(minutes=15), id="intraday_repair", replace_existing=True)

    # GCal sync every 10 minutes
    scheduler.add_job(gcal_sync_job, IntervalTrigger(minutes=10), id="gcal_sync", replace_existing=True)

    # Notification dispatch every minute
    scheduler.add_job(notification_dispatch_job, IntervalTrigger(minutes=1), id="notification_dispatch", replace_existing=True)

    return scheduler
