import difflib
import importlib.util
from pathlib import Path

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


def test_article_exposes_features_field():
    from graphql_api.schema import schema

    sdl = schema.as_str()
    # Campo lazy de features no Article (aditivo, drift-safe).
    assert "features: ArticleFeatures" in sdl
    assert "type ArticleFeatures {" in sdl
    assert "type EntityType {" in sdl
    entity_block = sdl.split("type EntityType {", 1)[1].split("}", 1)[0]
    assert "text: String!" in entity_block
    assert "type: String!" in entity_block
    assert "count: Int!" in entity_block
    # Fase 4: canonicalId/salience aditivos (nullable, drift-safe).
    assert "canonicalId: String" in entity_block
    assert "salience: Float" in entity_block
    feats_block = sdl.split("type ArticleFeatures {", 1)[1].split("}", 1)[0]
    assert "entities: [EntityType!]!" in feats_block
    assert "trendingScore: Float" in feats_block
    assert "viewCount: Int" in feats_block
    assert "wordCount: Int" in feats_block
    # Fase 5: contentAnnotations (lente semântica).
    assert "contentAnnotations: [ContentAnnotation!]!" in feats_block


def test_content_annotation_type_exposed():
    from graphql_api.schema import schema

    sdl = schema.as_str()
    assert "type ContentAnnotation {" in sdl
    block = sdl.split("type ContentAnnotation {", 1)[1].split("}", 1)[0]
    assert "start: Int!" in block
    assert "end: Int!" in block
    assert "type: String!" in block
    assert "text: String!" in block
    assert "canonicalId: String" in block


def test_entity_node_type_and_query_exposed():
    from graphql_api.schema import schema

    sdl = schema.as_str()
    assert "type EntityNode {" in sdl
    block = sdl.split("type EntityNode {", 1)[1].split("}", 1)[0]
    assert "entityId: String!" in block
    assert "canonicalName: String" in block
    assert "aliases: [String!]!" in block
    assert "wikidataId: String" in block
    assert "agencyKey: String" in block
    assert "entity(id: String!): EntityNode" in sdl


def test_article_filter_exposes_entities_and_sentiment():
    from graphql_api.schema import schema

    afilter = schema.schema_converter.type_map["ArticleFilter"].definition
    field_names = {f.name for f in afilter.fields}
    assert "entities" in field_names
    assert "sentiment" in field_names
    # Fase 4: filtro por entidade canônica.
    assert "entity_canonical" in field_names
    assert "entityCanonical" in schema.as_str()


def test_article_sort_enum_and_search_sort_arg():
    from graphql_api.schema import schema

    sdl = schema.as_str()
    assert "enum ArticleSort {" in sdl
    for value in ("RELEVANCE", "DATE", "TRENDING", "VIEWS"):
        assert value in sdl, f"valor {value} ausente em ArticleSort"
    # Os resolvers de listagem/busca aceitam o argumento `sort`.
    assert "sort: ArticleSort" in sdl


@pytest.mark.asyncio
async def test_invalid_query_returns_error(client):
    response = await client.post("/graphql", json={
        "query": "{ nonExistentField }"
    })
    assert response.status_code == 200
    data = response.json()
    assert "errors" in data


# --- Drift do SDL de referência (docs/reference/) --------------------------------
#
# `docs/reference/schema.graphql` é o contrato que os consumidores (gobus-mcp,
# data-platform) usam como snapshot; ficou meses desatualizado sem ninguém notar.
# A comparação normaliza os dois lados com graphql-core (ordem lexicográfica e
# formatação canônica) para não quebrar quando o CI instala outra versão do
# strawberry com printer ligeiramente diferente: falha só em mudança real de
# tipos, campos, argumentos, defaults ou descrições.

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFERENCE_DIR = _REPO_ROOT / "docs" / "reference"


def _load_export_script():
    spec = importlib.util.spec_from_file_location("export_schema", _REPO_ROOT / "scripts" / "export_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_sdl(sdl: str) -> str:
    from graphql import build_schema, lexicographic_sort_schema, print_schema

    return print_schema(lexicographic_sort_schema(build_schema(sdl)))


def test_sdl_de_referencia_em_sincronia_com_o_codigo():
    exported = _normalize_sdl(_load_export_script().render_sdl())
    committed = _normalize_sdl((_REFERENCE_DIR / "schema.graphql").read_text(encoding="utf-8"))
    if committed != exported:
        diff = "\n".join(
            difflib.unified_diff(
                committed.splitlines(), exported.splitlines(), "commitado", "exportado", lineterm="", n=1
            )
        )
        pytest.fail(
            "docs/reference/schema.graphql está desatualizado em relação ao schema Strawberry. "
            f"Rode `make docs-schema` e commite o resultado.\n{diff[:4000]}"
        )


def test_schema_md_embrulha_o_sdl_commitado():
    # schema.md e schema.graphql saem do mesmo export: regenerar só um deles é drift.
    committed_sdl = (_REFERENCE_DIR / "schema.graphql").read_text(encoding="utf-8")
    committed_md = (_REFERENCE_DIR / "schema.md").read_text(encoding="utf-8")
    assert committed_md == _load_export_script().render_markdown(committed_sdl), (
        "docs/reference/schema.md não corresponde a schema.graphql. Rode `make docs-schema`."
    )
