import strawberry

from graphql_api.schema.resolvers.articles import ArticleQuery
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.metadata import MetadataQuery
from graphql_api.schema.resolvers.search import SearchQuery


@strawberry.type
class Query(HealthQuery, ArticleQuery, SearchQuery, MetadataQuery):
    pass


schema = strawberry.Schema(query=Query)
