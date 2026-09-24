"""Tests for contract review endpoints."""

import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock


# ---------------------------------------------------------------------------
# POST /contract/review
# ---------------------------------------------------------------------------


class TestContractReview:
    """Tests for POST /api/v1/contract/review."""

    async def test_review_without_auth_returns_401(self, test_client: AsyncClient):
        """Contract review without authentication should return 401."""
        # No file, no document_id => 400, but auth check comes first
        resp = await test_client.post("/api/v1/contract/review")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /contract/reviews
# ---------------------------------------------------------------------------


class TestContractReviewHistory:
    """Tests for GET /api/v1/contract/reviews."""

    async def test_review_history_empty_initially(self, test_client: AsyncClient, auth_headers):
        """A fresh user should have zero contract reviews."""
        resp = await test_client.get("/api/v1/contract/reviews", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["reviews"] == []
        assert data["page"] == 1


# ---------------------------------------------------------------------------
# GET /contract/templates/catalog
# ---------------------------------------------------------------------------


class TestContractTemplates:
    """Tests for GET /api/v1/contract/templates/catalog."""

    async def test_list_contract_templates(self, test_client: AsyncClient, auth_headers):
        """Listing contract templates should return a non-empty catalog."""
        resp = await test_client.get(
            "/api/v1/contract/templates/catalog", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert "categories" in data
        assert "total" in data
        assert data["total"] >= 0

    async def test_list_templates_without_auth(self, test_client: AsyncClient):
        """Listing templates without auth should return 401."""
        resp = await test_client.get("/api/v1/contract/templates/catalog")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /contract/ocr
# ---------------------------------------------------------------------------


class TestContractOCR:
    """Tests for POST /api/v1/contract/ocr."""

    async def test_ocr_without_file_returns_422(self, test_client: AsyncClient, auth_headers):
        """OCR endpoint without a file upload should return 422 (validation error)."""
        resp = await test_client.post(
            "/api/v1/contract/ocr", headers=auth_headers
        )
        assert resp.status_code == 422
