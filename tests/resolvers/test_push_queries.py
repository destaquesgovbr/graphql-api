"""Testes das queries `pushPreferences` e `pushFiltersData` (gap-fix pre-rollout).

Hoje o resolver `push.py` so tem mutations — Portal precisa LER as prefs
para mostrar a UI (`/api/push/preferences` GET) e os filtros disponiveis
(`/api/push/filters-data` GET).

Shape esperado (alinhado ao portal):
- `pushPreferences`: `{ agencies: [String!]! }`
- `pushFiltersData`: `{ themes: [Theme!]!, agencies: [Agency!]! }`

Path canonico do Firestore: `users/{userId}/pushPreferences/filters`,
campo `agencies: [str]`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.metadata import MetadataQuery
from graphql_api.schema.resolvers.push import PushMutation, PushQuery


@strawberry.type
class _Query(HealthQuery, PushQuery, MetadataQuery):
    pass


@strawberry.type
class _Mutation(PushMutation):
    pass


test_schema = strawberry.Schema(query=_Query, mutation=_Mutation)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_firestore_ds_with_prefs(agencies: list[str] | None):
    """Constroi mock do firestore_ds com chain users/{id}/pushPreferences/filters."""
    ds = MagicMock()
    ds._db = MagicMock()

    doc = MagicMock()
    if agencies is None:
        doc.exists = False
        doc.to_dict.return_value = None
    else:
        doc.exists = True
        doc.to_dict.return_value = {"agencies": agencies}

    (
        ds._db.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value
    ) = doc
    return ds


def _make_typesense_ds_with_facets():
    """Constroi mock do typesense_ds para a query pushFiltersData reutilizar
    o resolver `themes`/`agencies` de MetadataQuery."""
    ds = MagicMock()
    # facet_counts retorna 1 entrada por campo: agencies, themes
    def search_side_effect(payload):
        facet_by = payload.get("facet_by")
        if facet_by == "agency":
            return {
                "facet_counts": [
                    {
                        "field_name": "agency",
                        "counts": [
                            {"value": "Ministerio A", "count": 10},
                            {"value": "Ministerio B", "count": 5},
                        ],
                    }
                ]
            }
        if facet_by == "theme_1_level_1_label":
            return {
                "facet_counts": [
                    {
                        "field_name": "theme_1_level_1_label",
                        "counts": [
                            {"value": "Economia", "count": 10},
                            {"value": "Saude", "count": 5},
                        ],
                    }
                ]
            }
        return {"facet_counts": []}

    ds.client.collections.__getitem__.return_value.documents.search.side_effect = (
        search_side_effect
    )
    return ds


def _authenticated_context(firestore_ds, typesense_ds=None):
    ctx = GraphQLContext(firestore_ds=firestore_ds, typesense_ds=typesense_ds)
    ctx.user = User(id="user-123", email="dev@example.com")
    return ctx


def _anonymous_context(firestore_ds, typesense_ds=None):
    return GraphQLContext(firestore_ds=firestore_ds, typesense_ds=typesense_ds)


# ---------------------------------------------------------------------------
# pushPreferences
# ---------------------------------------------------------------------------
class TestPushPreferencesQuery:
    def test_push_preferences_query_returns_user_agencies(self):
        """Usuario com prefs salvas retorna lista de agencies."""
        ds = _make_firestore_ds_with_prefs(["agencia-brasil", "ministerio-saude"])
        ctx = _authenticated_context(ds)

        result = test_schema.execute_sync(
            "{ pushPreferences { agencies } }", context_value=ctx
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["pushPreferences"]["agencies"] == [
            "agencia-brasil",
            "ministerio-saude",
        ]

    def test_push_preferences_query_requires_auth(self):
        """Sem JWT -> UNAUTHENTICATED."""
        ds = _make_firestore_ds_with_prefs([])
        ctx = _anonymous_context(ds)

        result = test_schema.execute_sync(
            "{ pushPreferences { agencies } }", context_value=ctx
        )
        assert result.errors is not None
        assert "UNAUTHENTICATED" in str(result.errors[0].message)

    def test_push_preferences_query_empty_when_user_has_no_prefs(self):
        """Doc inexistente -> agencies=[]."""
        ds = _make_firestore_ds_with_prefs(None)
        ctx = _authenticated_context(ds)

        result = test_schema.execute_sync(
            "{ pushPreferences { agencies } }", context_value=ctx
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["pushPreferences"]["agencies"] == []


# ---------------------------------------------------------------------------
# pushFiltersData
# ---------------------------------------------------------------------------
class TestPushFiltersDataQuery:
    def test_push_filters_data_query_returns_themes_and_agencies(self):
        """Retorna lista de themes e agencies disponiveis para o user escolher."""
        firestore_ds = _make_firestore_ds_with_prefs([])
        typesense_ds = _make_typesense_ds_with_facets()
        ctx = _authenticated_context(firestore_ds, typesense_ds=typesense_ds)

        result = test_schema.execute_sync(
            """
            {
                pushFiltersData {
                    themes { code label }
                    agencies { code label }
                }
            }
            """,
            context_value=ctx,
        )
        assert result.errors is None, f"Errors: {result.errors}"
        data = result.data["pushFiltersData"]
        assert len(data["themes"]) == 2
        assert data["themes"][0]["code"] == "Economia"
        assert len(data["agencies"]) == 2
        assert data["agencies"][0]["code"] == "Ministerio A"
