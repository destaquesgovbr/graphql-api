"""Testes do subfield `Clipping.releases` (Fase A5).

Cobre:
- Schema introspection: `Release` type, `Clipping.releases(limit, before)`.
- Resolver: paginacao, autorizacao (autor + subscribers), default limit.
- Integracao com o `releases_loader` (batch / no N+1).

Os testes usam o mesmo padrao dos demais resolvers: `strawberry.Schema`
sintetico com `ClippingQuery` + mock do `FirestoreDatasource`.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import strawberry

from graphql_api.context import GraphQLContext, User
from graphql_api.dataloaders import create_releases_loader, create_subscription_loader
from graphql_api.datasources.firestore import (
    ClippingData,
    ReleaseData,
    SubscriptionData,
)
from graphql_api.schema.resolvers.clippings import ClippingMutation, ClippingQuery
from graphql_api.schema.resolvers.health import HealthQuery

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@strawberry.type
class _Query(HealthQuery, ClippingQuery):
    pass


@strawberry.type
class _Mutation(ClippingMutation):
    pass


test_schema = strawberry.Schema(query=_Query, mutation=_Mutation)


def run_async(query, variable_values=None, context_value=None):
    """Helper para executar uma query assincrona pelo Strawberry.

    O resolver `Clipping.releases` e async (carrega via dataloader), entao
    nao podemos usar `execute_sync`. Rodamos o coro num novo event loop
    (cada teste cria o seu para evitar reuso entre testes).
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            test_schema.execute(
                query, variable_values=variable_values, context_value=context_value
            )
        )
    finally:
        loop.close()


def _make_release(release_id: str, clipping_id: str = "clip-1", offset_hours: int = 0) -> ReleaseData:
    return ReleaseData(
        id=release_id,
        clipping_id=clipping_id,
        user_id="user-author",
        clipping_name="Meu Clipping",
        digest='{"intro":"x"}',
        digest_html="<p>conteudo</p>",
        articles_count=3,
        created_at=NOW + timedelta(hours=offset_hours),
        release_url=f"/clipping/release/{release_id}",
        ref_time=NOW + timedelta(hours=offset_hours),
        since_hours=24,
    )


def _make_clipping_data(clipping_id: str = "clip-1", author_user_id: str = "user-author") -> ClippingData:
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


def _make_subscription(
    clipping_id: str = "clip-1",
    user_id: str = "user-author",
    role: str = "author",
) -> SubscriptionData:
    return SubscriptionData(
        id=f"sub-{clipping_id}-{user_id}",
        clipping_id=clipping_id,
        user_id=user_id,
        role=role,  # type: ignore
        delivery_channels={"email": True, "telegram": False, "push": False, "webhook": False},
        extra_emails=[],
        webhook_url="",
        active=True,
        subscribed_at=NOW,
    )


def _make_ds(
    *,
    releases_by_clipping: dict[str, list[ReleaseData]] | None = None,
    subscriptions_for_user: dict[str, dict[str, SubscriptionData]] | None = None,
) -> MagicMock:
    """Mock do FirestoreDatasource para testes de releases.

    - `releases_by_clipping`: map clipping_id -> list[ReleaseData] retornados
      por `get_releases` e `get_releases_batch`.
    - `subscriptions_for_user`: map user_id -> {clipping_id: sub}, usado pelo
      subscription_loader.
    """
    ds = MagicMock()
    releases_by_clipping = releases_by_clipping or {}

    def fake_get_releases(clipping_id, limit=20, before=None):
        rels = releases_by_clipping.get(clipping_id, [])
        if before is not None:
            rels = [r for r in rels if r.created_at and r.created_at < before]
        return rels[:limit]

    def fake_get_releases_batch(clipping_ids, limit=20, before=None):
        return {cid: fake_get_releases(cid, limit, before) for cid in clipping_ids}

    ds.get_releases.side_effect = fake_get_releases
    ds.get_releases_batch.side_effect = fake_get_releases_batch

    subscriptions_for_user = subscriptions_for_user or {}

    def fake_subs_for_user_and_clippings(user_id, clipping_ids):
        per_user = subscriptions_for_user.get(user_id, {})
        return {cid: per_user[cid] for cid in clipping_ids if cid in per_user}

    ds.get_subscriptions_for_user_and_clippings.side_effect = fake_subs_for_user_and_clippings
    return ds


