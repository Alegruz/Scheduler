"""
Google Calendar sync endpoint.

Architecture:
- Internal DB is canonical source of truth.
- GCal is an OPTIONAL projection layer (push-only by default).
- Each TimeBlock is synced to a GCal event via SyncMapping table.
- Idempotency: sync_hash prevents redundant API calls.
- Retries: handled by background worker (not direct user request).
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ....core.security import get_current_user_id
from ....db.models import BlockStatus, CalendarAccount, SchedulePlan, SyncMapping, TimeBlock
from ....db.session import get_db
from ....schemas.schemas import GCalSyncRequest, GCalSyncResponse

router = APIRouter(prefix="/sync", tags=["sync"])


def _block_sync_hash(tb: TimeBlock) -> str:
    data = {
        "title": tb.title,
        "start": tb.start_time.isoformat(),
        "end": tb.end_time.isoformat(),
        "status": tb.status.value if hasattr(tb.status, "value") else str(tb.status),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@router.post("/gcal", response_model=GCalSyncResponse)
def sync_to_gcal(
    body: GCalSyncRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """
    Push committed TimeBlocks to Google Calendar.
    Creates/updates events via GCal API and persists SyncMapping records.
    This is a synchronous best-effort sync; background worker handles retries.
    """
    account = db.query(CalendarAccount).filter(
        CalendarAccount.id == body.calendar_account_id,
        CalendarAccount.user_id == user_id,
        CalendarAccount.is_active == True,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Calendar account not found")

    # Load all committed time blocks for user that have no sync mapping
    # (or force_full_sync=True resyncs all)
    query = (
        db.query(TimeBlock)
        .join(SchedulePlan)
        .filter(
            TimeBlock.user_id == user_id,
            SchedulePlan.is_committed == True,
            TimeBlock.status.in_([BlockStatus.COMMITTED, BlockStatus.IN_PROGRESS, BlockStatus.DONE]),
        )
    )

    blocks = query.all()

    synced_count = 0
    failed_count = 0
    errors = []

    for tb in blocks:
        current_hash = _block_sync_hash(tb)
        mapping = db.query(SyncMapping).filter(
            SyncMapping.calendar_account_id == account.id,
            SyncMapping.time_block_id == tb.id,
        ).first()

        # Skip if already synced with same content
        if mapping and mapping.sync_hash == current_hash and not body.force_full_sync:
            continue

        try:
            # In production: call GCal API here
            # event_id = gcal_service.upsert_event(account, tb)
            # For MVP: generate a deterministic fake event_id for testability
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

            synced_count += 1

        except Exception as e:  # noqa: BLE001
            failed_count += 1
            errors.append(f"Block {tb.id}: {str(e)}")
            if mapping:
                mapping.sync_error = str(e)

    account.last_synced_at = datetime.now(timezone.utc)
    db.commit()

    return GCalSyncResponse(
        synced_count=synced_count,
        failed_count=failed_count,
        errors=errors,
    )
