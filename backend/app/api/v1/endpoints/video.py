"""Video generation and job endpoints for NEXUS AI."""

from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.models.user import User, UserFeaturePermission
from app.models.gateway import MediaJob
from app.services.ai_gateway.quota import QuotaService, QuotaError
from app.services.feature_flags import feature_enabled
from app.services.ai_gateway.managers import VideoProviderManager
from backend.media_service import generate_wan_video

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video", tags=["video"])

VIDEO_FEATURES = {"video_generation", "video_editing", "video_understanding"}


class VideoGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    space: str | None = None
    api_name: str | None = None


def _job_dict(job: MediaJob) -> dict[str, Any]:
    payload = dict(job.payload or {})
    return {
        "job_id": payload.get("job_id"),
        "user_id": job.user_id,
        "prompt": payload.get("prompt", ""),
        "mode": payload.get("mode", job.kind),
        "result": payload.get("result"),
        "status": job.status,
        "error": job.error,
    }


WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
# A live worker must have pinged within this window for its job to be considered
# still running. Anything older is dead regardless of the worker id.
HEARTBEAT_STALE_SECONDS = 120


def record_job(
    db: Session,
    *,
    job_id: str,
    user_id: int,
    prompt: str,
    mode: str,
    status: str,
    result: Any = None,
    error: str | None = None,
    reservation_id: str | None = None,
) -> MediaJob:
    """Persist a job so its state survives a process restart."""
    job = db.query(MediaJob).filter(MediaJob.request_id == job_id).first()
    if job is None:
        job = MediaJob(user_id=user_id, kind=f"video_{mode}", request_id=job_id)
        db.add(job)
    job.status = status
    job.error = error
    job.progress = 100 if status == "completed" else 0
    job.payload = {"job_id": job_id, "prompt": prompt, "mode": mode, "result": result}
    if reservation_id:
        job.reservation_id = reservation_id
    if status == "processing":
        # Stamp the owner and a fresh heartbeat each time work (re)starts.
        job.worker_id = WORKER_ID
        job.heartbeat_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


def heartbeat(job_id: str, *, db: Session | None = None) -> None:
    """Refresh the liveness stamp so recovery knows this job is still running."""
    own = db is None
    session = db or SessionLocal()
    try:
        session.query(MediaJob).filter(MediaJob.request_id == job_id).update(
            {"heartbeat_at": datetime.now(timezone.utc), "worker_id": WORKER_ID}
        )
        session.commit()
    finally:
        if own:
            session.close()


def recover_interrupted_jobs(*, now: datetime | None = None, db: Session | None = None) -> int:
    """Fail only the jobs whose worker stopped reporting, and refund them.

    Jobs held by a live worker (heartbeat inside the stale window, or the worker
    id equal to this process) are left untouched — a rolling restart must not
    kill another instance's running job.
    """
    own = db is None
    session = db or SessionLocal()
    recovered = 0
    try:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
        stale = (
            session.query(MediaJob)
            .filter(MediaJob.status.in_(("queued", "processing")))
            .all()
        )
        for job in stale:
            beat = job.heartbeat_at
            if beat is not None and beat.tzinfo is None:
                beat = beat.replace(tzinfo=timezone.utc)
            alive = (
                beat is not None and beat >= cutoff
            ) or job.worker_id == WORKER_ID
            if alive:
                continue
            job.status = "failed"
            job.error = "Interrupted: worker stopped reporting; quota refunded"
            job.progress = 0
            if job.reservation_id:
                try:
                    QuotaService().release(session, job.reservation_id, failure=True)
                except Exception:  # noqa: BLE001 - recovery must never block startup
                    session.rollback()
                    logger.warning("video job refund skipped; reservation not releasable", exc_info=True)
            job.status = "failed"
            job.error = "Interrupted: worker stopped reporting; quota refunded"
            recovered += 1
        if recovered:
            session.commit()
    except Exception:  # noqa: BLE001 - startup must not fail because of old rows
        session.rollback()
        logger.warning("video job recovery failed", exc_info=True)
        return recovered
    finally:
        if own:
            session.close()
    return recovered


def _has_feature(db: Session, user: User, feature: str) -> bool:
    permission = (
        db.query(UserFeaturePermission)
        .filter(UserFeaturePermission.user_id == user.id, UserFeaturePermission.feature == feature)
        .first()
    )
    return bool(permission and permission.enabled)


