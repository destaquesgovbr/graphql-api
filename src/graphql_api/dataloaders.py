from datetime import datetime
from typing import Any, Optional

from strawberry.dataloader import DataLoader

from graphql_api.datasources.firestore import ReleaseData, SubscriptionData
from graphql_api.schema.types.theme import Agency, Theme


async def _batch_load_themes(keys: list[str], typesense_ds: Any) -> list[Theme]:
    """Batch load themes via a single Typesense facet query."""
    response = typesense_ds.collections["articles"].documents.search(
        {
            "q": "*",
            "per_page": 0,
            "facet_by": "theme_1_level_1_label",
            "max_facet_values": 250,
        }
    )
    facet_map: dict[str, str] = {}
    for fc in response.get("facet_counts", []):
        if fc["field_name"] == "theme_1_level_1_label":
            for val in fc["counts"]:
                facet_map[val["value"]] = val["value"]

    return [Theme(code=key, label=facet_map.get(key, key)) for key in keys]


async def _batch_load_agencies(keys: list[str], typesense_ds: Any) -> list[Agency]:
    """Batch load agencies via a single Typesense facet query."""
    response = typesense_ds.collections["articles"].documents.search(
        {
            "q": "*",
            "per_page": 0,
            "facet_by": "agency",
            "max_facet_values": 250,
        }
    )
    facet_map: dict[str, str] = {}
    for fc in response.get("facet_counts", []):
        if fc["field_name"] == "agency":
            for val in fc["counts"]:
                facet_map[val["value"]] = val["value"]

    return [Agency(code=key, label=facet_map.get(key, key)) for key in keys]


def create_theme_loader(typesense_ds: Any) -> DataLoader:
    """Create a Strawberry DataLoader for themes."""

    async def load_fn(keys: list[str]) -> list[Theme]:
        return await _batch_load_themes(keys, typesense_ds)

    return DataLoader(load_fn=load_fn)


def create_agency_loader(typesense_ds: Any) -> DataLoader:
    """Create a Strawberry DataLoader for agencies."""

    async def load_fn(keys: list[str]) -> list[Agency]:
        return await _batch_load_agencies(keys, typesense_ds)

    return DataLoader(load_fn=load_fn)


# ---------------------------------------------------------------------------
# Subscription dataloader (Fase A3)
# ---------------------------------------------------------------------------
def create_features_loader(postgres_ds: Any) -> DataLoader:
    """DataLoader para `Article.features` (Fase 1).

    Batch por `unique_id`: uma única query a `news_features` por request, mesmo
    quando a tela renderiza o artigo + cards relacionados todos pedindo
    `features`. Retorna o dict de features (ou None) por key, alinhado por
    posição.
    """

    async def load_fn(keys: list[str]) -> list[Optional[dict]]:
        by_id = await postgres_ds.get_features_batch(list(keys))
        return [by_id.get(k) for k in keys]

    return DataLoader(load_fn=load_fn)


def create_subscription_loader(firestore_ds: Any) -> DataLoader:
    """DataLoader para `Clipping.mySubscription`.

    Keys são tuplas `(user_id, clipping_id)`. O loader agrupa por `user_id`
    (em geral todos do mesmo session user) e faz 1 query Firestore
    `subscriptions where userId == X`, evitando N+1 quando a UI lista vários
    clippings com `mySubscription` selecionado.

    Retorna `Optional[SubscriptionData]` para cada key, alinhado por posição.
    """

    async def load_fn(
        keys: list[tuple[str, str]],
    ) -> list[Optional[SubscriptionData]]:
        # Agrupa clippings por user_id (suporta caso edge de keys com user
        # diferentes — defensivo; em prática session user é sempre o mesmo).
        by_user: dict[str, list[str]] = {}
        for user_id, clipping_id in keys:
            by_user.setdefault(user_id, []).append(clipping_id)

        # Query Firestore por user_id (1 chamada por user).
        per_user_map: dict[str, dict[str, SubscriptionData]] = {}
        for user_id, clip_ids in by_user.items():
            per_user_map[user_id] = firestore_ds.get_subscriptions_for_user_and_clippings(
                user_id=user_id, clipping_ids=clip_ids
            )

        return [per_user_map.get(uid, {}).get(cid) for uid, cid in keys]

    return DataLoader(load_fn=load_fn)


# ---------------------------------------------------------------------------
# Releases dataloader (Fase A5)
# ---------------------------------------------------------------------------
def create_releases_loader(firestore_ds: Any) -> DataLoader:
    """DataLoader para `Clipping.releases(limit, before)`.

    Keys sao tuplas `(clipping_id, limit, before)`. O loader agrupa por
    `(limit, before)` para batchar todas as chamadas com mesmos parametros
    de paginacao em uma unica execucao de `get_releases_batch`.

    Isso elimina N+1 quando varios clippings sao listados com `releases`
    selecionado e o cliente usa o mesmo `limit`/`before` para todos (caso
    comum: UI lista clippings e mostra as N ultimas releases de cada).

    Retorna `list[ReleaseData]` para cada key, alinhado por posicao.
    """

    async def load_fn(
        keys: list[tuple[str, int, Optional[datetime]]],
    ) -> list[list[ReleaseData]]:
        # Agrupa chaves por (limit, before). Em geral, todas iguais — 1 grupo.
        groups: dict[tuple[int, Optional[datetime]], list[str]] = {}
        for clipping_id, limit, before in keys:
            groups.setdefault((limit, before), []).append(clipping_id)

        # 1 chamada por grupo (em prod tipicamente 1 grupo so).
        combined: dict[tuple[str, int, Optional[datetime]], list[ReleaseData]] = {}
        for (limit, before), clipping_ids in groups.items():
            batch_result = firestore_ds.get_releases_batch(
                clipping_ids=clipping_ids, limit=limit, before=before
            )
            for cid in clipping_ids:
                combined[(cid, limit, before)] = batch_result.get(cid, [])

        return [combined.get(k, []) for k in keys]

    return DataLoader(load_fn=load_fn)
