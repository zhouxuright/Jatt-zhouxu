"""Tests for law search endpoints."""

import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock


# ---------------------------------------------------------------------------
# Law search
# ---------------------------------------------------------------------------


class TestLawSearch:
    """Tests for POST /api/v1/law/search."""

    async def test_law_search_with_valid_query(self, test_client: AsyncClient, auth_headers):
        """Searching with a valid query should return results."""
        # Mock the LawRetrievalAgent to avoid needing real AI models
        mock_result = {
            "reranked_results": [
                {
                    "law_name": "中华人民共和国民法典",
                    "article_number": "第一百四十三条",
                    "article_content": "具备下列条件的民事法律行为有效...",
                    "relevance_score": 0.95,
                    "effective_status": "现行有效",
                    "category": "民法",
                    "publish_year": "2020",
                    "source": "semantic",
                }
            ],
            "final_output": "# 搜索结果\n\n## 中华人民共和国民法典 第一百四十三条\n...",
        }

        with patch(
            "app.api.v1.law.get_law_retrieval_agent"
        ) as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            payload = {"query": "民事法律行为有效条件", "top_k": 10}
            resp = await test_client.post(
                "/api/v1/law/search", json=payload, headers=auth_headers
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["query"] == "民事法律行为有效条件"
            assert data["total"] >= 1
            assert len(data["results"]) >= 1
            assert data["results"][0]["law_name"] == "中华人民共和国民法典"
            assert data["results"][0]["relevance_score"] == 0.95

    async def test_law_search_with_category_filter(self, test_client: AsyncClient, auth_headers):
        """Searching with a category filter should pass it through to the agent."""
        mock_result = {
            "reranked_results": [
                {
                    "law_name": "中华人民共和国刑法",
                    "article_number": "第二百三十二条",
                    "article_content": "故意杀人的，处死刑...",
                    "relevance_score": 0.88,
                    "effective_status": "现行有效",
                    "category": "刑法",
                    "publish_year": "2020",
                    "source": "keyword",
                }
            ],
            "final_output": "# 搜索结果\n...",
        }

        with patch(
            "app.api.v1.law.get_law_retrieval_agent"
        ) as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            payload = {
                "query": "故意杀人",
                "category": "刑法",
                "top_k": 5,
            }
            resp = await test_client.post(
                "/api/v1/law/search", json=payload, headers=auth_headers
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["query"] == "故意杀人"
            assert data["total"] >= 1

    async def test_law_search_without_auth(self, test_client: AsyncClient):
        """Searching without authentication should return 401."""
        payload = {"query": "劳动法", "top_k": 5}
        resp = await test_client.post("/api/v1/law/search", json=payload)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Law categories
# ---------------------------------------------------------------------------


class TestLawCategories:
    """Tests for GET /api/v1/law/categories."""

    async def test_law_categories(self, test_client: AsyncClient, auth_headers):
        """GET /categories should return available law categories."""
        resp = await test_client.get("/api/v1/law/categories", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # The endpoint returns a dict of category -> description
        assert isinstance(data, dict)
        assert len(data) > 0

    async def test_law_categories_without_auth(self, test_client: AsyncClient):
        """GET /categories without auth should return 401."""
        resp = await test_client.get("/api/v1/law/categories")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Law articles listing
# ---------------------------------------------------------------------------


class TestLawArticles:
    """Tests for GET /api/v1/law/articles."""

    async def test_list_law_articles(self, test_client: AsyncClient, auth_headers):
        """GET /articles should return articles organized by category."""
        resp = await test_client.get("/api/v1/law/articles", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        assert "total" in data
        assert data["total"] > 0
