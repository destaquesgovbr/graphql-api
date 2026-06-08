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


def test_article_type_exposes_theme_fields():
    from graphql_api.schema import schema

    sdl = schema.as_str()
    for expected in (
        "theme1Level1Code",
        "theme1Level1Label",
        "theme1Level2Code",
        "theme1Level2Label",
        "theme1Level3Code",
        "theme1Level3Label",
        "mostSpecificThemeCode",
        "mostSpecificThemeLabel",
    ):
        assert expected in sdl, f"campo {expected} ausente no schema (Article)"


def test_article_filter_exposes_theme_label_and_dedup():
    from graphql_api.schema import schema

    afilter = schema.schema_converter.type_map["ArticleFilter"].definition
    field_names = {f.name for f in afilter.fields}
    assert "theme_label" in field_names
    assert "dedup" in field_names
    # E o SDL expõe os nomes camelCase corretos.
    sdl = schema.as_str()
    assert "themeLabel" in sdl


@pytest.mark.asyncio
async def test_invalid_query_returns_error(client):
    response = await client.post("/graphql", json={
        "query": "{ nonExistentField }"
    })
    assert response.status_code == 200
    data = response.json()
    assert "errors" in data
