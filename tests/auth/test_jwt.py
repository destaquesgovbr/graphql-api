from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from graphql_api.auth.jwt import verify_jwt
from graphql_api.context import User

JWKS_URL = "https://accounts.example.com/.well-known/jwks.json"


@pytest.fixture
def mock_jwks_fetch(jwks_dict):
    """Patch the JWKS fetching to return our test JWKS."""
    with patch("graphql_api.auth.jwt._fetch_jwks", new_callable=AsyncMock, return_value=jwks_dict) as m:
        yield m


@pytest.mark.asyncio
async def test_valid_jwt_populates_user(make_jwt, mock_jwks_fetch):
    token = make_jwt()
    user = await verify_jwt(token, JWKS_URL)

    assert isinstance(user, User)
    assert user.id == "user-123"
    assert user.email == "test@example.com"
    assert user.roles == ["reader"]


@pytest.mark.asyncio
async def test_expired_jwt_returns_none(make_jwt, mock_jwks_fetch):
    token = make_jwt(
        claims={
            "exp": datetime.now(tz=timezone.utc) - timedelta(hours=1),
            "iat": datetime.now(tz=timezone.utc) - timedelta(hours=2),
        }
    )
    result = await verify_jwt(token, JWKS_URL)
    assert result is None


@pytest.mark.asyncio
async def test_no_token_returns_none():
    result = await verify_jwt(None, JWKS_URL)
    assert result is None


@pytest.mark.asyncio
async def test_malformed_token_returns_none(mock_jwks_fetch):
    result = await verify_jwt("not.a.valid.jwt", JWKS_URL)
    assert result is None
