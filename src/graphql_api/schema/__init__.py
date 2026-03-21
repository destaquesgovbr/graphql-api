import strawberry

from graphql_api.schema.resolvers.analytics import AnalyticsQuery
from graphql_api.schema.resolvers.articles import ArticleQuery
from graphql_api.schema.resolvers.clippings import ClippingMutation, ClippingQuery
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.metadata import MetadataQuery
from graphql_api.schema.resolvers.search import SearchQuery


@strawberry.type
class Query(HealthQuery, ArticleQuery, SearchQuery, MetadataQuery, AnalyticsQuery, ClippingQuery):
    pass


@strawberry.type
class Mutation(ClippingMutation):
    pass


schema = strawberry.Schema(query=Query, mutation=Mutation)
