"""NET-05: video jobs survive a restart, stale jobs fail with a quota refund."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.api.v1.endpoints import video as video_endpoints
from app.core.database import Base
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.gateway import CapabilityQuota, MediaJob, QuotaReservation
from app.models.user import User, UserFeaturePermission
from app.services.ai_gateway.quota import QuotaService


@pytest.fixture()
def video_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'video.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    user = User(email="video@example.com", identifier="video@example.com", full_name="Video User", hashed_password=hash_password("pass"))
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserFeaturePermission(user_id=user.id, feature="video_generation", enabled=True))
    db.commit()
    user_id = user.id
    db.close()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(video_endpoints, "feature_enabled", lambda db, name: True)
    # The recovery routine opens its own session; point it at the test database.
    monkeypatch.setattr(video_endpoints, "SessionLocal", factory)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'video@example.com'})}"}
    yield factory, user_id, headers
    app.dependency_overrides.clear()


def _reserve(factory, user_id, job_id):
    db = factory()
    try:
        return QuotaService().reserve(
            db, user_id=user_id, request_id=job_id, provider="huggingface-wan",
            model="fake-space", capability="video", units=1,
        ).reservation_id
    finally:
        db.close()


def test_interrupted_video_job_is_failed_and_refunded_on_restart(video_env, monkeypatch):
    factory, user_id, _headers = video_env
    job_id = str(uuid.uuid4())
    reservation_id = _reserve(factory, user_id, job_id)

    db = factory()
    db.add(MediaJob(user_id=user_id, kind="video_text", status="processing", request_id=job_id,
                    reservation_id=reservation_id, payload={"job_id": job_id, "prompt": "x", "mode": "text"}))
    db.commit()
    db.close()

    assert video_endpoints.recover_interrupted_jobs() == 1

    db = factory()
    job = db.query(MediaJob).filter_by(request_id=job_id).one()
    assert job.status == "failed"
    assert "stopped reporting" in (job.error or "")
    reservation = db.query(QuotaReservation).filter_by(reservation_id=reservation_id).one()
    assert reservation.state == "FAILED"
    quota = db.query(CapabilityQuota).filter_by(user_id=user_id, capability="video").one()
    assert quota.reserved == 0
    assert quota.daily_used == 0
    db.close()


def test_live_worker_job_survives_another_process_restart(video_env, monkeypatch):
    """Rolling restart: recovery must not fail a job whose heartbeat is fresh."""
    factory, user_id, _headers = video_env
    job_id = str(uuid.uuid4())
    reservation_id = _reserve(factory, user_id, job_id)
    fresh = datetime.now(timezone.utc) - timedelta(seconds=10)  # well inside the window

    db = factory()
    db.add(MediaJob(user_id=user_id, kind="video_text", status="processing", request_id=job_id,
                    reservation_id=reservation_id, worker_id="worker-live", heartbeat_at=fresh,
                    payload={"job_id": job_id, "prompt": "x", "mode": "text"}))
    db.commit()
    db.close()

    assert video_endpoints.recover_interrupted_jobs() == 0

    db = factory()
    job = db.query(MediaJob).filter_by(request_id=job_id).one()
    assert job.status == "processing"
    reservation = db.query(QuotaReservation).filter_by(reservation_id=reservation_id).one()
    assert reservation.state == "RESERVED"
    db.close()


def test_dead_worker_with_fresh_heartbeat_from_another_host_is_recovered(video_env):
    """A heartbeat older than the stale window means the worker is gone."""
    factory, user_id, _headers = video_env
    job_id = str(uuid.uuid4())
    reservation_id = _reserve(factory, user_id, job_id)
    stale = datetime.now(timezone.utc) - timedelta(seconds=video_endpoints.HEARTBEAT_STALE_SECONDS + 30)

    db = factory()
    db.add(MediaJob(user_id=user_id, kind="video_text", status="processing", request_id=job_id,
                    reservation_id=reservation_id, worker_id="worker-gone", heartbeat_at=stale,
                    payload={"job_id": job_id, "prompt": "x", "mode": "text"}))
    db.commit()
    db.close()

    assert video_endpoints.recover_interrupted_jobs() == 1
    db = factory()
    assert db.query(MediaJob).filter_by(request_id=job_id).one().status == "failed"
    db.close()


def test_current_process_owner_job_is_not_recovered_during_startup(video_env):
    """A job owned by THIS process (same WORKER_ID) survives its own startup hook."""
    factory, user_id, _headers = video_env
    job_id = str(uuid.uuid4())
    reservation_id = _reserve(factory, user_id, job_id)

    db = factory()
    db.add(MediaJob(user_id=user_id, kind="video_text", status="processing", request_id=job_id,
                    reservation_id=reservation_id, worker_id=video_endpoints.WORKER_ID,
                    payload={"job_id": job_id, "prompt": "x", "mode": "text"}))
    db.commit()
    db.close()

    assert video_endpoints.recover_interrupted_jobs() == 0


def test_completed_jobs_are_not_touched_by_recovery(video_env):
    factory, user_id, _headers = video_env
    job_id = str(uuid.uuid4())
    db = factory()
    db.add(MediaJob(user_id=user_id, kind="video_text", status="completed", request_id=job_id,
                    payload={"job_id": job_id, "prompt": "x", "mode": "text"}))
    db.commit()
    db.close()

    assert video_endpoints.recover_interrupted_jobs() == 0
    db = factory()
    assert db.query(MediaJob).filter_by(request_id=job_id).one().status == "completed"
    db.close()


def test_job_get_and_list_read_from_database(video_env, monkeypatch):
    factory, user_id, headers = video_env
    job_id = str(uuid.uuid4())
    db = factory()
    db.add(MediaJob(user_id=user_id, kind="video_text", status="completed", request_id=job_id,
                    payload={"job_id": job_id, "prompt": "saved prompt", "mode": "text", "result": {"status": "completed"}}))
    db.commit()
    db.close()

    with TestClient(app) as client:
        single = client.get(f"/api/v1/video/jobs/{job_id}", headers=headers)
        listing = client.get("/api/v1/video/jobs", headers=headers)
    assert single.status_code == 200
    assert single.json()["prompt"] == "saved prompt"
    assert single.json()["status"] == "completed"
    assert listing.status_code == 200
    assert any(item["job_id"] == job_id for item in listing.json())


def test_generate_failure_marks_job_failed_and_refunds(video_env, monkeypatch):
    _factory, _user_id, headers = video_env

    def broken(_prompt, space=None, api_name=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(video_endpoints, "generate_wan_video", broken)
    with TestClient(app) as client:
        response = client.post("/api/v1/video/generate", json={"prompt": "a cat"}, headers=headers)
        jobs = client.get("/api/v1/video/jobs", headers=headers).json()
    assert response.status_code == 502
    assert "quota was released" in response.json()["detail"]
    failed = [job for job in jobs if job["status"] == "failed"]
    assert len(failed) == 1
    assert "quota was released" in failed[0]["error"]


def test_generate_success_persists_completed_job(video_env, monkeypatch):
    _factory, _user_id, headers = video_env
    monkeypatch.setattr(video_endpoints, "generate_wan_video", lambda *_a, **_k: {"status": "completed", "video": "fake.mp4"})
    with TestClient(app) as client:
        response = client.post("/api/v1/video/generate", json={"prompt": "a dog"}, headers=headers)
        job_id = response.json()["job_id"]
        fetched = client.get(f"/api/v1/video/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"
    assert fetched.json()["result"]["video"] == "fake.mp4"
