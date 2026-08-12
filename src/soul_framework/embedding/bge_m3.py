"""Sovereign BGE-M3 embeddings through a local Ollama endpoint."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import socket
from typing import Any
from urllib import error, parse, request

DEFAULT_BGE_M3_MODEL = "bge-m3"


class _NoRedirect(request.HTTPRedirectHandler):
    """Prevent a loopback endpoint from redirecting memory text off-host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_loopback_url(url: str) -> str:
    parsed = parse.urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or parsed.path != "/api/embed"
        or parsed.hostname is None
        or parsed.port is None
    ):
        raise ValueError(
            "BGE-M3 endpoint must be an uncredentialed loopback /api/embed URL"
        )
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, parsed.port, type=socket.SOCK_STREAM
            )
        }
    except socket.gaierror as exc:
        raise ValueError("BGE-M3 endpoint hostname cannot be resolved") from exc
    if not addresses or not all(
        ipaddress.ip_address(value).is_loopback for value in addresses
    ):
        raise ValueError("BGE-M3 endpoint must resolve only to loopback addresses")
    return url


class BgeM3Embedding:
    """Normalized 1024-dimensional BGE-M3 embeddings served on loopback."""

    def __init__(
        self,
        model: str = DEFAULT_BGE_M3_MODEL,
        *,
        url: str = "http://127.0.0.1:11434/api/embed",
        timeout: float = 60.0,
        dimensions: int = 1024,
    ) -> None:
        if timeout <= 0 or dimensions < 1:
            raise ValueError("timeout and dimensions must be positive")
        self._model = model
        self._url = _validate_loopback_url(url)
        self._timeout = timeout
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_name(self) -> str:
        return self._model

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not all(isinstance(text, str) for text in texts):
            raise TypeError("all texts must be strings")
        if not texts:
            return []
        payload = {"model": self._model, "input": [text or " " for text in texts]}
        response = await asyncio.to_thread(self._post_json, payload)
        vectors = response.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError("Ollama returned an invalid embedding batch")
        result = [[float(value) for value in vector] for vector in vectors]
        if any(len(vector) != self._dimensions for vector in result):
            raise RuntimeError("BGE-M3 returned an unexpected vector dimension")
        if any(not math.isfinite(value) for vector in result for value in vector):
            raise RuntimeError("BGE-M3 returned a non-finite embedding value")
        return result

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        http_request = request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = request.build_opener(_NoRedirect)
            with opener.open(http_request, timeout=self._timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("local BGE-M3 embedding request failed") from exc
        if not isinstance(decoded, dict):
            raise TypeError("Ollama returned an invalid embedding response")
        return decoded
