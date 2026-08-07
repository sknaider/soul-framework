"""Ollama LLM provider — local inference via Ollama API.

Requires: pip install soul-framework[llm]
"""

from __future__ import annotations

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class OllamaProvider:
    """LLM provider using Ollama's HTTP API.

    Usage:
        llm = OllamaProvider(model="qwen2.5:7b")
        response = await llm.generate("Summarize this memory")
    """

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout: float = 60.0,
    ) -> None:
        if httpx is None:
            raise ImportError(
                "httpx is required for OllamaProvider. "
                "Install with: pip install soul-framework[llm]"
            )
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def generate(
        self, prompt: str, *, max_tokens: int = 500, temperature: float = 0.3
    ) -> str:
        """Generate text using Ollama."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature,
                    },
                },
            )
            response.raise_for_status()
            return response.json().get("response", "")
