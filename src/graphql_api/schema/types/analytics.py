import enum
from typing import Optional

import strawberry


@strawberry.input(description="Date range filter specified as number of days from today")
class DateRange:
    days: int


@strawberry.type(description="Key performance indicators for article analytics")
class AnalyticsKpis:
    total: int
    active_themes: int
    active_agencies: int
    daily_average: float


@strawberry.type(description="Theme statistics with article count")
class ThemeStats:
    label: str
    count: int


@strawberry.type(description="Agency statistics with article count")
class AgencyStats:
    name: str
    count: int


@strawberry.type(description="Daily article count")
class DailyCount:
    date: str
    count: int


@strawberry.enum
class Granularity(enum.Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@strawberry.enum
class MetricType(enum.Enum):
    VOLUME = "volume"
    SENTIMENT = "sentiment"
    READABILITY = "readability"
    THEMES = "themes"


@strawberry.type
class AgencyPeriodMetrics:
    period: str
    agency_key: str
    agency_name: Optional[str]
    article_count: int
    avg_sentiment_score: Optional[float]
    pct_positive: Optional[float]
    pct_negative: Optional[float]
    avg_readability_flesch: Optional[float]
    avg_word_count: Optional[float]
    top_themes: list[ThemeStats]


@strawberry.type
class ArticleSummary:
    unique_id: str
    title: str
    agency_name: Optional[str]
    published_at: Optional[str]
    trending_score: Optional[float]


@strawberry.type
class TrendingThemeResult:
    theme_label: str
    theme_code: Optional[str]
    window_count: int
    baseline_daily_avg: float
    growth_score: float
    top_articles: list[ArticleSummary]
