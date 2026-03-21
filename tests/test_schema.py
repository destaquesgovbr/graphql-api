import pytest


@pytest.mark.asyncio
async def test_introspection_query(client):
    response = await client.post("/graphql", json={
        "query": "{ __schema { queryType { name } } }"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["__schema"]["queryType"]["name"] == "Query"


@pytest.mark.asyncio
async def test_ping_query(client):
    response = await client.post("/graphql", json={
        "query": "{ ping }"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["ping"] == "pong"


@pytest.mark.asyncio
async def test_invalid_query_returns_error(client):
    response = await client.post("/graphql", json={
        "query": "{ nonExistentField }"
    })
    assert response.status_code == 200
    data = response.json()
    assert "errors" in data
