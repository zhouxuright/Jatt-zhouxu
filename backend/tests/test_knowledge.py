"""Tests for knowledge graph endpoints."""

import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock


def _mock_graph_builder():
    """Create a mock knowledge graph builder with stats and export methods."""
    builder = MagicMock()
    builder.get_stats.return_value = {
        "node_count": 0,
        "edge_count": 0,
        "law_count": 0,
        "article_count": 0,
        "case_count": 0,
        "concept_count": 0,
        "nodes_by_type": {},
        "edges_by_type": {},
    }
    builder.export_graph_data.return_value = {
        "nodes": [],
        "edges": [],
    }
    return builder


# ---------------------------------------------------------------------------
# GET /knowledge/stats
# ---------------------------------------------------------------------------


class TestKnowledgeStats:
    """Tests for GET /api/v1/knowledge/stats."""

    async def test_graph_stats(self, test_client: AsyncClient, auth_headers):
        """Graph stats endpoint should return statistics."""
        mock_builder = _mock_graph_builder()

        with patch(
            "app.api.v1.knowledge._get_graph_builder",
            return_value=mock_builder,
        ):
            resp = await test_client.get(
                "/api/v1/knowledge/stats", headers=auth_headers
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "node_count" in data
            assert "edge_count" in data
            assert "law_count" in data
            assert "article_count" in data


# ---------------------------------------------------------------------------
# GET /knowledge/export
# ---------------------------------------------------------------------------


class TestKnowledgeExport:
    """Tests for GET /api/v1/knowledge/export."""

    async def test_export_graph(self, test_client: AsyncClient, auth_headers):
        """Export endpoint should return graph data with nodes and edges."""
        mock_builder = _mock_graph_builder()

        with patch(
            "app.api.v1.knowledge._get_graph_builder",
            return_value=mock_builder,
        ):
            resp = await test_client.get(
                "/api/v1/knowledge/export", headers=auth_headers
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "nodes" in data
            assert "edges" in data
            assert isinstance(data["nodes"], list)
            assert isinstance(data["edges"], list)


# ---------------------------------------------------------------------------
# GET /knowledge/related/{article_id}  (used as a "patterns" proxy)
# ---------------------------------------------------------------------------


class TestKnowledgePatterns:
    """Tests for knowledge graph auxiliary endpoints."""

    async def test_related_articles_endpoint(self, test_client: AsyncClient, auth_headers):
        """The related articles endpoint should return a response."""
        mock_builder = _mock_graph_builder()
        mock_builder.find_related_articles.return_value = []

        with patch(
            "app.api.v1.knowledge._get_graph_builder",
            return_value=mock_builder,
        ):
            resp = await test_client.get(
                "/api/v1/knowledge/related/some-article-id",
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "article_id" in data
            assert "total_related" in data
