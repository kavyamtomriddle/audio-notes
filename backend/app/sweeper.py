import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Upload


@dataclass(frozen=True)
class SweepResult:
    job_id: uuid.UUID
    action: str          # "abandoned" | "timed_out" | "summary_timed_out"
    storage_path: str


ABANDONED_AFTER = timedelta(minutes=30)
PIPELINE_TIMEOUT = timedelta(hours=2)
SUMMARY_TIMEOUT = timedelta(minutes=30)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def sweep(session: AsyncSession, now: datetime | None = None) -> list[SweepResult]:
    if now is None:
        now = datetime.now(timezone.utc)
    
    # We must treat the provided 'now' as aware for comparisons.
    now = _aware(now)

    # Fetch the rows that are NOT in terminal states.
    stmt = select(Upload).where(
        Upload.status.in_(["awaiting_upload", "queued", "transcribing", "summarizing"])
    )
    
    result = await session.execute(stmt)
    rows = result.scalars().all()
    
    sweep_results = []
    
    for row in rows:
        # skip rows whose lease_expires_at is not None and > now
        lease_exp = _aware(row.lease_expires_at)
        if lease_exp is not None and lease_exp > now:
            continue
            
        action = None
        
        if row.status == "awaiting_upload":
            age_dt = _aware(row.created_at)
            if age_dt is not None and now - age_dt > ABANDONED_AFTER:
                row.status = "failed"
                row.error_code = "UPLOAD_ABANDONED"
                row.error_message = "Upload abandoned."
                row.retryable = False
                action = "abandoned"
                
        elif row.status in ("queued", "transcribing"):
            age_dt = _aware(row.processing_started_at) or _aware(row.queued_at) or _aware(row.created_at)
            if age_dt is not None and now - age_dt > PIPELINE_TIMEOUT:
                row.status = "failed"
                row.error_code = "PROVIDER_TIMEOUT"
                row.error_message = "Provider timed out."
                row.retryable = True
                action = "timed_out"
                
        elif row.status == "summarizing":
            age_dt = _aware(row.updated_at)
            if age_dt is not None and now - age_dt > SUMMARY_TIMEOUT:
                row.status = "completed"
                row.summary_status = "failed"
                row.summary_error = "Summary timed out"
                row.completed_at = now
                action = "summary_timed_out"
                
        if action:
            row.lease_expires_at = None
            row.updated_at = now
            sweep_results.append(SweepResult(job_id=row.id, action=action, storage_path=row.storage_path))
            
    # Always commit at the end if there are changes.
    # Actually wait, the instruction says "One commit at the end." so we should just commit unconditionally or only if sweep_results is not empty.
    await session.commit()
        
    return sweep_results
