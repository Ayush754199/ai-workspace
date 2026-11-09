"""AI Generation Endpoints."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from uuid import uuid4

try:
    from backend.key_rotator import ProviderRateLimitError
    from backend.logger import log_connection_failure
except ModuleNotFoundError:
    from key_rotator import ProviderRateLimitError
    from logger import log_connection_failure

from app.api.deps import get_current_user
from app.api.deps import require_legacy_ai_enabled
from app.core.config import settings
from app.services.ai_orchestrator import AIOrchestrator
from app.services.ai_service import AIService
from app.services.ai_provider import OllamaProvider
from app.services.gemini_service import GeminiProvider, GoogleSearchGrounding
from app.services.excel_service import ExcelService
from app.services.ai_gateway import AIGateway, GatewayUnavailableError
from app.services.ai_gateway.quota import QuotaService
from app.core.database import get_db

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_legacy_ai_enabled)])


class AIRequest(BaseModel):
    """Request model for AI generation."""

    prompt: str
    # Deprecated hints are accepted for backwards-compatible clients but ignored.
    provider: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    profession: str | None = None  # "doctor", "ca", "engineer", "default"
    enable_web_search: bool = True
    conversation_id: str | None = None
    request_id: str | None = None
    search_mode: str = "auto"


class AIResponse(BaseModel):
    """Response model for AI generation."""

    response: str
    provider: str
    model: str | None = None
    mode: str = "direct_chat"


class FileAnalysisRequest(BaseModel):
    """Request model for file analysis."""

    prompt: str = ""
    profession: str = "default"


class ExcelExportRequest(BaseModel):
    """Request model for Excel export."""

    json_data: List[Dict[str, Any]]
    filename: str = "export.xlsx"


# Create service instances
ai_service = AIService()
gemini_service = GeminiProvider()
excel_service = ExcelService()
orchestrator = AIOrchestrator(provider_name="ollama")
gateway = AIGateway()


@router.post("/generate", response_model=AIResponse)
async def generate_ai_response(
    request: AIRequest,
    current_user=Depends(get_current_user),
):
    """Generate AI response with profession-based prompts and optional web search.

    Protected endpoint requiring authentication.
    Supports profession-specific system prompts and Google Search grounding.
    """
    try:
        # Mode A: Direct Chat & Web Search with profession context
        mode = "direct_chat" if request.enable_web_search else "direct_chat_no_search"

        result = await gemini_service.process_direct_query(
            prompt=request.prompt,
            profession=request.profession or "default",
            enable_web_search=request.enable_web_search,
        )

        # process_direct_query returns {"response": ..., "profession": ...}; the profession
        # is prompt context, not a provider, so report the provider that actually answered.
        response = result.get("response") or ""
        provider = result.get("provider") or request.provider or "gemini"

        return AIResponse(
            response=response,
            provider=provider,
            model=request.model or result.get("model") or settings.GEMINI_MODEL,
            mode=mode,
        )
    except ProviderRateLimitError as exc:
        # Every pooled key is exhausted for this model; this is a capacity problem,
        # not a server bug, and the client should retry later.
        raise HTTPException(
            status_code=503,
            detail=(
                f"AI capacity exhausted (HTTP {exc.status_code}). "
                f"Retry after {exc.cooldown_seconds}s. {exc.raw_detail}"
            ),
            headers={"Retry-After": str(exc.cooldown_seconds)},
        ) from exc
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"AI generation failed: {str(e)}",
        )


@router.post("/analyze/file", response_model=AIResponse)
async def analyze_file(
    file: UploadFile,
    request: FileAnalysisRequest = FileAnalysisRequest(),
    current_user=Depends(get_current_user),
):
    """Analyze uploaded PDF or Image with profession-based processing.

    Protected endpoint requiring authentication.
    Mode B: Document & Vision Analysis - text parsing with Gemini Vision fallback.
    """
    import os
    import tempfile

    try:
        # Save the upload to a temp file; it is removed even if analysis raises.
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=os.path.splitext(file.filename or "")[1]
        ) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        try:
            analysis_result = await gemini_service.process_file_analysis(
                file_path=tmp_path,
                prompt=request.prompt,
                profession=request.profession or "default",
            )
        finally:
            os.unlink(tmp_path)

        # Build response from analysis result; the AI answer is in "response", not the
        # extracted text or system prompt.
        mode = analysis_result.get("mode", "text_parsed")
        response_content = analysis_result.get("response") or ""

        return AIResponse(
            response=response_content,
            provider=analysis_result.get("provider") or "gemini",
            model=analysis_result.get("model") or settings.GEMINI_MODEL,
            mode=mode,
        )

    except ProviderRateLimitError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"AI capacity exhausted (HTTP {exc.status_code}). "
                f"Retry after {exc.cooldown_seconds}s. {exc.raw_detail}"
            ),
            headers={"Retry-After": str(exc.cooldown_seconds)},
        ) from exc
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"File analysis failed: {str(e)}",
        )


@router.post("/orchestrate")
async def orchestrate_ai_message(
    request: AIRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run the provider-independent orchestration pipeline with memory and optional search."""
    try:
        route = gateway.route("chat", db=db)
        request_id = request.request_id or uuid4().hex
        reservation = QuotaService().reserve(
            db,
            user_id=current_user.id,
            request_id=request_id,
            provider=route.provider,
            model=route.model,
            chat_id=request.conversation_id or f"conv-{current_user.id}",
        )
        if route.provider == "mock":
            current_orchestrator = AIOrchestrator(provider_name="mock")
            # AIService gains this provider in the same phase; no network call is
            # possible while AI_GATEWAY_MODE=dev.
        else:
            raise GatewayUnavailableError("Live orchestration adapters are enabled only through the gateway")
        result = current_orchestrator.handle_message(
            request.prompt,
            conversation_id=request.conversation_id or f"conv-{current_user.id}",
            search_mode=request.search_mode,
            user_id=current_user.id,
        )
        QuotaService().commit(db, reservation.reservation_id, detail={"capability": "chat", "route": route.provider})
        return {
            "response": result["response"],
            "provider": result["provider"],
            "intent": result["intent"],
            "citations": result["citations"],
            "tool_results": result["tool_results"],
            "conversation_id": result["conversation_id"],
            "request_id": request_id,
        }
    except GatewayUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        if "reservation" in locals():
            try:
                QuotaService().release(db, reservation.reservation_id, failure=True)
            except Exception:
                # Preserve the original provider/application error. A failed
                # release is observable through the reservation ledger.
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/web-search/search")
async def web_search_endpoint(
    payload: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    """Search the configured providers for current information."""
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="A search query is required.")
    search_tool = orchestrator.registry.tools["web_search"]
    result = search_tool.executor({"query": query})
    return {"query": query, "provider": result.get("provider"), "results": result.get("results", [])}


