"""Tests for case search endpoints."""

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# POST /cases/search
# ---------------------------------------------------------------------------


class TestCaseSearch:
    """Tests for POST /api/v1/cases/search."""

    async def test_case_search_without_auth(self, test_client: AsyncClient):
        """Case search without authentication should return 401."""
        payload = {"query": "劳动合同纠纷", "top_k": 5}
        resp = await test_client.post("/api/v1/cases/search", json=payload)
        assert resp.status_code == 401

    async def test_case_search_returns_results(self, test_client: AsyncClient, auth_headers):
        """Case search with valid query should return a result set (possibly empty)."""
        payload = {"query": "劳动合同纠纷", "top_k": 5}
        resp = await test_client.post(
            "/api/v1/cases/search", json=payload, headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "劳动合同纠纷"
        assert "results" in data
        assert "total" in data
        assert isinstance(data["results"], list)


# ---------------------------------------------------------------------------
# GET /cases/categories
# ---------------------------------------------------------------------------


class TestCaseCategories:
    """Tests for GET /api/v1/cases/categories."""

    async def test_case_categories(self, test_client: AsyncClient, auth_headers):
        """Case categories should return available types, causes, and courts."""
        resp = await test_client.get(
            "/api/v1/cases/categories", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "case_types" in data
        assert "causes_of_action" in data
        assert "courts" in data
        assert isinstance(data["case_types"], list)
        assert isinstance(data["causes_of_action"], list)
        assert isinstance(data["courts"], list)


# ---------------------------------------------------------------------------
# GET /cases/{case_id}
# ---------------------------------------------------------------------------


class TestCaseDetail:
    """Tests for GET /api/v1/cases/{case_id}."""

    async def test_case_detail_invalid_id_returns_404(self, test_client: AsyncClient, auth_headers):
        """Requesting a non-existent case should return 404."""
        resp = await test_client.get(
            "/api/v1/cases/nonexistent-case-id-xyz", headers=auth_headers
        )
        assert resp.status_code == 404
        assert "detail" in resp.json()