def _make_ctx(ds, user_id="user-author") -> GraphQLContext:
    """Contexto com dataloaders reais sobre o ds mock."""
    ctx = GraphQLContext(firestore_ds=ds)
    # Re-injetar manualmente (o ctor cria automaticamente, mas garantimos aqui).
    ctx.subscription_loader = create_subscription_loader(ds)
    ctx.releases_loader = create_releases_loader(ds)
    if user_id is not None:
        ctx.user = User(id=user_id, email="test@example.com")
    return ctx


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------
class TestReleaseSchema:
    def test_release_type_has_digest_html_and_url(self):
        introspection = test_schema.execute_sync(
            """{
                __type(name: "Release") {
                    name
                    fields { name }
                }
            }"""
        )
        assert introspection.errors is None
        t = introspection.data["__type"]
        assert t is not None, "Release type missing from schema"
        field_names = {f["name"] for f in t["fields"]}
        # campos essenciais expostos no Release
        assert {
            "id",
            "clippingId",
            "clippingName",
            "digestHtml",
            "digestPreview",
            "articlesCount",
            "createdAt",
            "releaseUrl",
            "refTime",
            "sinceHours",
        }.issubset(field_names)

    def test_digest_preview_from_json_intro(self):
        # digest JSON com `intro` -> preview = intro[:150]
        from graphql_api.schema.types.clipping import release_from_data

        intro = "Brasil avança em " + ("x" * 200)
        data = ReleaseData.model_validate(
            {
                "id": "rel-1",
                "clippingId": "c1",
                "digest": json.dumps({"intro": intro, "outro": "ignored"}),
            }
        )
        release = release_from_data(data)
        assert release.digest_preview == intro[:150]
        assert len(release.digest_preview) == 150

    def test_digest_preview_from_non_json_falls_back_to_raw(self):
        # digest nao-JSON -> digest[:150]
        from graphql_api.schema.types.clipping import release_from_data

        raw = "texto puro sem json " + ("y" * 200)
        data = ReleaseData.model_validate(
            {"id": "rel-2", "clippingId": "c1", "digest": raw}
        )
        release = release_from_data(data)
        assert release.digest_preview == raw[:150]
        assert len(release.digest_preview) == 150

    def test_digest_preview_empty_when_no_digest(self):
        # digest vazio -> ""
        from graphql_api.schema.types.clipping import release_from_data

        data = ReleaseData.model_validate(
            {"id": "rel-3", "clippingId": "c1", "digest": ""}
        )
        release = release_from_data(data)
        assert release.digest_preview == ""

    def test_digest_preview_json_without_intro_returns_empty(self):
        # JSON valido sem `intro` -> "" (intro ausente vira "")
        from graphql_api.schema.types.clipping import release_from_data

        data = ReleaseData.model_validate(
            {
                "id": "rel-4",
                "clippingId": "c1",
                "digest": json.dumps({"outro": "sem intro"}),
            }
        )
        release = release_from_data(data)
        assert release.digest_preview == ""

    def test_clipping_has_releases_subfield_with_args(self):
        introspection = test_schema.execute_sync(
            """{
                __type(name: "Clipping") {
                    fields(includeDeprecated: false) {
                        name
                        args { name type { name kind ofType { name kind } } }
                    }
                }
            }"""
        )
        assert introspection.errors is None
        clipping_type = introspection.data["__type"]
        fields = {f["name"]: f for f in clipping_type["fields"]}
        assert "releases" in fields, "Clipping.releases missing"
        arg_names = {a["name"] for a in fields["releases"]["args"]}
        assert {"limit", "before"} == arg_names


