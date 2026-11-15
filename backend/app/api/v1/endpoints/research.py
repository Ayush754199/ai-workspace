"""Research endpoints: sourced answers built from live web search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.core.errors import log_and_mask
from app.services.feature_flags import require_feature
from app.services import research as research_service

router = APIRouter(prefix="/research", tags=["research"], dependencies=[Depends(require_feature("research"))])


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=6, ge=1, le=10)
    topic: str | None = Field(default=None, pattern="^(general|news|finance)$")


@router.post("/query")
def query(
    payload: ResearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = research_service.research(payload.question, db=db, user=user, limit=payload.limit, topic=payload.topic)
    except research_service.SearchUnavailable as exc:
        # SearchUnavailable is the service's safe, curated error type. Preserve its
        # quota-vs-outage code, but mask any unexpected string before returning it.
        status_code = 429 if "quota" in str(exc).lower() else 503
        detail = log_and_mask(exc, endpoint="research/query", operation="research_query", fallback="Web research is unavailable.")
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001 - provider detail stays in the log
        raise HTTPException(
            status_code=503,
            detail=log_and_mask(exc, endpoint="research/query", operation="research_query", fallback="Web research failed. Please retry."),
        ) from exc
    if not result["sources"]:
        raise HTTPException(status_code=404, detail="No results were returned for this question")
    return result


@router.post("/search")
def search(payload: ResearchRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        report = research_service.research(payload.question, db=db, user=user, limit=payload.limit, snippets_only=True, topic=payload.topic, analyze=False)
    except research_service.SearchUnavailable as exc:
        status_code = 429 if "quota" in str(exc).lower() else 503
        detail = log_and_mask(exc, endpoint="research/search", operation="research_search", fallback="Web search is unavailable.")
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001 - provider detail stays in the log
        raise HTTPException(
            status_code=503,
            detail=log_and_mask(exc, endpoint="research/search", operation="research_search", fallback="Web search failed. Please retry."),
        ) from exc
    return {"query": report["query"], "sources": report["sources"], "usage": report.get("usage", {})}
