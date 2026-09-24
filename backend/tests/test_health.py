"""Tests for the health check endpoint."""

import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock


class TestHealth:
    """Tests for GET /health."""

    async def test_health_returns_200(self, test_client: AsyncClient):
        """Health endpoint should return HTTP 200."""
        with patch("app.services.model_registry.ModelRegistry") as mock_registry, \
             patch("app.middleware.rate_limiter.get_circuit_breaker") as mock_cb:
            mock_registry.get_stats.return_value = {}
            mock_cb.return_value = MagicMock()
            mock_cb.return_value.get_state.return_value = "closed"

            resp = await test_client.get("/health")
            assert resp.status_code == 200

    async def test_health_response_structure(self, test_client: AsyncClient):
        """Health endpoint should return expected JSON structure."""
        with patch("app.services.model_registry.ModelRegistry") as mock_registry, \
             patch("app.middleware.rate_limiter.get_circuit_breaker") as mock_cb:
            mock_registry.get_stats.return_value = {"embedding": "loaded", "reranker": "loaded"}
            mock_cb.return_value = MagicMock()
            mock_cb.return_value.get_state.return_value = "closed"

            resp = await test_client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert "app" in data
            assert "version" in data
            assert "environment" in data