# ---------------------------------------------------------------------------
# DeliveryChannels.webhook (Fase A5)
# ---------------------------------------------------------------------------
class TestDeliveryChannelsWebhook:
    def test_delivery_channels_has_webhook_field(self):
        introspection = test_schema.execute_sync(
            """{
                __type(name: "DeliveryChannels") {
                    name
                    fields { name type { name kind ofType { name } } }
                }
            }"""
        )
        assert introspection.errors is None
        t = introspection.data["__type"]
        field_names = {f["name"] for f in t["fields"]}
        assert "webhook" in field_names

    def test_delivery_channels_input_has_webhook_field(self):
        introspection = test_schema.execute_sync(
            """{
                __type(name: "DeliveryChannelsInput") {
                    name
                    inputFields { name }
                }
            }"""
        )
        assert introspection.errors is None
        t = introspection.data["__type"]
        field_names = {f["name"] for f in t["inputFields"]}
        assert "webhook" in field_names


# ---------------------------------------------------------------------------
# Resolver: paginacao + autorizacao
# ---------------------------------------------------------------------------
RELEASES_QUERY = """
    query($id: String!, $limit: Int, $before: DateTime) {
        clipping(id: $id) {
            id
            releases(limit: $limit, before: $before) {
                id
                clippingId
                digestHtml
                createdAt
            }
        }
    }
"""


