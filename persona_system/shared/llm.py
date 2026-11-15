"""
persona_system/shared/llm.py
Unified LLM client — prefers local Ollama/Qwen, falls back to OpenAI gpt-4o-mini.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from persona_system.config.settings import settings

logger = logging.getLogger(__name__)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


async def _call_ollama(prompt: str, system: str = "", temperature: float = 0.8, max_tokens: int = 1024) -> str:
    payload = {
        "model": settings.ollama_model,
        "prompt": f"{system}\n\n{prompt}" if system else prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{settings.ollama_base_url}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"].strip()


async def _call_openai(prompt: str, system: str = "", temperature: float = 0.7, max_tokens: int = 512) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(_OPENAI_CHAT_URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def generate(
    prompt: str,
    system: str = "",
    temperature: float = 0.8,
    max_tokens: int = 1024,
    force_openai: bool = False,
) -> str:
    """
    Route to local Ollama first; fall back to OpenAI if Ollama is unavailable
    or force_openai=True is passed.
    """
    if force_openai or not settings.ollama_base_url:
        return await _call_openai(prompt, system, temperature, min(max_tokens, 2048))

    try:
        return await _call_ollama(prompt, system, temperature, max_tokens)
    except Exception as e:
        logger.warning("Ollama unavailable (%s) — falling back to OpenAI gpt-4o-mini", e)
        if settings.openai_api_key:
            return await _call_openai(prompt, system, temperature, min(max_tokens, 2048))
        raise RuntimeError("No LLM available. Start Ollama or set OPENAI_API_KEY.") from e
