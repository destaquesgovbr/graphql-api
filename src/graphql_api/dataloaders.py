from typing import Any, Optional

from strawberry.dataloader import DataLoader

from graphql_api.datasources.firestore import SubscriptionData
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
