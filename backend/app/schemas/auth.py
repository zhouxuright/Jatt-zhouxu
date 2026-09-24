"""Authentication schemas -- request/response models for auth endpoints."""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    """Schema for user registration."""
    username: str = Field(..., min_length=2, max_length=64, description="Unique username")
    email: str = Field(..., description="Valid email address")
    password: str = Field(..., min_length=8, max_length=128, description="Plain-text password")
    role: str = Field(default="user", description="User role: user, admin, lawyer")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Password must be at least 8 characters and contain both a letter and a digit."""
        if not re.search(r"[a-zA-Z]", v):
            raise ValueError("密码必须包含至少一个字母")
        if not re.search(r"[0-9]", v):
            raise ValueError("密码必须包含至少一个数字")
        return v


class UserLogin(BaseModel):
    """Schema for user login."""
    username: str = Field(..., description="Username or email")
    password: str = Field(..., description="Plain-text password")


class RefreshTokenRequest(BaseModel):
    """Schema for refresh token request."""
    refresh_token: str = Field(..., description="Refresh token to validate")


class RefreshTokenResponse(BaseModel):
    """Schema for refresh token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until access token expiration")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class Token(BaseModel):
    """JWT token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until token expiration")


class UserResponse(BaseModel):
    """Public user profile returned by the API."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LoginResponse(BaseModel):
    """Combined token + user response from login."""
    token: Token
    user: UserResponse

    @property
    def access_token(self) -> str:
        """Shortcut to access_token for frontend compatibility."""
        return self.token.access_token


# ---------------------------------------------------------------------------
# Account deletion schemas
# ---------------------------------------------------------------------------

class AccountDeleteRequest(BaseModel):
    """Request body for account deletion -- requires password confirmation."""
    password: str = Field(..., description="Current password for confirmation")


class AccountDeleteResponse(BaseModel):
    """Response confirming account deletion with counts of removed data."""
    message: str = Field(
        ...,
        description="Deletion confirmation message",
    )
    deleted_at: datetime = Field(
        ...,
        description="Timestamp when the account was soft-deleted",
    )
    grace_period_days: int = Field(
        ...,
        description="Number of days before permanent deletion (account can be restored within this period)",
    )
    data_counts: dict[str, int] = Field(
        ...,
        description="Counts of deleted data by type (e.g. conversations, messages, feedbacks)",
    )