class TestClippingReleasesResolver:
    def test_clipping_releases_subfield_returns_paginated_list(self):
        rels = [_make_release(f"rel-{i}", offset_hours=i) for i in range(3)]
        ds = _make_ds(
            releases_by_clipping={"clip-1": list(reversed(rels))},  # já desc
            subscriptions_for_user={"user-author": {"clip-1": _make_subscription()}},
        )
        ds.get_clipping.return_value = _make_clipping_data()
        result = run_async(
            RELEASES_QUERY,
            variable_values={"id": "clip-1", "limit": 10, "before": None},
            context_value=_make_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        releases = result.data["clipping"]["releases"]
        assert len(releases) == 3
        assert {r["id"] for r in releases} == {"rel-0", "rel-1", "rel-2"}

    def test_clipping_releases_default_limit_20(self):
        """`limit` default = 20 (definido na assinatura do resolver)."""
        # Geramos 25 e esperamos que o resolver passe limit=20 para o ds
        rels = [_make_release(f"rel-{i}", offset_hours=i) for i in range(25)]
        ds = _make_ds(
            releases_by_clipping={"clip-1": list(reversed(rels))},
            subscriptions_for_user={"user-author": {"clip-1": _make_subscription()}},
        )
        ds.get_clipping.return_value = _make_clipping_data()
        # Sem `limit` no GraphQL → backend usa default 20
        query = """
            query($id: String!) {
                clipping(id: $id) {
                    releases { id }
                }
            }
        """
        result = run_async(
            query,
            variable_values={"id": "clip-1"},
            context_value=_make_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert len(result.data["clipping"]["releases"]) == 20

    def test_clipping_releases_with_before_cursor(self):
        """`before` filtra createdAt < cursor."""
        rels = [_make_release(f"rel-{i}", offset_hours=i) for i in range(5)]
        ds = _make_ds(
            releases_by_clipping={"clip-1": list(reversed(rels))},
            subscriptions_for_user={"user-author": {"clip-1": _make_subscription()}},
        )
        ds.get_clipping.return_value = _make_clipping_data()
        cutoff = (NOW + timedelta(hours=3)).isoformat()
        result = run_async(
            RELEASES_QUERY,
            variable_values={"id": "clip-1", "limit": 10, "before": cutoff},
            context_value=_make_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        ids = {r["id"] for r in result.data["clipping"]["releases"]}
        # rel-0,1,2 (createdAt < cutoff), nao incluem rel-3 nem rel-4
        assert ids == {"rel-0", "rel-1", "rel-2"}

    def test_clipping_releases_empty_for_new_clipping(self):
        ds = _make_ds(
            releases_by_clipping={"clip-1": []},
            subscriptions_for_user={"user-author": {"clip-1": _make_subscription()}},
        )
        ds.get_clipping.return_value = _make_clipping_data()
        result = run_async(
            RELEASES_QUERY,
            variable_values={"id": "clip-1", "limit": 10, "before": None},
            context_value=_make_ctx(ds),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert result.data["clipping"]["releases"] == []

    def test_clipping_releases_visible_to_author(self):
        rels = [_make_release("rel-1")]
        ds = _make_ds(
            releases_by_clipping={"clip-1": rels},
            subscriptions_for_user={
                "user-author": {"clip-1": _make_subscription(role="author")}
            },
        )
        ds.get_clipping.return_value = _make_clipping_data(author_user_id="user-author")
        result = run_async(
            RELEASES_QUERY,
            variable_values={"id": "clip-1", "limit": 10, "before": None},
            context_value=_make_ctx(ds, user_id="user-author"),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert len(result.data["clipping"]["releases"]) == 1

    def test_clipping_releases_visible_to_subscriber(self):
        rels = [_make_release("rel-1")]
        ds = _make_ds(
            releases_by_clipping={"clip-1": rels},
            subscriptions_for_user={
                "user-sub": {"clip-1": _make_subscription(user_id="user-sub", role="subscriber")}
            },
        )
        ds.get_clipping.return_value = _make_clipping_data(author_user_id="user-author")
        result = run_async(
            RELEASES_QUERY,
            variable_values={"id": "clip-1", "limit": 10, "before": None},
            context_value=_make_ctx(ds, user_id="user-sub"),
        )
        assert result.errors is None, f"Errors: {result.errors}"
        assert len(result.data["clipping"]["releases"]) == 1

    def test_clipping_releases_blocked_for_outsider(self):
        """Nao-autor e nao-subscriber recebem erro `FORBIDDEN`."""
        rels = [_make_release("rel-1")]
        ds = _make_ds(
            releases_by_clipping={"clip-1": rels},
            subscriptions_for_user={},  # user-outro sem sub
        )
        ds.get_clipping.return_value = _make_clipping_data(author_user_id="user-author")
        result = run_async(
            RELEASES_QUERY,
            variable_values={"id": "clip-1", "limit": 10, "before": None},
            context_value=_make_ctx(ds, user_id="user-outsider"),
        )
        assert result.errors is not None
        # Mensagem de erro contem 'FORBIDDEN' (ou 'forbidden' lower)
        msg = str(result.errors[0].message).upper()
        assert "FORBIDDEN" in msg or "AUTOR" in msg


# ---------------------------------------------------------------------------
# Dataloader: N+1 check
# ---------------------------------------------------------------------------
class TestReleasesLoader:
    @pytest.mark.asyncio
    async def test_releases_dataloader_no_n_plus_1(self):
        """3 clippings com `releases` selecionado → 1 chamada batch ao ds."""
        ds = _make_ds(
            releases_by_clipping={
                "clip-1": [_make_release("rel-a")],
                "clip-2": [_make_release("rel-b", clipping_id="clip-2")],
                "clip-3": [_make_release("rel-c", clipping_id="clip-3")],
            }
        )
        loader = create_releases_loader(ds)
        results = await loader.load_many(
            [
                ("clip-1", 20, None),
                ("clip-2", 20, None),
                ("clip-3", 20, None),
            ]
        )
        assert len(results) == 3
        assert len(results[0]) == 1 and results[0][0].id == "rel-a"
        # 1 chamada batch (todas com mesmo (limit, before))
        assert ds.get_releases_batch.call_count == 1
