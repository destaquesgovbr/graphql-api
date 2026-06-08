import strawberry

from graphql_api.schema.resolvers.agent import AgentSubscription
from graphql_api.schema.resolvers.analytics import AnalyticsQuery
from graphql_api.schema.resolvers.articles import ArticleQuery
from graphql_api.schema.resolvers.clippings import ClippingMutation, ClippingQuery
from graphql_api.schema.resolvers.health import HealthQuery
from graphql_api.schema.resolvers.internal_mutations import InternalMutation
from graphql_api.schema.resolvers.internal_queries import InternalQuery
from graphql_api.schema.resolvers.marketplace import MarketplaceMutation, MarketplaceQuery
from graphql_api.schema.resolvers.metadata import MetadataQuery
from graphql_api.schema.resolvers.public_content import PublicContentQuery
from graphql_api.schema.resolvers.push import PushMutation, PushQuery
from graphql_api.schema.resolvers.search import SearchQuery
from graphql_api.schema.resolvers.user import UserQuery
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
    PushQuery,
    WidgetQuery,
    UserQuery,
    InternalQuery,
    PublicContentQuery,
):
    pass


@strawberry.type
class Mutation(ClippingMutation, MarketplaceMutation, PushMutation, InternalMutation):
    pass


@strawberry.type
class Subscription(AgentSubscription):
    pass


schema = strawberry.Schema(query=Query, mutation=Mutation, subscription=Subscription)
