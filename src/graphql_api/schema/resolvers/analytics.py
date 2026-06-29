from datetime import datetime, timedelta, timezone
from typing import Optional

import strawberry

from graphql_api.schema.types.analytics import (
    AgencyPeriodMetrics,
    AgencyStats,
    AnalyticsKpis,
    ArticleSummary,
    DailyCount,
    DateRange,
    Granularity,
    MetricType,
    ThemeStats,
    TrendingThemeResult,
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

    @strawberry.field(description="Métricas de publicação por agência e período")
    async def agency_analytics(
        self,
        info: strawberry.types.Info,
        agencies: list[str],
        date_from: str,
        date_to: str,
        granularity: Granularity = Granularity.MONTH,
        metrics: Optional[list[MetricType]] = None,
    ) -> list[AgencyPeriodMetrics]:
        if not agencies:
            raise ValueError("agencies não pode ser vazio")
        ds = info.context.postgres_ds
        rows = await ds.agency_analytics(granularity.value, agencies, date_from, date_to)
        return [
            AgencyPeriodMetrics(
                period=str(row["period"]),
                agency_key=row["agency_key"] or "",
                agency_name=row.get("agency_name"),
                article_count=int(row["article_count"] or 0),
                avg_sentiment_score=row.get("avg_sentiment_score"),
                pct_positive=row.get("pct_positive"),
                pct_negative=row.get("pct_negative"),
                avg_readability_flesch=row.get("avg_readability_flesch"),
                avg_word_count=row.get("avg_word_count"),
                top_themes=[],
            )
            for row in rows
        ]

    @strawberry.field(description="Temas em crescimento comparando janela recente com baseline histórico")
    def trending_themes(
        self,
        info: strawberry.types.Info,
        window_days: int = 7,
        baseline_days: int = 28,
        min_articles: int = 3,
        growth_threshold: float = 1.5,
        agency_key: Optional[str] = None,
        limit: int = 10,
    ) -> list[TrendingThemeResult]:
        if window_days <= 0 or baseline_days <= 0:
            raise ValueError("window_days e baseline_days devem ser > 0")
        if window_days >= baseline_days:
            raise ValueError("window_days deve ser menor que baseline_days")

        ts = info.context.typesense_ds

        def _ts_filter(days: int) -> str:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
            return f"published_at:>={int(cutoff.timestamp())}"

        agency_filter = f" && agency:{agency_key}" if agency_key else ""

        window_resp = ts.client.collections["news"].documents.search({
            "q": "*", "per_page": 0,
            "filter_by": _ts_filter(window_days) + agency_filter,
            "facet_by": "theme_1_level_1_label",
            "max_facet_values": 100,
        })
        baseline_resp = ts.client.collections["news"].documents.search({
            "q": "*", "per_page": 0,
            "filter_by": _ts_filter(baseline_days) + agency_filter,
            "facet_by": "theme_1_level_1_label",
            "max_facet_values": 100,
        })

        window_counts = {
            item["value"]: item["count"]
            for item in _extract_facets(window_resp, "theme_1_level_1_label")
        }
        baseline_counts = {
            item["value"]: item["count"]
            for item in _extract_facets(baseline_resp, "theme_1_level_1_label")
        }

        results = []
        for theme, w_count in window_counts.items():
            if w_count < min_articles:
                continue
            b_count = baseline_counts.get(theme, 0)
            baseline_daily = b_count / baseline_days if b_count > 0 else 0.001
            window_daily = w_count / window_days
            growth = window_daily / baseline_daily
            if growth >= growth_threshold:
                results.append(TrendingThemeResult(
                    theme_label=theme,
                    theme_code=None,
                    window_count=w_count,
                    baseline_daily_avg=round(baseline_daily, 3),
                    growth_score=round(growth, 3),
                    top_articles=[],
                ))

        results.sort(key=lambda x: x.growth_score, reverse=True)
        final = results[:limit]
        for result in final:
            safe_label = result.theme_label.replace('"', '\\"')
            theme_filter = (
                _ts_filter(window_days)
                + agency_filter
                + f' && theme_1_level_1_label:="{safe_label}"'
            )
            try:
                art_resp = ts.client.collections["news"].documents.search({
                    "q": "*",
                    "per_page": 5,
                    "filter_by": theme_filter,
                    "sort_by": "trending_score:desc,published_at:desc",
                    "include_fields": "id,unique_id,title,agency,published_at,trending_score",
                })
                result.top_articles = [
                    ArticleSummary(
                        unique_id=h["document"].get("unique_id") or h["document"].get("id", ""),
                        title=h["document"].get("title") or "",
                        agency_name=h["document"].get("agency"),
                        published_at=(
                            datetime.fromtimestamp(
                                h["document"]["published_at"], tz=timezone.utc
                            ).isoformat()
                            if h["document"].get("published_at")
                            else None
                        ),
                        trending_score=h["document"].get("trending_score"),
                    )
                    for h in art_resp.get("hits", [])
                ]
            except Exception:
                pass
        return final
