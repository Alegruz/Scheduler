import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ....core.security import get_current_user_id
from ....db.models import AuditEvent, AuditEventKind
from ....db.session import get_db
from ....schemas.schemas import AuditEventResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=List[AuditEventResponse])
def list_audit_events(
    kind: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    q = db.query(AuditEvent).filter(AuditEvent.user_id == user_id)
    if kind:
        try:
            kind_enum = AuditEventKind(kind)
            q = q.filter(AuditEvent.kind == kind_enum)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown audit event kind: {kind}")
    return q.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/{event_id}", response_model=AuditEventResponse)
def get_audit_event(
    event_id: uuid.UUID,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    event = db.query(AuditEvent).filter(
        AuditEvent.id == event_id, AuditEvent.user_id == user_id
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Audit event not found")
    return event
