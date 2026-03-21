import strawberry

from graphql_api.schema.resolvers.health import HealthQuery


@strawberry.type
class Query(HealthQuery):
    pass


schema = strawberry.Schema(query=Query)
