from typing import Any

from strawberry.dataloader import DataLoader

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
