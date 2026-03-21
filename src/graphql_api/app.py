from fastapi import FastAPI
from fastapi.responses import JSONResponse
from strawberry.fastapi import GraphQLRouter

from graphql_api.context import GraphQLContext, get_context
from graphql_api.schema import schema


def create_app() -> FastAPI:
    app = FastAPI(title="DGB GraphQL API", docs_url=None, redoc_url=None)

    async def context_dependency() -> GraphQLContext:
        return await get_context()

    graphql_router = GraphQLRouter(schema, context_getter=context_dependency)
    app.include_router(graphql_router, prefix="/graphql")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    return app
