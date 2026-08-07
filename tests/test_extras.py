"""Tests for optional extras — SentenceTransformerEmbedding, OllamaProvider.

These test import behavior and error handling without requiring actual services.
"""

import pytest


# =============================================================================
# SentenceTransformerEmbedding
# =============================================================================


class TestSentenceTransformerEmbedding:
    def test_import_without_library(self):
        """Module should be importable even without sentence-transformers."""
        from soul_framework.embedding.sentence_transformer import SentenceTransformerEmbedding
        assert SentenceTransformerEmbedding is not None

    def test_raises_without_library_on_init(self):
        """If sentence-transformers isn't installed, init should raise ImportError."""
        from soul_framework.embedding import sentence_transformer as mod
        original = mod.SentenceTransformer
        try:
            mod.SentenceTransformer = None
            with pytest.raises(ImportError, match="sentence-transformers"):
                mod.SentenceTransformerEmbedding()
        finally:
            mod.SentenceTransformer = original


# =============================================================================
# OllamaProvider
# =============================================================================


class TestOllamaProvider:
    def test_import_without_httpx(self):
        """Module should be importable even without httpx."""
        from soul_framework.llm.ollama import OllamaProvider
        assert OllamaProvider is not None

    def test_raises_without_httpx_on_init(self):
        """If httpx isn't installed, init should raise ImportError."""
        from soul_framework.llm import ollama as mod
        original = mod.httpx
        try:
            mod.httpx = None
            with pytest.raises(ImportError, match="httpx"):
                mod.OllamaProvider()
        finally:
            mod.httpx = original

    def test_default_config(self):
        """OllamaProvider should have sensible defaults."""
        from soul_framework.llm import ollama as mod
        if mod.httpx is None:
            pytest.skip("httpx not installed")
        provider = mod.OllamaProvider()
        assert provider._model == "qwen2.5:7b"
        assert "11434" in provider._base_url

    def test_custom_config(self):
        """OllamaProvider should accept custom model and URL."""
        from soul_framework.llm import ollama as mod
        if mod.httpx is None:
            pytest.skip("httpx not installed")
        provider = mod.OllamaProvider(
            model="llama3:8b",
            base_url="http://10.0.0.1:11434",
            timeout=120.0,
        )
        assert provider._model == "llama3:8b"
        assert provider._base_url == "http://10.0.0.1:11434"
        assert provider._timeout == 120.0
