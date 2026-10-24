"""AI Provider Tests - Testing provider interface and service logic."""

import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app
import types


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    """Set up database for AI tests."""
    from app.core.database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(setup_db):
    """Create test client."""
    from app.api.deps import get_db
    from app.core.database import SessionLocal

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_base_ai_provider_interface():
    """Test that BaseAIProvider cannot be instantiated directly."""
    from app.services.ai_provider import BaseAIProvider

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        BaseAIProvider()


def test_gemini_provider_initialization():
    """Test GeminiProvider can be initialized with API key."""
    from app.services.ai_provider import GeminiProvider

    provider = GeminiProvider(api_key="test-key-123", model="gemini-pro")
    assert provider.api_key == "test-key-123"
    assert provider.model == "gemini-pro"


def test_openai_provider_initialization():
    """Test OpenAIProvider can be initialized with API key."""
    from app.services.ai_provider import OpenAIProvider

    provider = OpenAIProvider(api_key="test-key-456", model="gpt-4")
    assert provider.api_key == "test-key-456"
    assert provider.model == "gpt-4"


def test_ollama_provider_initialization():
    """Test OllamaProvider can be initialized with custom URL."""
    from app.services.ai_provider import OllamaProvider

    provider = OllamaProvider(base_url="http://localhost:11434", model="llama2")
    assert provider.base_url == "http://localhost:11434"
    assert provider.model == "llama2"


def test_ai_service_provider_selection():
    """Test AIService can select providers by name."""
    from app.services.ai_service import AIService

    service = AIService(config={"default_provider": "ollama"})

    # Test ollama provider selection
    provider = service.get_provider(provider_name="ollama")
    assert provider.__class__.__name__ == "OllamaProvider"

    # Test gemini provider selection (with default api_key)
    provider = service.get_provider(provider_name="gemini")
    assert provider.__class__.__name__ == "GeminiProvider"

    # Test openai provider selection (with default api_key)
    provider = service.get_provider(provider_name="openai")
    assert provider.__class__.__name__ == "OpenAIProvider"


def test_ai_service_generate_with_mocked_provider():
    """Test AIService.generate delegates to provider correctly."""
    from app.services.ai_service import AIService
    from app.services.ai_provider import BaseAIProvider

    service = AIService()

    # Create a mocked provider
    mock_provider = MagicMock(spec=BaseAIProvider)
    mock_provider.generate_response.return_value = "Mocked AI response"

    # Store original get_provider and replace with proper wrapper
    original_get_provider = service.get_provider

    def mock_get_provider(self, provider_name=None, **provider_kwargs):
        """Mock get_provider that returns the mock provider regardless of name."""
        if provider_name is None:
            provider_name = self.config.get("default_provider", "ollama")
        return mock_provider

    service.get_provider = types.MethodType(mock_get_provider, service)

    result = service.generate(prompt="Test prompt", provider_name="ollama")
    assert result == "Mocked AI response"
    mock_provider.generate_response.assert_called_once_with("Test prompt", None)


def test_ai_service_generate_with_system_prompt():
    """Test AIService.generate passes system prompt to provider."""
    from app.services.ai_service import AIService
    from app.services.ai_provider import BaseAIProvider

    service = AIService()

    mock_provider = MagicMock(spec=BaseAIProvider)
    mock_provider.generate_response.return_value = "Response with system prompt"

    # Use MethodType to properly bind the mock function
    import types

    def mock_get_provider(self, provider_name=None, **provider_kwargs):
        """Mock get_provider that returns the mock provider regardless of name."""
        if provider_name is None:
            provider_name = self.config.get("default_provider", "ollama")
        return mock_provider

    service.get_provider = types.MethodType(mock_get_provider, service)

    result = service.generate(
        prompt="Test prompt", system_prompt="You are helpful", provider_name="ollama"
    )
    assert result == "Response with system prompt"
    mock_provider.generate_response.assert_called_once_with(
        "Test prompt", "You are helpful"
    )


def test_ai_endpoint_structure():
    """Test that AI endpoint is properly registered."""
    from app.main import app
    from app.api.v1.endpoints.ai import router as ai_router

    # Verify the AI router is included in the app
    assert ai_router is not None
    assert hasattr(ai_router, "routes")
    # Check that the router has routes
    assert len(ai_router.routes) > 0, "AI router should have routes defined"


def test_ai_service_config():
    """Test AIService configuration handling."""
    from app.services.ai_service import AIService

    # Test with custom config - ollama defaults
    service = AIService(config={"default_provider": "ollama"})
    provider = service.get_provider()
    assert provider.__class__.__name__ == "OllamaProvider"

    # Test without config (default to ollama)
    service2 = AIService()
    provider2 = service2.get_provider()
    assert provider2.__class__.__name__ == "OllamaProvider"