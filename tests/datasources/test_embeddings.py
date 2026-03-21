from unittest.mock import AsyncMock, patch

import httpx
import pytest

from graphql_api.datasources.embeddings import EmbeddingsDatasource


@pytest.fixture
def datasource():
    return EmbeddingsDatasource(api_url="http://test-embeddings/embeddings")


async def test_generate_embedding_returns_vector(datasource):
    mock_vector = [0.1, 0.2, 0.3, 0.4, 0.5]
    mock_response = httpx.Response(
        200,
        json={"embeddings": [mock_vector]},
        request=httpx.Request("POST", datasource.api_url),
    )

    with patch("graphql_api.datasources.embeddings.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await datasource.generate_embedding("test text")

    assert result == mock_vector
    assert isinstance(result, list)
    assert all(isinstance(v, float) for v in result)


async def test_generate_embedding_api_unavailable_returns_none(datasource):
    with patch("graphql_api.datasources.embeddings.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await datasource.generate_embedding("test text")

    assert result is None


async def test_generate_embedding_sends_correct_payload(datasource):
    mock_response = httpx.Response(
        200,
        json={"embeddings": [[0.1, 0.2]]},
        request=httpx.Request("POST", datasource.api_url),
    )

    with patch("graphql_api.datasources.embeddings.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await datasource.generate_embedding("hello world")

    mock_client.post.assert_called_once_with(
        datasource.api_url,
        json={"texts": ["hello world"]},
    )