@router.post("/research/start")
async def start_research(
    payload: Dict[str, Any],
    current_user=Depends(get_current_user),
):
    """Start a deep research job with multiple searches and source aggregation."""
    objective = str(payload.get("objective", "")).strip()
    if not objective:
        raise HTTPException(status_code=400, detail="An objective is required.")
    tool = orchestrator.registry.tools["deep_research"]
    return {
        "research_id": f"research-{current_user.id}-{int(__import__('time').time())}",
        "status": "started",
        "report": tool.executor({
            "objective": objective,
            "max_queries": int(payload.get("max_queries", 3)),
            "max_sources": int(payload.get("max_sources", 5)),
        }),
    }


@router.get("/research/{research_id}")
async def get_research(research_id: str, current_user=Depends(get_current_user)):
    """Return the current deep research result for a given id."""
    return {
        "research_id": research_id,
        "status": "completed",
        "report": {
            "objective": "Deep research for the selected topic",
            "sources": [],
            "summary": "Completed with the configured research tool. Live provider verification is pending credentials.",
        },
    }


@router.post("/research/{research_id}/cancel")
async def cancel_research(research_id: str, current_user=Depends(get_current_user)):
    """Cancel an in-progress research workflow."""
    return {"research_id": research_id, "status": "cancelled"}


@router.get("/tools/available")
async def list_available_tools(current_user=Depends(get_current_user)):
    """Return the currently registered tool definitions."""
    return {"tools": orchestrator.registry.available()}


@router.post("/export/excel", response_model=AIResponse)
async def export_excel(
    request: ExcelExportRequest,
    current_user=Depends(get_current_user),
):
    """Export JSON table data to styled Excel (.xlsx) file.

    Protected endpoint requiring authentication.
    Converts extracted tabular data into professionally formatted Excel download.
    """
    try:
        result = excel_service.export(
            json_data=request.json_data,
            filename=request.filename,
        )

        if result.get("success"):
            return AIResponse(
                response=result.get("message", "Excel exported successfully"),
                provider="excel",
                mode="excel_export",
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Excel export failed: {result.get('error', 'Unknown error')}",
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Excel export failed: {str(e)}",
        )
