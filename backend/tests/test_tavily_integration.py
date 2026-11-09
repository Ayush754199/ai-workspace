# Mocked Tavily API contracts and independent daily usage ledger coverage.
from __future__ import annotations
import uuid
import pytest
from app.models.gateway import CapabilityQuota, UsageLog
from app.models.user import User
from app.services import research, tavily
from app.services.ai_gateway import quota as quota_service_module
@pytest.fixture(autouse=True)
def isolate_quota_clock(monkeypatch):
    # Use today's date so local historical quota rows cannot interfere.
    from datetime import date
    monkeypatch.setattr(quota_service_module, "_today", lambda: date.today())

class FakeResponse:

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload or {}
    def json(self):
        return self._payload

def test_tavily_retries_only_eligible_technical_failure(monkeypatch):
    monkeypatch.setattr(tavily.settings, "TAVILY_API_KEY_1", "account-one")
    monkeypatch.setattr(tavily.settings, "TAVILY_API_KEY_2", "account-two")
    monkeypatch.setattr(tavily.settings, "TAVILY_TECHNICAL_FAILOVER_ENABLED", True)
    seen = []
    responses = [FakeResponse(503), FakeResponse(payload={"results": [], "usage": {"credits": 1}})]
    def post(url, **kwargs):
        seen.append(kwargs["headers"]["Authorization"])
        return responses.pop(0)
    monkeypatch.setattr(tavily.requests, "post", post)
    result = tavily.request("search", {"query": "safe"})
    assert seen == ["Bearer account-one", "Bearer account-two"]
    assert result.account == "account_2"

def test_tavily_does_not_rotate_on_authentication_failure(monkeypatch):
    monkeypatch.setattr(tavily.settings, "TAVILY_API_KEY_1", "account-one")
    monkeypatch.setattr(tavily.settings, "TAVILY_API_KEY_2", "account-two")
    seen = []
    def post(_url, **kwargs):
        seen.append(kwargs["headers"]["Authorization"])
        return FakeResponse(401)
    monkeypatch.setattr(tavily.requests, "post", post)
    with pytest.raises(tavily.TavilyError) as error:
        tavily.request("search", {"query": "safe"})
    assert error.value.status_code == 401
    assert seen == ["Bearer account-one"]

def test_tavily_search_maps_dates_and_news_topic(monkeypatch):
    captured = {}
    monkeypatch.setattr(tavily, "request", lambda endpoint, payload: (captured.update({"endpoint": endpoint, **payload}) or tavily.TavilyResult(
        {"results": [{"title": "Headline", "url": "https://example.com/news", "content": "Story", "published_date": "2026-11-06"}], "usage": {"credits": 1}, "request_id": "req-1"}, "account_1")))
    rows, usage = tavily.search("today news", topic="news")
    assert captured["endpoint"] == "search"
    assert captured["topic"] == "news"
    assert captured["search_depth"] == "basic"
    assert captured["include_usage"] is True
    assert rows == [{"title": "Headline", "url": "https://example.com/news", "snippet": "Story", "published_date": "2026-11-06"}]
    assert usage == {"credits": 1, "account": "account_1", "request_id": "req-1"}

def test_search_and_extraction_commit_distinct_quota_and_account_usage(db_session, monkeypatch):
    identifier = f"tavily-{uuid.uuid4().hex}@example.test"
    user = User(email=identifier, identifier=identifier, full_name="Tavily Test", hashed_password="test-hash")
    db_session.add(user)
    db_session.commit()
    def fake_search(*_args, **_kwargs):
        return ([{"title": "Example", "url": "https://example.com/article", "snippet": "Useful result", "published_date": "2026-11-06"}], {"credits": 1, "account": "account_1", "request_id": "search-req"})
    def fake_extract(*_args, **_kwargs):
        return ({"https://example.com/article": "Extracted page text"}, {"credits": 0, "account": "account_2", "request_id": "extract-req", "successful_urls": 1})
    monkeypatch.setattr(research.tavily, "search", fake_search)
    monkeypatch.setattr(research.tavily, "extract", fake_extract)
    sources, _usage = research._search("technical research", db=db_session, user=user, limit=3)
    context, _extract_usage = research._extract(sources, db=db_session, user=user, question="technical research")
    assert "Extracted page text" in context
    counters = {row.capability: row.daily_used for row in db_session.query(CapabilityQuota).filter(CapabilityQuota.user_id == user.id).all()}
    assert counters == {"search": 1, "extraction": 1}
    logs = db_session.query(UsageLog).filter(UsageLog.user_id == user.id, UsageLog.provider == "tavily").all()
    assert len(logs) == 2
    assert {entry.detail["account"] for entry in logs} == {"account_1", "account_2"}
    assert {entry.detail["credits"] for entry in logs} == {0, 1}
    assert db_session.query(UsageLog).filter(UsageLog.user_id == user.id, UsageLog.provider == "tavily").count() == 2
