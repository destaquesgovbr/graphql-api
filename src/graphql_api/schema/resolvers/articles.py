from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.datasources.typesense import ArticleDocument
from graphql_api.schema.types.article import Article, ArticleFilter, ArticlesResult


def _to_graphql_article(doc: ArticleDocument) -> Article:
    return Article(
        unique_id=doc.unique_id,
        title=doc.title,
        url=doc.url,
        image=doc.image,
        video_url=doc.video_url,
        content=doc.content,
        summary=doc.summary,
        subtitle=doc.subtitle,
        editorial_lead=doc.editorial_lead,
        category=doc.category,
        tags=doc.tags,
        agency=doc.agency,
        agency_name=doc.agency_name,
        published_at=doc.published_at,
        extracted_at=doc.extracted_at,
    )


@strawberry.type
class ArticleQuery:
    @strawberry.field(description="Lista artigos com filtros e paginação")
    def articles(
        self,
        info: Info,
        page: int = 1,
        limit: int = 10,
        filter: Optional[ArticleFilter] = None,
    ) -> ArticlesResult:
        ctx = info.context
        ds = ctx.typesense_ds

        result = ds.search_articles(
            page=page,
            limit=limit,
            agencies=filter.agencies if filter else None,
            themes=filter.themes if filter else None,
            tags=filter.tags if filter else None,
            start_date=filter.start_date if filter else None,
            end_date=filter.end_date if filter else None,
        )

        return ArticlesResult(
            articles=[_to_graphql_article(a) for a in result.articles],
            page=result.page,
            found=result.found,
        )

    @strawberry.field(description="Busca artigo por ID")
    def article(self, info: Info, unique_id: str) -> Optional[Article]:
        ctx = info.context
        ds = ctx.typesense_ds

        doc = ds.get_article_by_id(unique_id)
        if doc is None:
            return None
        return _to_graphql_article(doc)
