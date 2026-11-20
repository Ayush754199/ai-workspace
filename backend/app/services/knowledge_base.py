"""Retrieval over the private knowledge base.

The PDFs are never served to users — they are chunked into `knowledge_chunks`
and this module pulls the passages that best match a question, which the chat
pipeline injects into the model prompt as background knowledge.

Retrieval is deterministic keyword scoring (no external embedding service, no
network call, works on SQLite and Postgres):

1. Normalise the query to meaningful tokens (lowercase, stopwords removed).
2. Pull candidate chunks whose text contains any of the ~8 rarest tokens, so a
   domain word like "materia" does not drag in the whole table.
3. Score each candidate: token hits weighted by inverse document frequency, plus
   an exact-phrase bonus. Return the best few chunks.

This is deliberately simple and observable; swapping in embeddings later only
needs to replace `retrieve()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.library import KnowledgeChunk

# Words that carry no retrieval signal in this domain.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "have", "has", "had", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "about", "as", "into", "through", "during", "before",
    "after", "above", "below", "between", "and", "or", "but", "if", "then",
    "than", "so", "such", "no", "not", "only", "own", "same", "too", "very",
    "just", "also", "how", "what", "when", "where", "which", "who", "whom",
    "why", "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "she", "it", "they", "them", "their", "please", "tell",
    "give", "explain", "describe", "write", "make", "create", "list", "kya",
    "kaise", "kya", "hai", "hain", "ka", "ke", "ki", "ko", "se", "me", "mein",
    "aur", "ya", "par", "bata", "batao", "chahiye", "karo", "kar", "do", "de",
}

MIN_TOKEN_LEN = 3
MAX_QUERY_TOKENS = 12
CANDIDATE_LIMIT = 400
DEFAULT_RESULTS = 4
MIN_SCORE = 1.0


@dataclass(frozen=True)
class RetrievedChunk:
    document_title: str
    subject: str | None
    chunk_index: int
    content: str
    score: float


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]{2,}", (text or "").lower())
    return [w for w in words if len(w) >= MIN_TOKEN_LEN and w not in STOPWORDS]


def _query_tokens(query: str) -> list[str]:
    tokens = _tokenize(query)
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    # Longest tokens are usually the most specific (e.g. "boericke", "miasm").
    return sorted(seen, key=len, reverse=True)[:MAX_QUERY_TOKENS]


def _rarer_tokens(db: Session, tokens: list[str]) -> list[str]:
    """Order tokens from rarest to most common so the LIKE filter stays narrow."""
    counts: list[tuple[str, int]] = []
    for token in tokens:
        hit = (
            db.query(func.count(KnowledgeChunk.id))
            .filter(KnowledgeChunk.content.ilike(f"%{token}%"))
            .scalar()
        )
        counts.append((token, int(hit or 0)))
    counts.sort(key=lambda item: item[1])
    return [token for token, hit in counts if hit > 0]


def retrieve(db: Session, query: str, *, limit: int = DEFAULT_RESULTS) -> list[RetrievedChunk]:
    """Return the best-matching knowledge chunks for a question (possibly empty)."""
    if not query or not query.strip():
        return []
    tokens = _query_tokens(query)
    if not tokens:
        return []
    ranked_tokens = _rarer_tokens(db, tokens)
    if not ranked_tokens:
        return []

    # Pull candidates containing any of the 3 rarest tokens.
    filters = [KnowledgeChunk.content.ilike(f"%{token}%") for token in ranked_tokens[:3]]
    candidates = (
        db.query(KnowledgeChunk)
        .filter(*filters if len(filters) == 1 else [__import__("sqlalchemy").or_(*filters)])
        .limit(CANDIDATE_LIMIT)
        .all()
    )
    if not candidates:
        return []

    # Document frequency for IDF weighting.
    total_chunks = db.query(func.count(KnowledgeChunk.id)).scalar() or 1
    doc_freq: dict[str, int] = {}
    for token in tokens:
        doc_freq[token] = (
            db.query(func.count(KnowledgeChunk.id))
            .filter(KnowledgeChunk.content.ilike(f"%{token}%"))
            .scalar()
        ) or 1

    phrase = (query or "").strip().lower()
    results: list[RetrievedChunk] = []
    for chunk in candidates:
        text_lower = (chunk.content or "").lower()
        if not text_lower:
            continue
        score = 0.0
        for token in tokens:
            hits = text_lower.count(token)
            if hits:
                idf = max(0.1, __import__("math").log(max(total_chunks, 2) / max(doc_freq.get(token, 1), 1)))
                score += hits * idf
        if len(phrase) >= 12 and phrase in text_lower:
            score += 4.0
        if score >= MIN_SCORE:
            results.append(
                RetrievedChunk(
                    document_title=chunk.document_title,
                    subject=chunk.subject,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=round(score, 3),
                )
            )

    results.sort(key=lambda item: item.score, reverse=True)
    return results[:limit]


def build_context(chunks: list[RetrievedChunk], *, max_chars: int = 9000) -> str:
    """Render retrieved chunks as a compact, citable knowledge block."""
    if not chunks:
        return ""
    parts: list[str] = []
    used = 0
    for index, chunk in enumerate(chunks, start=1):
        header = f"[{index}] {chunk.document_title}" + (f" — {chunk.subject}" if chunk.subject else "")
        body = (chunk.content or "").strip()
        block = f"{header}\n{body}"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 400:
                parts.append(block[:remaining])
            break
        parts.append(block)
        used += len(block)
    return (
        "WORKSPACE KNOWLEDGE BASE (authoritative study material; cite as [1], [2], …):\n\n"
        + "\n\n".join(parts)
    )


def build_knowledge_context(db: Session, query: str, *, limit: int = DEFAULT_RESULTS) -> str:
    """Retrieve top chunks for *query* and return a context block, or "" if none."""
    chunks = retrieve(db, query, limit=limit)
    return build_context(chunks)


def knowledge_summary(db: Session) -> dict:
    """Small status payload for the library page / health checks."""
    total = db.query(func.count(KnowledgeChunk.id)).scalar() or 0
    documents = (
        db.query(KnowledgeChunk.document_title, func.count(KnowledgeChunk.id))
        .group_by(KnowledgeChunk.document_title)
        .order_by(KnowledgeChunk.document_title)
        .all()
    )
    return {
        "chunks": int(total),
        "documents": [{"title": title, "chunks": int(count)} for title, count in documents],
    }