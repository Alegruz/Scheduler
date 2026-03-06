"""
Notification action endpoint.
Mobile / Wear OS sends user actions (done, skip, snooze) back to the server.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ....core.security import get_current_user_id
from ....db.models import AuditEvent, AuditEventKind, BlockStatus, NotificationEvent, TimeBlock, TaskInstance, TaskStatus
from ....db.session import get_db
from ....schemas.schemas import NotificationActionRequest

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/action")
def handle_notification_action(
    body: NotificationActionRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """
    Handle user action on a notification (done / skip / snooze).
    Updates time block status and records audit event.
    """
    notification = db.query(NotificationEvent).filter(
        NotificationEvent.id == body.notification_id,
        NotificationEvent.user_id == user_id,
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.user_action = body.action
    notification.action_at = datetime.now(timezone.utc)

    time_block = None
    if notification.time_block_id:
        time_block = db.query(TimeBlock).filter(TimeBlock.id == notification.time_block_id).first()

    if body.action == "done":
        if time_block:
            time_block.status = BlockStatus.DONE
        if time_block and time_block.task_instance_id:
            instance = db.query(TaskInstance).filter(TaskInstance.id == time_block.task_instance_id).first()
            if instance:
                instance.status = TaskStatus.DONE
        explanation = f"User marked block '{time_block.title if time_block else '?'}' as done via notification"
        audit_kind = AuditEventKind.BLOCK_DONE

    elif body.action == "skip":
        if time_block:
            time_block.status = BlockStatus.SKIPPED
        if time_block and time_block.task_instance_id:
            instance = db.query(TaskInstance).filter(TaskInstance.id == time_block.task_instance_id).first()
            if instance:
                instance.status = TaskStatus.SKIPPED
        explanation = f"User skipped block '{time_block.title if time_block else '?'}' via notification"
        audit_kind = AuditEventKind.BLOCK_SKIPPED

    elif body.action == "snooze":
        snooze_min = body.snooze_minutes or 15
        if time_block:
            time_block.start_time = time_block.start_time + timedelta(minutes=snooze_min)
            time_block.end_time = time_block.end_time + timedelta(minutes=snooze_min)
            time_block.move_reason = f"Snoozed {snooze_min}min by user"
        explanation = f"User snoozed block '{time_block.title if time_block else '?'}' by {snooze_min}min"
        audit_kind = AuditEventKind.BLOCK_MOVED

    else:
        raise HTTPException(status_code=422, detail=f"Unknown action: {body.action}")

    audit = AuditEvent(
        user_id=user_id,
        kind=audit_kind,
        actor="user",
        time_block_id=time_block.id if time_block else None,
        explanation=explanation,
        event_metadata={"action": body.action, "notification_id": str(body.notification_id)},
    )
    db.add(audit)
    db.commit()

    return {"status": "ok", "action": body.action}
