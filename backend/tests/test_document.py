"""Tests for document generation endpoints."""

import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock


# ---------------------------------------------------------------------------
# GET /document/templates
# ---------------------------------------------------------------------------


class TestDocumentTemplates:
    """Tests for GET /api/v1/document/templates."""

    async def test_list_document_templates(self, test_client: AsyncClient, auth_headers):
        """Listing document templates should return a list of templates."""
        resp = await test_client.get(
            "/api/v1/document/templates", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert "total" in data
        assert isinstance(data["templates"], list)


# ---------------------------------------------------------------------------
# POST /document/generate
# ---------------------------------------------------------------------------


class TestDocumentGeneration:
    """Tests for POST /api/v1/document/generate."""

    async def test_generate_without_auth_returns_401(self, test_client: AsyncClient):
        """Document generation without authentication should return 401."""
        payload = {
            "template_type": "complaint_filing",
            "parameters": {"test": "value"},
        }
        resp = await test_client.post(
            "/api/v1/document/generate", json=payload
        )
        assert resp.status_code == 401

    async def test_generate_with_valid_input(self, test_client: AsyncClient, auth_headers):
        """Document generation with valid input should invoke the agent and return 201."""
        mock_agent_result = {
            "generated_document": "# 民事起诉状\n\n原告：张三\n\n被告：李四\n\n诉讼请求：...",
            "final_output": "# 民事起诉状\n\n原告：张三\n\n被告：李四\n",
            "missing_fields": [],
        }

        with patch(
            "app.api.v1.document.get_document_gen_agent"
        ) as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_get_agent.return_value = mock_agent

            payload = {
                "template_type": "complaint_filing",
                "parameters": {
                    "原告姓名/名称": "张三",
                    "原告住所地": "北京市",
                    "被告姓名/名称": "李四",
                    "被告住所地": "上海市",
                    "诉讼请求": "请求判令被告偿还借款",
                    "案件事实": "被告于2024年借款10万元未还",
                    "法律依据": "民法典第六百六十七条",
                },
                "language": "zh",
            }
            resp = await test_client.post(
                "/api/v1/document/generate", json=payload, headers=auth_headers
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["template_type"] == "complaint_filing"
            assert data["title"] == "民事起诉状"
            assert "content" in data
            assert "id" in data


# ---------------------------------------------------------------------------
# GET /document/history
# ---------------------------------------------------------------------------


class TestDocumentHistory:
    """Tests for GET /api/v1/document/history."""

    async def test_document_history_empty_initially(self, test_client: AsyncClient, auth_headers):
        """A fresh user should have zero documents in history."""
        resp = await test_client.get(
            "/api/v1/document/history", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["documents"] == []
