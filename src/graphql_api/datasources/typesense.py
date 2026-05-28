import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import typesense

logger = logging.getLogger(__name__)


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


def _parse_typesense_conn(env_name: str) -> Optional[dict]:
    """Le e parseia uma env var no formato JSON `{"host":..., "port":..., "protocol":..., "apiKey":...}`.

    Retorna `None` se a env var estiver ausente ou inválida — o caller decide
    como tratar (em prod = warning + DS=None; em testes = mock).
    """
    raw = os.environ.get(env_name)
    if not raw:
        return None
    try:
        conn = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("%s nao e JSON valido: %s", env_name, exc)
        return None
    if not isinstance(conn, dict):
        logger.warning("%s parseou para tipo nao-dict: %s", env_name, type(conn))
        return None
    return conn


def _build_typesense_client(conn: dict, connection_timeout_seconds: int = 5) -> typesense.Client:
    """Constrói um `typesense.Client` a partir de um dict com host/port/protocol/apiKey.

    Aceita tanto camelCase (`apiKey`) quanto snake_case (`api_key`) — defensivo
    para inconsistências entre secrets.
    """
    api_key = conn.get("apiKey") or conn.get("api_key") or ""
    host = conn.get("host") or "localhost"
    port = int(conn.get("port") or 443)
    protocol = conn.get("protocol") or "https"
    return typesense.Client(
        {
            "api_key": api_key,
            "nodes": [{"host": host, "port": port, "protocol": protocol}],
            "connection_timeout_seconds": connection_timeout_seconds,
        }
    )


class TypesenseDatasource:
    def __init__(self, client: typesense.Client):
        self.client = client

    @classmethod
    def from_env(cls, env_name: str = "TYPESENSE_READ_CONN") -> Optional["TypesenseDatasource"]:
        """Cria um datasource a partir da env var (JSON). Retorna None se ausente/inválida.

        Não levanta exceção — falha silenciosa permite que o app suba em dev
        local sem Typesense configurado (DS=None; resolvers que dependem dele
        retornam erro de permission/feature unavailable em runtime).
        """
        conn = _parse_typesense_conn(env_name)
        if conn is None:
            return None
        try:
            client = _build_typesense_client(conn)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("falha ao construir typesense.Client a partir de %s: %s", env_name, exc)
            return None
        return cls(client=client)

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
