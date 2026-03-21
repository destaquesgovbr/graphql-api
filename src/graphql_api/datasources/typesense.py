from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import typesense


@dataclass
class ArticleDocument:
    unique_id: str
    title: str
    url: str
    image: Optional[str] = None
    video_url: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    subtitle: Optional[str] = None
    editorial_lead: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    agency: Optional[str] = None
    agency_name: Optional[str] = None
    published_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None


@dataclass
class SearchResult:
    articles: list[ArticleDocument]
    page: int
    found: int


def _parse_timestamp(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def _document_to_article(doc: dict) -> ArticleDocument:
    return ArticleDocument(
        unique_id=doc.get("unique_id", ""),
        title=doc.get("title", ""),
        url=doc.get("url", ""),
        image=doc.get("image"),
        video_url=doc.get("video_url"),
        content=doc.get("content"),
        summary=doc.get("summary"),
        subtitle=doc.get("subtitle"),
        editorial_lead=doc.get("editorial_lead"),
        category=doc.get("category"),
        tags=doc.get("tags", []),
        agency=doc.get("agency"),
        agency_name=doc.get("agency_name"),
        published_at=_parse_timestamp(doc.get("published_at")),
        extracted_at=_parse_timestamp(doc.get("extracted_at")),
    )


COLLECTION_NAME = "news"


class TypesenseDatasource:
    def __init__(self, client: typesense.Client):
        self.client = client

    def search_articles(
        self,
        page: int = 1,
        limit: int = 10,
        agencies: Optional[list[str]] = None,
        themes: Optional[list[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> SearchResult:
        filter_parts: list[str] = []

        if agencies:
            joined = ", ".join(f"`{a}`" for a in agencies)
            filter_parts.append(f"agency:[{joined}]")

        if themes:
            theme_conditions = []
            for theme in themes:
                theme_conditions.append(f"theme_1_level_1_code:={theme}")
            filter_parts.append(" || ".join(theme_conditions))

        if tags:
            joined = ", ".join(f"`{t}`" for t in tags)
            filter_parts.append(f"tags:[{joined}]")

        if start_date:
            dt = datetime.fromisoformat(start_date)
            ts = int(dt.timestamp())
            filter_parts.append(f"published_at:>={ts}")

        if end_date:
            dt = datetime.fromisoformat(end_date)
            ts = int(dt.timestamp())
            filter_parts.append(f"published_at:<={ts}")

        filter_by = " && ".join(filter_parts) if filter_parts else ""

        search_params = {
            "q": "*",
            "per_page": limit,
            "page": page,
            "sort_by": "published_at:desc",
        }
        if filter_by:
            search_params["filter_by"] = filter_by

        response = self.client.collections[COLLECTION_NAME].documents.search(search_params)

        articles = [_document_to_article(hit["document"]) for hit in response.get("hits", [])]

        return SearchResult(
            articles=articles,
            page=page,
            found=response.get("found", 0),
        )

    def get_article_by_id(self, unique_id: str) -> Optional[ArticleDocument]:
        try:
            doc = self.client.collections[COLLECTION_NAME].documents[unique_id].retrieve()
            return _document_to_article(doc)
        except typesense.exceptions.ObjectNotFound:
            return None
