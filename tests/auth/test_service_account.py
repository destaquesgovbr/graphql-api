from unittest.mock import patch

import pytest

from graphql_api.auth.service_account import verify_service_account
from graphql_api.context import ServiceAccount

AUDIENCE = "https://dgb-api.example.com"


@pytest.mark.asyncio
async def test_valid_oidc_sets_service_account():
    mock_id_info = {
        "email": "my-sa@my-project.iam.gserviceaccount.com",
        "email_verified": True,
    }
    with patch("graphql_api.auth.service_account.id_token.verify_oauth2_token", return_value=mock_id_info):
        sa = await verify_service_account("valid-oidc-token", AUDIENCE)

    assert isinstance(sa, ServiceAccount)
    assert sa.email == "my-sa@my-project.iam.gserviceaccount.com"
    assert sa.is_service_account is True


@pytest.mark.asyncio
async def test_invalid_oidc_returns_none():
    with patch(
        "graphql_api.auth.service_account.id_token.verify_oauth2_token",
        side_effect=ValueError("Invalid token"),
    ):
        result = await verify_service_account("bad-token", AUDIENCE)

    assert result is None


@pytest.mark.asyncio
async def test_no_token_returns_none():
    result = await verify_service_account(None, AUDIENCE)
    assert result is None
