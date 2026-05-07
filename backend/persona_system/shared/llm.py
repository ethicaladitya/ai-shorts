"""
backend/persona_system/shared/llm.py
LLM routing: Ollama/Qwen local → Azure OpenAI gpt-4o-mini → OpenAI fallback.
Reuses existing Azure OpenAI credentials from .env.
"""
from __future__ import annotations
import logging
import httpx
from persona_system.config.settings import settings

logger = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


async def _call_ollama(prompt: str, system: str = "", temperature: float = 0.8, max_tokens: int = 1024) -> str:
    payload = {
        "model": settings.ollama_model,
        "prompt": f"{system}\n\n{prompt}" if system else prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=180.0) as c:  # local Ollama can be slow on first load
        r = await c.post(f"{settings.ollama_base_url}/api/generate", json=payload)
        r.raise_for_status()
        return r.json()["response"].strip()


async def _call_azure(prompt: str, system: str = "", temperature: float = 0.7, max_tokens: int = 512, deployment: str | None = None) -> str:
    """Use existing Azure OpenAI gpt-4o-mini — zero extra cost for bulk generation."""
    endpoint = settings.azure_openai_endpoint.rstrip("/")
    api_version = settings.azure_openai_api_version
    use_deployment = deployment or settings.azure_openai_deployment_mini
    url = f"{endpoint}/openai/deployments/{use_deployment}/chat/completions?api-version={api_version}"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            url,
            headers={"api-key": settings.azure_openai_api_key, "Content-Type": "application/json"},
            json={"messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def _call_openai(prompt: str, system: str = "", temperature: float = 0.7, max_tokens: int = 512) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            _OPENAI_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": "gpt-4o-mini", "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def generate(
    prompt: str,
    system: str = "",
    temperature: float = 0.8,
    max_tokens: int = 1024,
    force_cloud: bool = False,
    deployment: str | None = None,
) -> str:
    """
    Priority: Azure OpenAI (when AI_PROVIDER=azure_openai) → Ollama (local) → OpenAI.
    Skips Ollama entirely when AI_PROVIDER is set to azure_openai to avoid wasted latency.
    """
    import os
    ai_provider = os.environ.get("AI_PROVIDER", "azure_openai").lower()

    # Go direct to Azure when configured as primary provider
    if ai_provider == "azure_openai" or force_cloud:
        if settings.azure_openai_endpoint and settings.azure_openai_api_key:
            return await _call_azure(prompt, system, temperature, min(max_tokens, 4096), deployment=deployment)

    # Try Ollama only when it's the configured provider
    if ai_provider == "ollama" and settings.ollama_base_url:
        try:
            return await _call_ollama(prompt, system, temperature, max_tokens)
        except Exception as e:
            logger.info("Ollama unavailable (%s) — falling back to Azure OpenAI", e)
            if settings.azure_openai_endpoint and settings.azure_openai_api_key:
                return await _call_azure(prompt, system, temperature, min(max_tokens, 4096), deployment=deployment)

    if settings.openai_api_key:
        return await _call_openai(prompt, system, temperature, min(max_tokens, 4096))

    raise RuntimeError("No LLM available. Configure AZURE_OPENAI_* or OPENAI_API_KEY in .env")
