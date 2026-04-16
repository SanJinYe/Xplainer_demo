"""LLM client implementations for explanation generation."""

import json
from typing import Any, AsyncIterator, Optional

import httpx

from tailevents.explanation.exceptions import (
    LLMClientError,
    UnsupportedLLMBackendError,
)


DEFAULT_TIMEOUT_SECONDS = 30.0
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class OllamaLLMClient:
    """Async client for the local Ollama backend."""

    def __init__(self, base_url: str, model: str, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        payload = {
            "model": self._model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                trust_env=True,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise LLMClientError(
                f"Ollama request failed with status {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise LLMClientError(f"Ollama request failed: {error}") from error

        data = response.json()
        content = data.get("response")
        if not content:
            raise LLMClientError("Ollama returned an empty response")
        return str(content).strip()

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        saw_content = False
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                trust_env=True,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/generate",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError as error:
                            raise LLMClientError("Ollama returned malformed streaming JSON") from error
                        chunk = str(data.get("response", ""))
                        if chunk:
                            saw_content = True
                            yield chunk
        except httpx.HTTPStatusError as error:
            raise LLMClientError(
                f"Ollama request failed with status {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise LLMClientError(f"Ollama request failed: {error}") from error

        if not saw_content:
            raise LLMClientError("Ollama returned an empty response")


class ClaudeLLMClient:
    """Async client for the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        proxy_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._api_key = api_key
        self._model = model
        self._proxy_url = proxy_url
        self._timeout = timeout

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                proxy=self._proxy_url,
                trust_env=True,
            ) as client:
                response = await client.post(
                    ANTHROPIC_API_URL,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise LLMClientError(
                f"Claude request failed with status {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise LLMClientError(f"Claude request failed: {error}") from error

        data = response.json()
        blocks = data.get("content", [])
        text_blocks = [
            str(block.get("text", "")).strip()
            for block in blocks
            if block.get("type") == "text"
        ]
        content = "\n".join(block for block in text_blocks if block).strip()
        if not content:
            raise LLMClientError("Claude returned an empty response")
        return content

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "stream": True,
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
        }

        saw_content = False
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                proxy=self._proxy_url,
                trust_env=True,
            ) as client:
                async with client.stream(
                    "POST",
                    ANTHROPIC_API_URL,
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for _event_name, payload_text in _iter_sse_events(response):
                        if payload_text == "[DONE]":
                            break
                        try:
                            data = json.loads(payload_text)
                        except json.JSONDecodeError as error:
                            raise LLMClientError("Claude returned malformed streaming JSON") from error
                        if data.get("type") != "content_block_delta":
                            continue
                        delta = data.get("delta", {})
                        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
                            continue
                        chunk = str(delta.get("text", ""))
                        if chunk:
                            saw_content = True
                            yield chunk
        except httpx.HTTPStatusError as error:
            raise LLMClientError(
                f"Claude request failed with status {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise LLMClientError(f"Claude request failed: {error}") from error

        if not saw_content:
            raise LLMClientError("Claude returned an empty response")


class OpenRouterLLMClient:
    """Async client for the OpenRouter Chat Completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        proxy_url: Optional[str] = None,
        site_url: Optional[str] = None,
        app_name: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._proxy_url = proxy_url
        self._site_url = site_url
        self._app_name = app_name
        self._timeout = timeout

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name:
            headers["X-Title"] = self._app_name

        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                proxy=self._proxy_url,
                trust_env=True,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise LLMClientError(
                f"OpenRouter request failed with status {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise LLMClientError(f"OpenRouter request failed: {error}") from error

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise LLMClientError("OpenRouter returned an empty response")

        message = choices[0].get("message", {})
        content = self._extract_message_content(message.get("content"))
        if not content:
            raise LLMClientError("OpenRouter returned an empty response")
        return content

    async def stream_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name:
            headers["X-Title"] = self._app_name

        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        saw_content = False
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                proxy=self._proxy_url,
                trust_env=True,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for _event_name, payload_text in _iter_sse_events(response):
                        if payload_text == "[DONE]":
                            break
                        try:
                            data = json.loads(payload_text)
                        except json.JSONDecodeError as error:
                            raise LLMClientError(
                                "OpenRouter returned malformed streaming JSON"
                            ) from error
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if not isinstance(delta, dict):
                            continue
                        chunk = self._extract_delta_content(delta.get("content"))
                        if chunk:
                            saw_content = True
                            yield chunk
        except httpx.HTTPStatusError as error:
            raise LLMClientError(
                f"OpenRouter request failed with status {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise LLMClientError(f"OpenRouter request failed: {error}") from error

        if not saw_content:
            raise LLMClientError("OpenRouter returned an empty response")

    def _extract_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", "")).strip()
                    if text:
                        text_parts.append(text)
            return "\n".join(text_parts).strip()
        return ""

    def _extract_delta_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", ""))
                    if text:
                        text_parts.append(text)
            return "".join(text_parts)
        return ""


class LLMClientFactory:
    """Create an LLM client from settings-like objects."""

    @staticmethod
    def create(settings: Any):
        backend = str(LLMClientFactory._get_value(settings, "llm_backend", "ollama")).lower()

        if backend == "ollama":
            return OllamaLLMClient(
                base_url=str(
                    LLMClientFactory._get_value(settings, "ollama_base_url", "")
                ),
                model=str(LLMClientFactory._get_value(settings, "ollama_model", "")),
            )

        if backend == "claude":
            api_key = LLMClientFactory._get_value(settings, "claude_api_key")
            if not api_key:
                raise UnsupportedLLMBackendError(
                    "Claude backend requires TAILEVENTS_CLAUDE_API_KEY"
                )
            return ClaudeLLMClient(
                api_key=str(api_key),
                model=str(
                    LLMClientFactory._get_value(settings, "claude_model", "")
                ),
                proxy_url=LLMClientFactory._get_value(settings, "proxy_url"),
            )

        if backend == "openrouter":
            api_key = LLMClientFactory._get_value(settings, "openrouter_api_key")
            if not api_key:
                raise UnsupportedLLMBackendError(
                    "OpenRouter backend requires TAILEVENTS_OPENROUTER_API_KEY"
                )
            model = str(
                LLMClientFactory._get_value(settings, "openrouter_model", "")
            ).strip()
            if not model:
                raise UnsupportedLLMBackendError(
                    "OpenRouter backend requires TAILEVENTS_OPENROUTER_MODEL"
                )
            return OpenRouterLLMClient(
                api_key=str(api_key),
                model=model,
                base_url=str(
                    LLMClientFactory._get_value(
                        settings,
                        "openrouter_base_url",
                        "",
                    )
                ),
                proxy_url=LLMClientFactory._get_value(settings, "proxy_url"),
                site_url=LLMClientFactory._get_value(settings, "openrouter_site_url"),
                app_name=LLMClientFactory._get_value(settings, "openrouter_app_name"),
            )

        raise UnsupportedLLMBackendError(f"Unsupported LLM backend: {backend}")

    @staticmethod
    def _get_value(settings: Any, key: str, default: Optional[Any] = None) -> Any:
        if isinstance(settings, dict):
            return settings.get(key, default)
        return getattr(settings, key, default)


__all__ = [
    "ClaudeLLMClient",
    "LLMClientFactory",
    "OllamaLLMClient",
    "OpenRouterLLMClient",
]


async def _iter_sse_events(
    response: httpx.Response,
) -> AsyncIterator[tuple[Optional[str], str]]:
    event_name: Optional[str] = None
    data_lines: list[str] = []

    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield (event_name, "\n".join(data_lines))
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or None
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if data_lines:
        yield (event_name, "\n".join(data_lines))
