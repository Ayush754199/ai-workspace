"""AI Provider Interface - Dynamic provider selection for LLM APIs."""

from abc import ABC, abstractmethod


class BaseAIProvider(ABC):
    """Abstract base class for AI providers."""

    @abstractmethod
    def generate_response(self, prompt: str, system_prompt: str = None) -> str:
        """Generate a response to the given prompt.

        Args:
            prompt: The user's prompt/question.
            system_prompt: Optional system/instruction prompt.

        Returns:
            The AI-generated response text.
        """
        pass


class GeminiProvider(BaseAIProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str, model: str = "gemini-pro"):
        self.api_key = api_key
        self.model = model

    def generate_response(self, prompt: str, system_prompt: str = None) -> str:
        """Generate response using Google Gemini API."""
        import openai

        openai.api_key = self.api_key
        openai.api_base = "https://generativelanguage.googleapis.com/v1beta/openai/"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = openai.ChatCompletion.create(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

        return response.choices[0].message.content or ""


class OpenAIProvider(BaseAIProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str, model: str = "gpt-4"):
        self.api_key = api_key
        self.model = model

    def generate_response(self, prompt: str, system_prompt: str = None) -> str:
        """Generate response using OpenAI API."""
        import openai

        openai.api_key = self.api_key

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = openai.ChatCompletion.create(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
        )

        return response.choices[0].message.content or ""


class OllamaProvider(BaseAIProvider):
    """Local Ollama LLM provider for running local LLMs."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama2"):
        self.base_url = base_url
        self.model = model

    def generate_response(self, prompt: str, system_prompt: str = None) -> str:
        """Generate response using Ollama local LLM."""
        import requests

        url = f"{self.base_url}/api/chat"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        response = requests.post(url, json=payload)
        response.raise_for_status()

        result = response.json()
        return result.get("message", {}).get("content", "")