from datetime import datetime, timedelta, timezone

import strawberry

from graphql_api.schema.types.analytics import (
    AgencyStats,
    AnalyticsKpis,
    DailyCount,
    DateRange,
    ThemeStats,
)


def _extract_facets(response: dict, field_name: str) -> list[dict]:
    """Extract facet counts for a given field from a Typesense response."""
    for fc in response.get("facet_counts", []):
        if fc["field_name"] == field_name:
            return fc["counts"]
    return []


def _date_filter(days: int) -> str:
    """Build a Typesense filter_by clause for the given number of days."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    timestamp = int(cutoff.timestamp())
    return f"published_at:>={timestamp}"


@strawberry.type
class AnalyticsQuery:
    @strawberry.field(description="Key performance indicators for the given date range")
    def analytics_kpis(
        self, info: strawberry.types.Info, range: DateRange
    ) -> AnalyticsKpis:
        if range.days <= 0:
            raise ValueError("range.days must be greater than 0")

        ts = info.context.typesense_ds
        response = ts.client.collections["news"].documents.search(
            {
                "q": "*",
                "per_page": 0,
                "filter_by": _date_filter(range.days),
                "facet_by": "theme_1_level_1_label,agency",
                "max_facet_values": 250,
            }
        )

        total = response.get("found", 0)
        theme_counts = _extract_facets(response, "theme_1_level_1_label")
        agency_counts = _extract_facets(response, "agency")
        daily_average = total / range.days if range.days > 0 else 0.0

        return AnalyticsKpis(
            total=total,
            active_themes=len(theme_counts),
            active_agencies=len(agency_counts),
            daily_average=round(daily_average, 2),
        )

    @strawberry.field(description="Top themes by article count")
    def top_themes(
        self,
        info: strawberry.types.Info,
        range: DateRange,
        limit: int = 8,
    ) -> list[ThemeStats]:
        if range.days <= 0:
            raise ValueError("range.days must be greater than 0")

        ts = info.context.typesense_ds
        response = ts.client.collections["news"].documents.search(
            {
                "q": "*",
                "per_page": 0,
                "filter_by": _date_filter(range.days),
                "facet_by": "theme_1_level_1_label",
                "max_facet_values": limit,
            }
        )

        counts = _extract_facets(response, "theme_1_level_1_label")
        return [
            ThemeStats(label=item["value"], count=item["count"])
            for item in counts
        ]

    @strawberry.field(description="Top agencies by article count")
    def top_agencies(
        self,
        info: strawberry.types.Info,
        range: DateRange,
        limit: int = 8,
    ) -> list[AgencyStats]:
        if range.days <= 0:
            raise ValueError("range.days must be greater than 0")

        ts = info.context.typesense_ds
        response = ts.client.collections["news"].documents.search(
            {
                "q": "*",
                "per_page": 0,
                "filter_by": _date_filter(range.days),
                "facet_by": "agency",
                "max_facet_values": limit,
            }
        )

        counts = _extract_facets(response, "agency")
        return [
            AgencyStats(name=item["value"], count=item["count"])
            for item in counts
        ]

    @strawberry.field(description="Daily article counts for the given date range")
    def articles_timeline(
        self, info: strawberry.types.Info, range: DateRange
    ) -> list[DailyCount]:
        if range.days <= 0:
            raise ValueError("range.days must be greater than 0")

        ts = info.context.typesense_ds
        response = ts.client.collections["news"].documents.search(
            {
                "q": "*",
                "per_page": 0,
                "filter_by": _date_filter(range.days),
                "facet_by": "published_date",
                "max_facet_values": range.days,
            }
        )

        counts = _extract_facets(response, "published_date")
        results = [
            DailyCount(date=item["value"], count=item["count"])
            for item in counts
        ]
        results.sort(key=lambda x: x.date)
        return results
