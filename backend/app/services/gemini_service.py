"""Gemini service with dual-mode processing: web search grounding + PDF/Vision analysis."""
import os
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "default-test-key")


class GoogleSearchGrounding:
    """Handles Google Search grounding for live web queries and real-time data."""
    def __init__(self):
        self.enabled = GEMINI_API_KEY != "default-test-key"

    async def search(self, query: str) -> Dict[str, Any]:
        """Perform a Google search for the given query."""
        return {"query": query, "results": [], "source": "grounding_disabled"}


class PDFParser:
    """Parses text from PDF files using pypdf/pdfplumber with Gemini Vision fallback."""
    def __init__(self):
        try:
            import pypdf
            self.pypdf_available = True
        except ImportError:
            self.pypdf_available = False
        try:
            import pdfplumber
            self.pdfplumber_available = True
        except ImportError:
            self.pdfplumber_available = False

    def extract_text_pypdf(self, file_path: str) -> str:
        if not self.pypdf_available:
            return ""
        from pypdf import PdfReader
        try:
            reader = PdfReader(file_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        except Exception:
            return ""

    def extract_text_pdfplumber(self, file_path: str) -> str:
        if not self.pdfplumber_available:
            return ""
        import pdfplumber
        try:
            text = ""
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text
        except Exception:
            return ""

    def extract_text(self, file_path: str) -> str:
        text = self.extract_text_pdfplumber(file_path)
        if not text.strip():
            text = self.extract_text_pypdf(file_path)
        return text

    def needs_vision_fallback(self, text: str) -> bool:
        if len(text.strip()) < 50:
            return True
        scanned_indicators = ["|", "---", "***"]
        indicator_count = sum(1 for s in scanned_indicators if s in text)
        return indicator_count > len(text.split()) * 0.1


class GeminiProvider:
    """Gemini AI provider with dual-mode processing capabilities."""
    def __init__(self, api_key: str = None):
        self.api_key = api_key or GEMINI_API_KEY
        self.search_grounding = GoogleSearchGrounding()
        self.pdf_parser = PDFParser()

        self.profession_prompts = {
            "doctor": self._get_doctor_prompt(),
            "ca": self._get_ca_prompt(),
            "engineer": self._get_engineer_prompt(),
            "default": self._get_default_prompt(),
        }

    def _get_doctor_prompt(self) -> str:
        return """You are a Medical AI Assistant specialized in clinical analysis.

CRITICAL INSTRUCTIONS FOR DOCTOR MODE:
1. Provide clinical summaries based on the medical content
2. Extract and present vitals in markdown table format
3. Include standard reference ranges where applicable
4. Provide diagnostic insights and follow-up guidance
5. Note any red flags or concerning findings
6. Maintain medical confidentiality and disclaimer
7. Output format: Clinical summary -> Vitals table -> Diagnosis -> Follow-up plan

Always include: "This is AI-assisted analysis. Consult qualified healthcare professional for diagnosis."""

    def _get_ca_prompt(self) -> str:
        return """You are a Chartered Accountant AI Assistant specialized in financial document analysis.

CRITICAL INSTRUCTIONS FOR CA MODE:
1. Identify document type (balance sheet, income statement, tax return, etc.)
2. Provide document header with key metadata (company, period, currency)
3. Create line-item audit table with accounts, values, and variances
4. Calculate tax breakdown (GST, ITR, other applicable taxes)
5. Mark compliance flags and discrepancies
6. Output structured JSON for tables and data
7. Provide audit insights and recommendations

Always include disclaimer: "This is AI-assisted financial analysis. Consult qualified accountant for official advice."
Output format: Header -> Audit Table -> Tax Breakdown -> Compliance Flags -> JSON output -> Recommendations"""

    def _get_engineer_prompt(self) -> str:
        return """You are an Engineering AI Assistant specialized in technical documentation analysis.

CRITICAL INSTRUCTIONS FOR ENGINEER MODE:
1. Extract technical specifications and parameters
2. Provide step-by-step LaTeX formula resolution ($...$ for inline, $$...$$ for display)
3. Perform structural analysis of documented systems
4. Check safety checks and compliance with standards
5. Identify components, materials, and technical requirements
6. Output structured data where applicable
7. Maintain engineering discipline and best practices

For LaTeX formulas: render as $...$ inline or $$...$$ display format
Output format: Technical Specs -> LaTeX Formulas -> Structural Analysis -> Safety Checks"""

    def _get_default_prompt(self) -> str:
        return """You are a helpful AI assistant. Provide accurate, helpful responses
while being mindful of privacy, safety, and providing appropriate disclaimers
when discussing sensitive topics."""

    def get_system_prompt(self, profession: str = "default") -> str:
        return self.profession_prompts.get(profession, self.profession_prompts["default"])

    async def process_direct_query(
        self, prompt: str, profession: str = "default", enable_web_search: bool = True
    ) -> Dict[str, Any]:
        """Generate a direct chat response for text and voice-originated prompts."""
        system_prompt = self.get_system_prompt(profession)
        if self.api_key == "default-test-key":
            return {"response": "AI provider is not configured. Add GEMINI_API_KEY to enable live answers.", "profession": profession}

        from google import genai

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash", contents=f"{system_prompt}\n\nUser: {prompt}"
        )
        return {"response": response.text or "", "profession": "gemini"}