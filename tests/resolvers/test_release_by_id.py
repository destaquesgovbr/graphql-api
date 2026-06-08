"""Testes do resolver top-level `release(id)` (Fase 1A SSR).

Substitui o `getReleaseById` do portal (`clipping/release/[releaseId]/actions.ts`).

Autorizacao espelha `MarketplaceListing.releases`:
- PUBLICO se o listing fonte do clipping esta ativo.
- Caso contrario, somente autor ou subscriber pode ler.

Convencao de retorno (mesma do `marketplaceListing`/`clipping`): retorna
`None` quando o release nao existe OU o caller nao esta autorizado.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.datasources.firestore import (
    ClippingData,
    ReleaseData,
    SubscriptionData,
)
from graphql_api.schema.resolvers.clippings import ClippingQuery
from graphql_api.schema.resolvers.health import HealthQuery

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@strawberry.type
class _Query(HealthQuery, ClippingQuery):
    pass


test_schema = strawberry.Schema(query=_Query)


QUERY = """
    query($id: String!) {
        release(id: $id) {
            id
            clippingId
            clippingName
            digestHtml
            digestPreview
            articlesCount
            createdAt
            marketplaceListingId
            recortes {
                id
                title
                themes
                agencies
                keywords
            }
        }
    }
"""


def _clipping_with_recortes(
    clipping_id: str = "clip-1", author_user_id: str = "user-author"
) -> ClippingData:
    return ClippingData(
        id=clipping_id,
        name="Meu Clipping",
        recortes=[
            {
                "id": "rec-1",
                "title": "Saude",
                "themes": ["saude"],
                "agencies": ["ms"],
                "keywords": ["vacina"],
            }
        ],
        schedule="0 8 * * *",
        author_user_id=author_user_id,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _release(release_id: str = "rel-1", clipping_id: str = "clip-1") -> ReleaseData:
    return ReleaseData(
        id=release_id,
        clipping_id=clipping_id,
        user_id="user-author",
        clipping_name="Meu Clipping",
        digest='{"intro":"resumo do digest"}',
        digest_html="<p>conteudo</p>",
        articles_count=3,
        created_at=NOW,
        release_url=f"/clipping/release/{release_id}",
        ref_time=NOW,
        since_hours=24,
    )


def _clipping(
    clipping_id: str = "clip-1", author_user_id: str = "user-author"
) -> ClippingData:
    return ClippingData(
        id=clipping_id,
        name="Meu Clipping",
        recortes=[],
        schedule="0 8 * * *",
        author_user_id=author_user_id,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _subscription(clipping_id="clip-1", user_id="user-sub") -> SubscriptionData:
    return SubscriptionData(
        id="sub-1",
        clipping_id=clipping_id,
        user_id=user_id,
        role="subscriber",
        delivery_channels={"email": True},
        active=True,
        subscribed_at=NOW,
    )


def _ctx(ds, user_id=None) -> GraphQLContext:
    ctx = GraphQLContext(firestore_ds=ds)
    if user_id is not None:
        ctx.user = User(id=user_id, email="test@example.com")
    return ctx


class TestReleaseById:
    def test_public_active_listing_returns_release_anonymous(self):
        """Listing fonte ativo -> release publica (anonimo pode ver)."""
        ds = MagicMock()
        ds.get_release.return_value = _release()
        ds.get_clipping.return_value = _clipping_with_recortes()
        # Listing fonte ativo (get_marketplace_listing retorna dict != None).
        ds.get_marketplace_listing_for_clipping.return_value = {
            "id": "listing-1",
            "active": True,
        }
        result = test_schema.execute_sync(
            QUERY, variable_values={"id": "rel-1"}, context_value=_ctx(ds)
        )
        assert result.errors is None, f"Errors: {result.errors}"
        rel = result.data["release"]
        assert rel is not None
        assert rel["id"] == "rel-1"
        assert rel["digestPreview"] == "resumo do digest"
        # marketplaceListingId vem do listing ativo; recortes vem do clipping.
        assert rel["marketplaceListingId"] == "listing-1"
        assert len(rel["recortes"]) == 1
        assert rel["recortes"][0]["id"] == "rec-1"
        assert rel["recortes"][0]["title"] == "Saude"
        assert rel["recortes"][0]["themes"] == ["saude"]
        assert rel["recortes"][0]["agencies"] == ["ms"]
        assert rel["recortes"][0]["keywords"] == ["vacina"]

    def test_nonexistent_release_returns_none(self):
        ds = MagicMock()
        ds.get_release.return_value = None
        result = test_schema.execute_sync(
            QUERY, variable_values={"id": "missing"}, context_value=_ctx(ds)
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["release"] is None

    def test_unauthorized_returns_none(self):
        """Listing inativo + nao-autor + nao-subscriber -> None."""
        ds = MagicMock()
        ds.get_release.return_value = _release()
        ds.get_clipping.return_value = _clipping(author_user_id="user-author")
        ds.get_marketplace_listing_for_clipping.return_value = None  # inativo/ausente
        ds.get_subscription.return_value = None  # nao inscrito
        result = test_schema.execute_sync(
            QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(ds, user_id="user-outsider"),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["release"] is None

    def test_author_can_read_private_release(self):
        ds = MagicMock()
        ds.get_release.return_value = _release()
        ds.get_clipping.return_value = _clipping_with_recortes(
            author_user_id="user-author"
        )
        ds.get_marketplace_listing_for_clipping.return_value = None
        ds.get_subscription.return_value = None
        result = test_schema.execute_sync(
            QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(ds, user_id="user-author"),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        rel = result.data["release"]
        assert rel["id"] == "rel-1"
        # Sem listing ativo -> marketplaceListingId null; recortes ainda vem do clipping.
        assert rel["marketplaceListingId"] is None
        assert len(rel["recortes"]) == 1
        assert rel["recortes"][0]["title"] == "Saude"

    def test_release_sdl_exposes_recortes_and_listing_id(self):
        """O tipo Release expoe os novos campos recortes + marketplaceListingId."""
        sdl = test_schema.as_str()
        assert "type Release {" in sdl
        release_block = sdl.split("type Release {", 1)[1].split("}", 1)[0]
        assert "recortes: [Recorte!]!" in release_block
        assert "marketplaceListingId: String" in release_block

    def test_subscriber_can_read_private_release(self):
        ds = MagicMock()
        ds.get_release.return_value = _release()
        ds.get_clipping.return_value = _clipping(author_user_id="user-author")
        ds.get_marketplace_listing_for_clipping.return_value = None
        ds.get_subscription.return_value = _subscription(user_id="user-sub")
        result = test_schema.execute_sync(
            QUERY,
            variable_values={"id": "rel-1"},
            context_value=_ctx(ds, user_id="user-sub"),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["release"]["id"] == "rel-1"
