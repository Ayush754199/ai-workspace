"""AI Generation Endpoints."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from typing import List, Dict, Any

from app.api.deps import get_current_user
from app.services.ai_service import AIService
from app.services.ai_provider import OllamaProvider
from app.services.gemini_service import GeminiProvider, GoogleSearchGrounding
from app.services.excel_service import ExcelService

router = APIRouter(prefix="/ai", tags=["ai"])


class AIRequest(BaseModel):
    """Request model for AI generation."""

    prompt: str
    provider: str | None = None  # "gemini", "openai", "ollama"
    model: str | None = None
    system_prompt: str | None = None
    profession: str | None = None  # "doctor", "ca", "engineer", "default"
    enable_web_search: bool = True


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

        response = result.get("combined_prompt", request.prompt)

        return AIResponse(
            response=response,
            provider=result.get("profession", request.provider or "ollama"),
            model=request.model,
            mode=mode,
        )
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
    try:
        # Save uploaded file temporarily
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(
            delete=False, suffix=os.path.splitext(file.filename)[1]
        ) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        # Process file analysis
        analysis_result = await gemini_service.process_file_analysis(
            file_path=tmp_path,
            prompt=request.prompt,
            profession=request.profession or "default",
        )

        # Clean up temp file
        os.unlink(tmp_path)

        # Build response from analysis result
        mode = analysis_result.get("mode", "text_parsed")
        extracted_text = analysis_result.get("extracted_text", "")

        response_content = (
            f"{analysis_result.get('system_prompt', '')}\n\n"
            f"Extracted Content: {extracted_text}\n\n"
            f"User Query: {request.prompt}"
        )

        return AIResponse(
            response=response_content,
            provider=mode,
            mode=mode,
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"File analysis failed: {str(e)}",
        )


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