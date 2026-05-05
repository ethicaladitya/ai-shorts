"""AI Provider abstraction layer."""
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class AIProvider(ABC):
    """Base class for AI providers."""

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str = "", temperature: float = 0.7, max_tokens: int = 2000) -> str:
        pass

    @abstractmethod
    async def test_connection(self) -> dict:
        pass


class AzureOpenAIProvider(AIProvider):
    """Azure OpenAI implementation."""

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version

        if not all([endpoint, api_key, deployment, api_version]):
            raise ValueError("Azure OpenAI configuration incomplete. Set endpoint, api_key, deployment, and api_version.")

    async def generate(self, prompt: str, system_prompt: str = "", temperature: float = 0.7, max_tokens: int = 2000) -> str:
        import httpx

        url = f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions?api-version={self.api_version}"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json={"messages": messages, "temperature": temperature, "max_tokens": max_tokens},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def test_connection(self) -> dict:
        try:
            result = await self.generate("Say 'Connection successful' in exactly two words.", max_tokens=20)
            return {"status": "ok", "message": result.strip()}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class OpenAIProvider(AIProvider):
    """Standard OpenAI fallback."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.api_key = api_key
        self.model = model
        if not api_key:
            raise ValueError("OpenAI API key not configured.")

    async def generate(self, prompt: str, system_prompt: str = "", temperature: float = 0.7, max_tokens: int = 2000) -> str:
        import httpx

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def test_connection(self) -> dict:
        try:
            result = await self.generate("Say 'Connection successful' in exactly two words.", max_tokens=20)
            return {"status": "ok", "message": result.strip()}
        except Exception as e:
            return {"status": "error", "message": str(e)}


def get_ai_provider(
    provider_name: str = "azure_openai",
    endpoint: str = "",
    api_key: str = "",
    deployment: str = "",
    api_version: str = "",
    openai_api_key: str = "",
) -> AIProvider:
    """Factory for AI providers."""
    if provider_name == "azure_openai":
        return AzureOpenAIProvider(endpoint=endpoint, api_key=api_key, deployment=deployment, api_version=api_version)
    elif provider_name == "openai":
        return OpenAIProvider(api_key=openai_api_key)
    else:
        raise ValueError(f"Unknown AI provider: {provider_name}")
