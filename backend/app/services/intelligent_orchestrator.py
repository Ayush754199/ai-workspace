"""Context-aware, capability-first routing policy for chat requests.

This module deliberately does not call an extra classifier model for every turn.
It derives a structured request plan from the persisted conversation, attachments,
explicit user controls, and task signals, then scores only verified compatible
routes. The result is auditable and cheap enough to run on every request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable


@dataclass(frozen=True)
class IntentAssessment:
    capability: str = "chat"
    complexity: float = 0.0
    needs_current_information: bool = False
    needs_reasoning: bool = False
    needs_multimodal: bool = False
    needs_image_generation: bool = False
    needs_image_editing: bool = False
    needs_document_analysis: bool = False
    follow_up: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


def assess_intent(prompt: str, *, conversation: Iterable[dict] = (), attachments: Iterable[dict] = (), requested_capability: str = "chat") -> IntentAssessment:
    text = (prompt or "").strip()
    lower = text.casefold()
    turns = list(conversation)
    files = list(attachments)
    reasons: list[str] = []
    current = bool(re.search(r"\b(today|latest|current|recent|news|this week|right now|as of)\b", lower))
    reasoning = bool(re.search(r"\b(compare|evaluate|design|debug|architecture|trade[- ]?off|derive|prove|multi[- ]step|root cause|strategy)\b", lower))
    image_generate = bool(re.search(r"\b(generate|create|make|draw)\b.*\b(image|picture|illustration|logo|poster)\b", lower))
    image_edit = bool(files and any(str(item.get("kind", "")).casefold() == "image" for item in files) and bool(text))
    document = bool(files and any(str(item.get("kind", "")).casefold() in {"pdf", "document", "text"} for item in files))
    follow_up = bool(re.match(r"^(it|that|this|these|those|as mentioned|continue|now)\b", lower)) and bool(turns)
    complexity = 0.15
    if len(text) > 500 or text.count("\n") >= 4:
        complexity += 0.2
    if reasoning:
        complexity += 0.35
        reasons.append("requires multi-step reasoning or evaluation")
    if current:
        reasons.append("asks for time-sensitive information")
    if document:
        complexity += 0.15
        reasons.append("requires local document context")
    if follow_up:
        complexity += 0.15
        reasons.append("depends on prior conversation context")
    if image_generate or image_edit:
        reasons.append("requires an image-capable route")
    complexity = min(1.0, complexity)
    capability = requested_capability
    if image_generate or image_edit:
        capability = "image"
    elif current:
        capability = "research"
    elif document:
        capability = "multimodal"
    return IntentAssessment(capability, complexity, current, reasoning, document or image_edit, image_generate, image_edit, document, follow_up, tuple(reasons))


def preferred_providers(assessment: IntentAssessment) -> tuple[str, ...]:
    if assessment.capability == "image":
        return ("muse_image", "cloudflare")
    if assessment.needs_current_information:
        return ("muse_spark", "cloudflare", "gemini", "groq")
    if assessment.needs_reasoning or assessment.complexity >= 0.65:
        return ("muse_spark", "gemini", "groq")
    if assessment.needs_multimodal:
        return ("gemini", "muse_spark", "groq")
    return ("groq", "gemini", "muse_spark")
