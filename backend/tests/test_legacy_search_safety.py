from app.services.ai_orchestrator import BraveSearchProvider, FirecrawlSearchProvider, WebSearchTool


def test_unconfigured_legacy_search_never_emits_placeholder_url_or_citation():
    result = WebSearchTool([BraveSearchProvider(), FirecrawlSearchProvider()])({"query": "latest news"})

    assert result["provider"] == "none"
    assert result["results"] == []


def test_unconfigured_legacy_extraction_is_empty_not_provider_error_text():
    result = FirecrawlSearchProvider().extract("https://example.org/article")

    assert result == {"url": "https://example.org/article", "title": "", "content": ""}
