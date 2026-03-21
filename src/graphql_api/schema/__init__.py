import strawberry

from graphql_api.schema.resolvers.analytics import AnalyticsQuery
from graphql_api.schema.resolvers.articles import ArticleQuery
from graphql_api.schema.resolvers.clippings import ClippingMutation, ClippingQuery
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.marketplace import MarketplaceMutation, MarketplaceQuery
from graphql_api.schema.resolvers.metadata import MetadataQuery
from graphql_api.schema.resolvers.push import PushMutation
from graphql_api.schema.resolvers.search import SearchQuery
from graphql_api.schema.resolvers.widgets import WidgetQuery


@strawberry.type
class Query(
    HealthQuery,
    ArticleQuery,
    SearchQuery,
    MetadataQuery,
    AnalyticsQuery,
    ClippingQuery,
    MarketplaceQuery,
    WidgetQuery,
):
    pass


@strawberry.type
class Mutation(ClippingMutation, MarketplaceMutation, PushMutation):
    pass


schema = strawberry.Schema(query=Query, mutation=Mutation)
