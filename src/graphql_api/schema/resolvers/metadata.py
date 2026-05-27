from typing import Optional

import strawberry

from graphql_api.schema.types.theme import Agency, Tag, Theme


def _extract_facets(response: dict, field_name: str) -> list[dict]:
    """Extract facet counts for a given field from a Typesense response."""
    for fc in response.get("facet_counts", []):
        if fc["field_name"] == field_name:
            return fc["counts"]
    return []


@strawberry.type
class MetadataQuery:
    @strawberry.field(description="Lista de temas distintos extraidos das noticias")
    def themes(self, info: strawberry.types.Info) -> list[Theme]:
        typesense_ds = info.context.typesense_ds
        response = typesense_ds.client.collections["news"].documents.search(
            {
                "q": "*",
                "per_page": 0,
                "facet_by": "theme_1_level_1_label",
                "max_facet_values": 250,
            }
        )
        counts = _extract_facets(response, "theme_1_level_1_label")
        return [Theme(code=item["value"], label=item["value"]) for item in counts]

    @strawberry.field(description="Lista de orgaos distintos")
    def agencies(self, info: strawberry.types.Info) -> list[Agency]:
        typesense_ds = info.context.typesense_ds
        response = typesense_ds.client.collections["news"].documents.search(
            {
                "q": "*",
                "per_page": 0,
                "facet_by": "agency",
                "max_facet_values": 250,
            }
        )
        counts = _extract_facets(response, "agency")
        return [Agency(code=item["value"], label=item["value"]) for item in counts]

    @strawberry.field(description="Tags mais populares")
    def popular_tags(
        self, info: strawberry.types.Info, limit: Optional[int] = 20
    ) -> list[Tag]:
        typesense_ds = info.context.typesense_ds
        response = typesense_ds.client.collections["news"].documents.search(
            {
                "q": "*",
                "per_page": 0,
                "facet_by": "tags",
                "max_facet_values": limit,
            }
        )
        counts = _extract_facets(response, "tags")
        return [Tag(label=item["value"], count=item["count"]) for item in counts]