@router.post("/generate")
def generate_video(
    request: VideoGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _has_feature(db, current_user, "video_generation"):
        raise HTTPException(status_code=403, detail="video_generation is disabled for this user")
    if not feature_enabled(db, "video_generation"):
        raise HTTPException(status_code=503, detail="Video generation is disabled by an administrator")
    job_id = str(uuid.uuid4())
    reservation = None
    try:
        reservation = QuotaService().reserve(
            db, user_id=current_user.id, request_id=job_id, provider="huggingface-wan",
            model=request.space or "configured", capability="video", units=1,
        )
        record_job(
            db, job_id=job_id, user_id=current_user.id, prompt=request.prompt,
            mode="text", status="processing", reservation_id=reservation.reservation_id,
        )
        heartbeat(job_id, db=db)
        result = generate_wan_video(request.prompt, space=request.space, api_name=request.api_name)
        if isinstance(result, dict) and result.get("status") not in {"completed", "queued"}:
            raise RuntimeError(result.get("message") or "The video provider did not complete the job")
        heartbeat(job_id, db=db)
        QuotaService().commit(db, reservation.reservation_id, detail={"mode": "text"})
    except QuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        if reservation:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            record_job(
                db, job_id=job_id, user_id=current_user.id, prompt=request.prompt,
                mode="text", status="failed", error="Video generation failed; quota was released",
            )
        raise HTTPException(status_code=502, detail="Video generation failed; quota was released") from exc
    job = record_job(
        db, job_id=job_id, user_id=current_user.id, prompt=request.prompt,
        mode="text", status="completed", result=result, reservation_id=reservation.reservation_id,
    )
    return _job_dict(job)


@router.post("/image-to-video")
async def image_to_video(
    prompt: str = Form(default=""),
    image: UploadFile = File(...),
    space: str | None = Form(default=None),
    api_name: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Image-to-video through the same job store as prompt-to-video.

    The upload is handed to the configured Hugging Face image-to-video Space.
    When no such Space is configured the endpoint says so instead of returning
    an unrelated text-to-video result.
    """
    if not _has_feature(db, current_user, "video_generation"):
        raise HTTPException(status_code=403, detail="video_generation is disabled for this user")
    if not feature_enabled(db, "video_generation"):
        raise HTTPException(status_code=503, detail="Video generation is disabled by an administrator")
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="Upload a starting image")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image exceeds the 20 MB limit")
    from app.services.video_service import image_to_video as run_image_to_video

    job_id = str(uuid.uuid4())
    reservation = None
    try:
        reservation = QuotaService().reserve(
            db, user_id=current_user.id, request_id=job_id, provider="huggingface-wan",
            model=space or "configured", capability="video", units=1,
        )
        record_job(
            db, job_id=job_id, user_id=current_user.id, prompt=prompt,
            mode="image", status="processing", reservation_id=reservation.reservation_id,
        )
        result = run_image_to_video(data, image.filename or "frame.png", prompt, space=space, api_name=api_name)
        if isinstance(result, dict) and result.get("status") not in {"completed", "queued"}:
            raise RuntimeError(result.get("message") or "The image-to-video provider did not complete the job")
        QuotaService().commit(db, reservation.reservation_id, detail={"mode": "image"})
    except QuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        if reservation:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            record_job(
                db, job_id=job_id, user_id=current_user.id, prompt=prompt,
                mode="image", status="failed", error="Image-to-video failed; quota was released",
            )
        raise HTTPException(status_code=502, detail="Image-to-video failed; quota was released") from exc
    job = record_job(
        db, job_id=job_id, user_id=current_user.id, prompt=prompt,
        mode="image", status="completed", result=result, reservation_id=reservation.reservation_id,
    )
    return _job_dict(job)


@router.get("/jobs")
def list_video_jobs(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = (
        db.query(MediaJob)
        .filter(MediaJob.user_id == current_user.id)
        .order_by(MediaJob.id.desc())
        .all()
    )
    return [_job_dict(job) for job in jobs]


@router.get("/capabilities")
def video_capabilities(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What video generation can actually do on this server right now."""
    from app.services.feature_flags import feature_enabled

    from app.core.config import settings
    status = VideoProviderManager().status()
    text_ready = bool(settings.HF_TEXT_TO_VIDEO_SPACE.strip())
    image_ready = bool(settings.HF_IMAGE_TO_VIDEO_SPACE.strip())
    return {
        "feature_enabled": feature_enabled(db, "video_generation"),
        "user_permitted": _has_feature(db, current_user, "video_generation"),
        "text_to_video": {"ready": text_ready, "space": settings.HF_TEXT_TO_VIDEO_SPACE.strip() or None},
        "image_to_video": {"ready": image_ready, "space": settings.HF_IMAGE_TO_VIDEO_SPACE.strip() or None},
        "providers": list(status.providers),
        "paid_providers_enabled": status.paid_enabled,
    }


@router.get("/jobs/{job_id}")
def get_video_job(job_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(MediaJob).filter(MediaJob.request_id == job_id, MediaJob.user_id == current_user.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Video job not found")
    return _job_dict(job)
