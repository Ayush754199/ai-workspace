"""AI Service - Dynamic provider selection and management."""

from app.services.ai_provider import BaseAIProvider, GeminiProvider, OpenAIProvider, OllamaProvider


class AIService:
    """Service for AI provider selection and response generation."""

    PROVIDERS = {
        "gemini": GeminiProvider,
        "openai": OpenAIProvider,
        "ollama": OllamaProvider,
    }

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._providers: dict[str, BaseAIProvider] = {}

    def get_provider(
        self,
        provider_name: str | None = None,
        **provider_kwargs,
    ) -> BaseAIProvider:
        """Get a provider instance, selecting dynamically based on name or config.

        Args:
            provider_name: Name of the provider ("gemini", "openai", "ollama").
                         If None, uses default from config.
            **provider_kwargs: Provider-specific configuration (api_key, model, etc.).

        Returns:
            An instance of BaseAIProvider subclass.
        """
        if provider_name is None:
            provider_name = self.config.get("default_provider", "ollama")

        if provider_name not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider_name}. Available: {list(self.PROVIDERS.keys())}")

        if provider_name not in self._providers:
            ProviderClass = self.PROVIDERS[provider_name]
            # Filter kwargs to only what the provider expects, but provide default api_key if missing
            if provider_name in ("gemini", "openai"):
                if "api_key" not in provider_kwargs:
                    provider_kwargs["api_key"] = "default-test-key"
            provider_kwargs = self._filter_provider_kwargs(provider_name, provider_kwargs)
            self._providers[provider_name] = ProviderClass(**provider_kwargs)

        return self._providers[provider_name]

    def generate(
        self,
        prompt: str,
        system_prompt: str = None,
        provider_name: str | None = None,
        **provider_kwargs,
    ) -> str:
        """Generate AI response using the specified or default provider.

        Args:
            prompt: The user's prompt/question.
            system_prompt: Optional system/instruction prompt.
            provider_name: Name of the provider to use.
            **provider_kwargs: Provider-specific options.

        Returns:
            The AI-generated response text.
        """
        provider = self.get_provider(provider_name=provider_name, **provider_kwargs)
        return provider.generate_response(prompt, system_prompt)

    def _filter_provider_kwargs(self, provider_name: str, kwargs: dict) -> dict:
        """Filter provider-specific kwargs based on provider type."""
        if provider_name == "gemini":
            return {k: v for k, v in kwargs.items() if k in ("api_key", "model")}
        elif provider_name == "openai":
            return {k: v for k, v in kwargs.items() if k in ("api_key", "model")}
        elif provider_name == "ollama":
            return {k: v for k, v in kwargs.items() if k in ("base_url", "model")}
        return kwargs