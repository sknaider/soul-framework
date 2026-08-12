from __future__ import annotations

import asyncio
import math

import pytest

from soul_framework.embedding.bge_m3 import DEFAULT_BGE_M3_MODEL, BgeM3Embedding


def test_bge_m3_defaults_are_local_and_1024_dimensions():
    provider = BgeM3Embedding()
    assert provider.model_name == DEFAULT_BGE_M3_MODEL
    assert provider.dimensions == 1024


async def test_embed_batch_uses_one_bounded_local_request(monkeypatch):
    provider = BgeM3Embedding(dimensions=3)
    seen = {}

    def fake_post(payload):
        seen.update(payload)
        return {"embeddings": [[1, 2, 3], [4, 5, 6]]}

    monkeypatch.setattr(provider, "_post_json", fake_post)
    assert await provider.embed_batch(["uno", "dos"]) == [
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
    ]
    assert seen == {"model": "bge-m3", "input": ["uno", "dos"]}


async def test_embedding_io_is_offloaded_from_event_loop(monkeypatch):
    provider = BgeM3Embedding(dimensions=2)
    monkeypatch.setattr(
        provider, "_post_json", lambda payload: {"embeddings": [[1, 2]]}
    )
    called = False
    original = asyncio.to_thread

    async def observed(function, *args, **kwargs):
        nonlocal called
        called = True
        return await original(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", observed)
    assert await provider.embed("hola") == [1.0, 2.0]
    assert called


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/embed",
        "http://10.0.0.2:11434/api/embed",
        "http://localhost:80@evil.example/api/embed",
        "http://127.0.0.1:11434@evil.example/api/embed",
        "http://127.0.0.1:11434/api/embed?next=https://evil.example",
    ],
)
def test_remote_endpoint_is_rejected(url):
    with pytest.raises(ValueError, match="loopback"):
        BgeM3Embedding(url=url)


async def test_bad_batch_or_dimension_fails_closed(monkeypatch):
    provider = BgeM3Embedding(dimensions=2)
    monkeypatch.setattr(provider, "_post_json", lambda payload: {"embeddings": []})
    with pytest.raises(RuntimeError, match="invalid embedding batch"):
        await provider.embed("hola")
    monkeypatch.setattr(provider, "_post_json", lambda payload: {"embeddings": [[1]]})
    with pytest.raises(RuntimeError, match="unexpected vector dimension"):
        await provider.embed("hola")
    monkeypatch.setattr(
        provider, "_post_json", lambda payload: {"embeddings": [[math.nan, 1.0]]}
    )
    with pytest.raises(RuntimeError, match="non-finite"):
        await provider.embed("hola")
