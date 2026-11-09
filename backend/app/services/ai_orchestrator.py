"""Central orchestration layer for AI requests, memory, tools, and search."""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from app.services.ai_service import AIService


DEFAULT_SYSTEM_PROMPT = (
    "You are an AI Workspace assistant. Answer using the available context and be explicit "
    "about uncertainty. Quote relevant facts from the retrieved sources when available."
)


@dataclass
class ConversationMessage:
    role: str
    content: str
    created_at: str | None = None
    message_id: str | None = None


class ConversationMemory:
    """In-memory memory for long-running conversations."""

    def __init__(self) -> None:
        self._messages: list[ConversationMessage] = []
        self._summaries: list[str] = []

    def add_message(self, role: str, content: str, *, message_id: str | None = None) -> ConversationMessage:
        record = ConversationMessage(role=role, content=content, created_at=None, message_id=message_id)
        self._messages.append(record)
        return record

    def get_recent(self, window: int = 12) -> list[ConversationMessage]:
        return self._messages[-window:]

    def get_all(self) -> list[ConversationMessage]:
        return list(self._messages)

    def get_relevant(self, query: str, limit: int = 5) -> list[ConversationMessage]:
        text = (query or "").lower()
        if not text:
            return self.get_recent(limit)
        matches: list[tuple[int, ConversationMessage]] = []
        for record in self._messages:
            score = 0
            for token in re.findall(r"[a-zA-Z0-9]+", text):
                if token and token.lower() in (record.content or "").lower():
                    score += 1
            if score:
                matches.append((score, record))
        matches.sort(key=lambda item: item[0], reverse=True)
        ranked = [record for _, record in matches[:limit]]
        return ranked if ranked else self.get_recent(limit)

    def update_summary(self, summary: str) -> None:
        self._summaries.append(summary)

    def get_summary(self) -> str:
        return "\n\n".join(self._summaries) if self._summaries else ""


class TokenManager:
    """Simple token budgeting for context management."""

    def __init__(
        self,
        model: str = "gemini-3.8-flash",
        *,
        context_window: int = 1_048_576,
        output_reserve: int = 512,
        safety_margin: float = 0.10,
    ) -> None:
        self.model = model
        self.context_window = context_window
        self.output_reserve = output_reserve
        self.safety_margin = safety_margin

    def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        words = re.findall(r"\S+", text)
        return max(1, int(len(words) * 1.3))

    def available_tokens(self, messages: Sequence[dict[str, str]] | None = None) -> int:
        budget = self.context_window - self.output_reserve
        if messages is None:
            return int(budget * (1 - self.safety_margin))
        current = sum(self.estimate_tokens(str(item.get("content", ""))) for item in messages)
        reserved = max(0, int(budget * self.safety_margin))
        return max(0, budget - current - reserved)

    def trim_context(self, messages: Sequence[dict[str, str]], *, max_tokens: int | None = None) -> list[dict[str, str]]:
        limit = max_tokens or self.available_tokens(messages)
        trimmed: list[dict[str, str]] = []
        current = 0
        for item in reversed(messages):
            item_tokens = self.estimate_tokens(str(item.get("content", "")))
            if current + item_tokens > limit:
                break
            trimmed.insert(0, item)
            current += item_tokens
        return trimmed


class SearchResult:
    def __init__(self, title: str, url: str, snippet: str, source: str = "") -> None:
        self.title = title
        self.url = url
        self.snippet = snippet
        self.source = source


class SearchProvider:
    """Abstraction for external web search providers."""

    name = "search"

    def search(self, query: str) -> list[SearchResult]:
        return []

    def extract(self, url: str) -> dict[str, Any]:
        return {"url": url, "title": "", "content": ""}


class BraveSearchProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or ""

    def search(self, query: str) -> list[SearchResult]:
        if not self.api_key:
            return [SearchResult("Search not configured", "https://example.com", "Add BRAVE_API_KEY to enable live web search.", self.name)]
        return []


class TavilySearchProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or ""

    def search(self, query: str) -> list[SearchResult]:
        if not self.api_key:
            return []
        return []


class FirecrawlSearchProvider(SearchProvider):
    name = "firecrawl"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or ""

    def extract(self, url: str) -> dict[str, Any]:
        if not self.api_key:
            return {"url": url, "title": "", "content": "Unavailable without FIRECRAWL_API_KEY"}
        return {"url": url, "title": "", "content": ""}


class ToolDefinition:
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        executor: Any,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.executor = executor


class ToolRegistry:
    """Select and execute permitted tools for the orchestrator."""

    def __init__(self) -> None:
        self.tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self.tools[tool.name] = tool

    def available(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self.tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        result = tool.executor(arguments)
        return {"tool": name, "status": "ok", "result": result}


class WebSearchTool:
    def __init__(self, providers: Sequence[SearchProvider] | None = None) -> None:
        self.providers = list(providers or [])

    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("A query is required for web search.")
        results: list[dict[str, Any]] = []
        used_provider = "none"
        for provider in self.providers:
            provider_results = provider.search(query)
            if provider_results:
                used_provider = provider.name
                results = [
                    {
                        "title": result.title,
                        "url": result.url,
                        "snippet": result.snippet,
                        "source": result.source or provider.name,
                    }
                    for result in provider_results
                ]
                break
        return {"query": query, "provider": used_provider, "results": results}


class HistoricalConversationTool:
    def __init__(self, memory: ConversationMemory) -> None:
        self.memory = memory

    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", ""))
        limit = int(arguments.get("limit", 5))
        matches = self.memory.get_relevant(query, limit=limit)
        return {
            "query": query,
            "matches": [
                {"role": rec.role, "content": rec.content, "message_id": rec.message_id}
                for rec in matches
            ],
        }


class DeepResearchTool:
    def __init__(self, search_tool: WebSearchTool) -> None:
        self.search_tool = search_tool

    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        objective = str(arguments.get("objective", "")).strip()
        max_queries = max(1, int(arguments.get("max_queries", 3)))
        max_sources = max(1, int(arguments.get("max_sources", 5)))
        if not objective:
            raise ValueError("An objective is required for deep research.")
        queries = [objective, f"{objective} key findings", f"{objective} recent updates"][:max_queries]
        all_results: list[dict[str, Any]] = []
        for query in queries:
            results = self.search_tool({"query": query})
            for result in results.get("results", [])[:max_sources]:
                all_results.append(result)
        unique = []
        seen = set()
        for item in all_results:
            url = item.get("url")
            if url and url not in seen:
                seen.add(url)
                unique.append(item)
        report = {
            "objective": objective,
            "queries": queries,
            "sources": unique[:max_sources],
            "summary": "Research compiled from multiple searches; verify claims independently when accuracy is critical.",
        }
        return report


class AIOrchestrator:
    """Coordinates memory, search, and generation across providers."""

    def __init__(self, provider_name: str = "ollama", *, ai_service: AIService | None = None) -> None:
        self.provider_name = provider_name
        self.ai_service = ai_service or AIService(config={"default_provider": provider_name})
        self.memory = ConversationMemory()
        self.token_manager = TokenManager(model=provider_name)
        self.search_tool = WebSearchTool(
            providers=[
                BraveSearchProvider(),
                TavilySearchProvider(),
                FirecrawlSearchProvider(),
            ]
        )
        self.registry = ToolRegistry()
        self.registry.register(
            ToolDefinition(
                "web_search",
                "Search the web for current information.",
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                self.search_tool,
            )
        )
        self.registry.register(
            ToolDefinition(
                "history_retrieval",
                "Retrieve relevant earlier conversation messages.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                HistoricalConversationTool(self.memory),
            )
        )
        self.registry.register(
            ToolDefinition(
                "deep_research",
                "Run a structured research workflow across multiple searches.",
                {
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string"},
                        "max_queries": {"type": "integer", "default": 3},
                        "max_sources": {"type": "integer", "default": 5},
                    },
                    "required": ["objective"],
                },
                DeepResearchTool(self.search_tool),
            )
        )

    @staticmethod
    def classify_intent(message: str) -> str:
        lowered = message.lower()
        if any(term in lowered for term in ("latest", "today", "current", "news", "recent", "breaking", "search the web", "what happened")):
            return "web_search"
        if any(term in lowered for term in ("research", "deep dive", "investigate", "analyze the market", "compare")):
            return "deep_research"
        if any(term in lowered for term in ("history", "earlier", "before", "remember", "we discussed")):
            return "history_lookup"
        return "direct_answer"

    @staticmethod
    def requires_web_search(message: str, *, search_mode: str = "auto") -> bool:
        if search_mode == "disabled":
            return False
        if search_mode == "manual":
            return False
        lowered = message.lower()
        explicit_search = any(term in lowered for term in ("search online", "look up", "check the web", "latest", "current news", "stock price", "newest"))
        if explicit_search:
            return True
        current_topics = any(term in lowered for term in ("price", "release", "breaking", "recent", "today", "yesterday", "now"))
        return current_topics and not any(term in lowered for term in ("calculate", "explain", "rewrite", "summarize"))

    def build_context(self, message: str) -> tuple[list[dict[str, str]], str]:
        summary = self.memory.get_summary()
        recent = self.memory.get_recent(8)
        relevant = self.memory.get_relevant(message, limit=5)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        ]
        if summary:
            messages.append({"role": "system", "content": f"Conversation summary:\n{summary}"})
        for record in recent:
            messages.append({"role": record.role, "content": record.content})
        for record in relevant:
            if record not in recent:
                messages.append({"role": record.role, "content": record.content})
        messages.append({"role": "user", "content": message})
        prompt = self.token_manager.trim_context(messages, max_tokens=self.token_manager.available_tokens(messages))
        return prompt, summary

    def handle_message(
        self,
        message: str,
        *,
        conversation_id: str = "default",
        search_mode: str = "auto",
        user_id: str | int | None = None,
    ) -> dict[str, Any]:
        user_message = (message or "").strip()
        if not user_message:
            raise ValueError("A message is required for the orchestrator.")

        self.memory.add_message("user", user_message, message_id=f"{conversation_id}:{len(self.memory.get_all()) + 1}")

        intent = self.classify_intent(user_message)
        tool_results: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        if self.requires_web_search(user_message, search_mode=search_mode) or intent == "web_search":
            search_result = self.registry.execute(
                "web_search",
                {"query": user_message},
            )
            tool_results.append(search_result)
            citations.extend(
                {"title": item["title"], "url": item["url"], "source": item["source"]}
                for item in search_result["result"].get("results", [])
            )
        elif intent == "deep_research":
            research_result = self.registry.execute(
                "deep_research",
                {"objective": user_message, "max_queries": 3, "max_sources": 5},
            )
            tool_results.append(research_result)
            citations.extend(
                {"title": item["title"], "url": item["url"], "source": item["source"]}
                for item in research_result["result"].get("sources", [])
            )
        elif intent == "history_lookup":
            history_result = self.registry.execute(
                "history_retrieval",
                {"query": user_message, "limit": 5},
            )
            tool_results.append(history_result)

        context, summary = self.build_context(user_message)
        generated = self.ai_service.generate(
            message,
            system_prompt="\n".join(item["content"] for item in context if item.get("role") == "system"),
            provider_name=self.provider_name,
        )

        self.memory.add_message("assistant", generated, message_id=f"{conversation_id}:assistant:{len(self.memory.get_all()) + 1}")
        if summary:
            self.memory.update_summary(summary)

        return {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "intent": intent,
            "search_required": self.requires_web_search(user_message, search_mode=search_mode),
            "tool_results": tool_results,
            "citations": citations,
            "response": generated,
            "context_tokens": self.token_manager.estimate_tokens(json.dumps(context)),
            "memory_summary": summary,
            "provider": self.provider_name,
        }
