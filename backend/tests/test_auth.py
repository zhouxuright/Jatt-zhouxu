"""Tests for authentication endpoints (register, login, get current user)."""

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegister:
    """Tests for POST /api/v1/auth/register."""

    async def test_register_success(self, test_client: AsyncClient):
        """Registering a new user should return 201 with user data."""
        payload = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "SecurePass1",
            "role": "user",
        }
        resp = await test_client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["email"] == "newuser@example.com"
        assert data["role"] == "user"
        assert data["is_active"] is True
        assert "id" in data
        assert "hashed_password" not in data

    async def test_register_duplicate_username(self, test_client: AsyncClient, sample_user):
        """Registering with an existing username should return 409."""
        payload = {
            "username": "testuser",  # already exists from sample_user fixture
            "email": "different@example.com",
            "password": "AnotherPass1",
        }
        resp = await test_client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 409
        assert "already taken" in resp.json()["detail"].lower() or "already" in resp.json()["detail"].lower()

    async def test_register_duplicate_email(self, test_client: AsyncClient, sample_user):
        """Registering with an existing email should return 409."""
        payload = {
            "username": "differentuser",
            "email": "testuser@example.com",  # already exists
            "password": "AnotherPass1",
        }
        resp = await test_client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 409
        assert "already" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    """Tests for POST /api/v1/auth/login."""

    async def test_login_success(self, test_client: AsyncClient, sample_user):
        """Logging in with correct credentials should return a JWT token."""
        payload = {
            "username": "testuser",
            "password": "TestPass123",
        }
        resp = await test_client.post("/api/v1/auth/login", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "access_token" in data["token"]
        assert data["user"]["username"] == "testuser"

    async def test_login_wrong_password(self, test_client: AsyncClient, sample_user):
        """Logging in with wrong password should return 401."""
        payload = {
            "username": "testuser",
            "password": "WrongPassword1",
        }
        resp = await test_client.post("/api/v1/auth/login", json=payload)
        assert resp.status_code == 401
        assert "detail" in resp.json()

    async def test_login_nonexistent_user(self, test_client: AsyncClient):
        """Logging in with a non-existent user should return 401."""
        payload = {
            "username": "ghost",
            "password": "SomePass123",
        }
        resp = await test_client.post("/api/v1/auth/login", json=payload)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Get current user (/me)
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    """Tests for GET /api/v1/auth/me."""

    async def test_get_me_with_valid_token(self, test_client: AsyncClient, auth_headers):
        """GET /me with valid Bearer token should return the user profile."""
        resp = await test_client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"
        assert data["email"] == "testuser@example.com"

    async def test_get_me_without_token(self, test_client: AsyncClient):
        """GET /me without any token should return 401."""
        resp = await test_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_get_me_with_invalid_token(self, test_client: AsyncClient):
        """GET /me with a bogus token should return 401."""
        headers = {"Authorization": "Bearer invalid.token.here"}
        resp = await test_client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401
