"""
Health / context signal ingestion endpoint.
Android Health Connect sends signals here; they are stored as soft context
and used by the scheduling engine during nightly planning.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ....core.security import get_current_user_id
from ....db.models import ContextSignal
from ....db.session import get_db
from ....schemas.schemas import ContextSignalIngest, ContextSignalResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.post("/signals", response_model=ContextSignalResponse, status_code=status.HTTP_201_CREATED)
def ingest_signal(
    body: ContextSignalIngest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    signal = ContextSignal(
        user_id=user_id,
        signal_type=body.signal_type,
        value=body.value,
        recorded_at=body.recorded_at,
        source=body.source,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal


@router.get("/signals", response_model=List[ContextSignalResponse])
def list_signals(
    signal_type: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    q = db.query(ContextSignal).filter(ContextSignal.user_id == user_id)
    if signal_type:
        q = q.filter(ContextSignal.signal_type == signal_type)
    return q.order_by(ContextSignal.recorded_at.desc()).limit(limit).all()
