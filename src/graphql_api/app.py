from fastapi import FastAPI
from fastapi.responses import JSONResponse
from strawberry.fastapi import GraphQLRouter
from strawberry.subscriptions import (
    GRAPHQL_TRANSPORT_WS_PROTOCOL,
    GRAPHQL_WS_PROTOCOL,
)

from graphql_api.context import GraphQLContext, get_context
from graphql_api.schema import schema


def create_app() -> FastAPI:
    app = FastAPI(title="DGB GraphQL API", docs_url=None, redoc_url=None)

    async def context_dependency() -> GraphQLContext:
        return await get_context()

    # Subscriptions sao expostas via WebSocket (graphql-transport-ws + legacy
    # graphql-ws). Decisao tomada na Fase A6 (PLANO-ATUALIZACAO-v2.md §5):
    # Strawberry 0.311 nao tem suporte estavel a graphql-sse no
    # GraphQLRouter, portanto adotamos WS — formato 1st-class do ecossistema.
    # O passthrough do SSE upstream (clipping worker) continua sendo SSE
    # internamente; apenas o transport cliente <-> graphql-api e WS.
    graphql_router = GraphQLRouter(
        schema,
        context_getter=context_dependency,
        subscription_protocols=(
            GRAPHQL_TRANSPORT_WS_PROTOCOL,
            GRAPHQL_WS_PROTOCOL,
        ),
    )
    app.include_router(graphql_router, prefix="/graphql")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    return app
