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
