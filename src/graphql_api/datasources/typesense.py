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
    theme_1_level_1_code: Optional[str] = None
    theme_1_level_1_label: Optional[str] = None
    theme_1_level_2_code: Optional[str] = None
    theme_1_level_2_label: Optional[str] = None
    theme_1_level_3_code: Optional[str] = None
    theme_1_level_3_label: Optional[str] = None
    most_specific_theme_code: Optional[str] = None
    most_specific_theme_label: Optional[str] = None


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
        theme_1_level_1_code=doc.get("theme_1_level_1_code"),
        theme_1_level_1_label=doc.get("theme_1_level_1_label"),
        theme_1_level_2_code=doc.get("theme_1_level_2_code"),
        theme_1_level_2_label=doc.get("theme_1_level_2_label"),
        theme_1_level_3_code=doc.get("theme_1_level_3_code"),
        theme_1_level_3_label=doc.get("theme_1_level_3_label"),
        most_specific_theme_code=doc.get("most_specific_theme_code"),
        most_specific_theme_label=doc.get("most_specific_theme_label"),
    )


def _iter_documents(response: dict):
    """Itera os documentos de uma resposta de busca, lidando com `hits`
    (busca normal) e `grouped_hits` (quando `group_by` está ativo)."""
    if "grouped_hits" in response:
        for group in response["grouped_hits"]:
            for hit in group.get("hits", []):
                yield hit["document"]
        return
    for hit in response.get("hits", []):
        yield hit["document"]


COLLECTION_NAME = "news"

# Fase 2/4: campos facetáveis de entidade por tipo (combinado = `entities`).
# Fase 4 adiciona EVENT/POLICY (eram silenciosamente descartados no indexer) e
# o pseudo-tipo CANONICAL → `entity_canonical` (facet de canonical_id dedup'd).
_ENTITY_FACET_FIELDS = {
    "ORG": "entity_org",
    "PER": "entity_per",
    "LOC": "entity_loc",
    "EVENT": "entity_event",
    "POLICY": "entity_policy",
    "CANONICAL": "entity_canonical",
}


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
        theme_label: Optional[str] = None,
        entities: Optional[list[str]] = None,
        entity_canonical: Optional[list[str]] = None,
        sentiment: Optional[list[str]] = None,
        dedup: bool = False,
        vector: Optional[list[float]] = None,
        alpha: Optional[float] = None,
        q: str = "*",
        query_by: Optional[str] = None,
        sort_by: Optional[str] = "published_at:desc",
    ) -> SearchResult:
        filter_parts: list[str] = []

        if agencies:
            joined = ", ".join(f"`{a}`" for a in agencies)
            filter_parts.append(f"agency:[{joined}]")

        if themes:
            # OR através de L1/L2/L3 code para cada code informado; tudo OR'd.
            theme_conditions = []
            for theme in themes:
                theme_conditions.append(f"theme_1_level_1_code:={theme}")
                theme_conditions.append(f"theme_1_level_2_code:={theme}")
                theme_conditions.append(f"theme_1_level_3_code:={theme}")
            filter_parts.append("(" + " || ".join(theme_conditions) + ")")

        if theme_label:
            filter_parts.append(f"theme_1_level_1_label:={theme_label}")

        if tags:
            joined = ", ".join(f"`{t}`" for t in tags)
            filter_parts.append(f"tags:[{joined}]")

        if entities:
            joined = ", ".join(f"`{e}`" for e in entities)
            filter_parts.append(f"entities:[{joined}]")

        if entity_canonical:
            # Filtro por entidade canônica (canonical_id / entity_id). Match
            # exato no campo `entity_canonical` (string[] facet); OR entre ids.
            joined = ", ".join(f"`{e}`" for e in entity_canonical)
            filter_parts.append(f"entity_canonical:[{joined}]")

        if sentiment:
            joined = ", ".join(f"`{s}`" for s in sentiment)
            filter_parts.append(f"sentiment_label:[{joined}]")

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
            "q": q,
            "per_page": limit,
            "page": page,
        }
        # sort_by=None => omitir a chave para o Typesense ordenar por relevância
        # de text-match (correto para busca por keyword). Default mantém date-sort.
        if sort_by is not None:
            search_params["sort_by"] = sort_by
        # Busca por keyword real: só quando q != "*" (wildcard default).
        # Aí precisamos de query_by para o Typesense saber em que campos buscar.
        if q != "*" and query_by:
            search_params["query_by"] = query_by
        if filter_by:
            search_params["filter_by"] = filter_by

        if vector is not None:
            # Busca híbrida: alpha controla peso semântico vs. keyword.
            # Default 0.3 mantém o comportamento legado quando alpha=None.
            effective_alpha = 0.3 if alpha is None else alpha
            vector_str = ", ".join(str(v) for v in vector)
            search_params["vector_query"] = (
                f"content_embedding:([{vector_str}], alpha:{effective_alpha})"
            )

        if dedup:
            search_params["group_by"] = "content_hash"
            search_params["group_limit"] = 1

        response = self.client.collections[COLLECTION_NAME].documents.search(search_params)

        articles = [_document_to_article(doc) for doc in _iter_documents(response)]

        return SearchResult(
            articles=articles,
            page=page,
            found=response.get("found", 0),
        )

    def theme_counts(self, level: int, days: int) -> list[tuple[str, int]]:
        """Contagem de artigos por code de tema (nível `level`), nos últimos `days` dias.

        Usa `group_by:'theme_1_level_{level}_code'` + `filter_by:'published_at:>=<now-days>'`
        + `per_page:250`, lendo `grouped_hits[].group_key[0]` (code) + `.found` (contagem).
        Espelha `temas/actions.ts:getThemeArticleCounts` do portal.
        """
        since = datetime.now(tz=timezone.utc).timestamp() - days * 86400
        search_params = {
            "q": "*",
            "query_by": "title",
            "per_page": 250,
            "group_by": f"theme_1_level_{level}_code",
            "group_limit": 1,
            "filter_by": f"published_at:>={int(since)}",
        }

        response = self.client.collections[COLLECTION_NAME].documents.search(search_params)

        counts: list[tuple[str, int]] = []
        for group in response.get("grouped_hits", []):
            key = group.get("group_key") or []
            if not key:
                continue
            counts.append((key[0], group.get("found", 0)))
        return counts

    def get_article_by_id(self, unique_id: str) -> Optional[ArticleDocument]:
        """Busca um artigo por `unique_id` via SEARCH (não `.documents[id].retrieve()`).

        `retrieve()` é uma operação não-search que a search-only API key não pode
        executar (Typesense responde 401). Usar um filtro exato por `unique_id`
        numa busca normal funciona com a search-only key e mantém o mesmo
        mapeamento de `search_articles`.
        """
        result = self.client.collections[COLLECTION_NAME].documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": f"unique_id:=`{unique_id}`",
                "per_page": 1,
            }
        )
        for doc in _iter_documents(result):
            return _document_to_article(doc)
        return None

    def get_articles_by_ids(self, unique_ids: list[str]) -> dict[str, ArticleDocument]:
        """Busca múltiplos artigos por `unique_id` via SEARCH (filtro IN),
        compatível com a search-only key (não usa `.documents[id].retrieve()`).

        Retorna `{unique_id: ArticleDocument}` — o caller preserva a ordem
        desejada (ex.: ordem de similaridade). Ids ausentes no índice
        simplesmente não aparecem no dict.
        """
        if not unique_ids:
            return {}
        joined = ", ".join(f"`{uid}`" for uid in unique_ids)
        per_page = min(max(len(unique_ids), 1), 250)
        result = self.client.collections[COLLECTION_NAME].documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": f"unique_id:[{joined}]",
                "per_page": per_page,
            }
        )
        docs: dict[str, ArticleDocument] = {}
        for doc in _iter_documents(result):
            article = _document_to_article(doc)
            docs[article.unique_id] = article
        return docs

    def entity_facets(
        self, query: str = "", entity_type: Optional[str] = None, limit: int = 10
    ) -> list[tuple[str, int]]:
        """Sugestões de entidades (facet) para o typeahead do filtro e o header
        das páginas de entidade.

        `entity_type` (ORG/PER/LOC) escolhe o campo tipado; ausente/desconhecido
        usa o campo combinado `entities`. `query` (prefixo) filtra os valores via
        `facet_query`. Retorna [(valor, contagem)] na ordem do Typesense
        (desc por contagem).
        """
        field = _ENTITY_FACET_FIELDS.get((entity_type or "").upper(), "entities")
        params: dict = {
            "q": "*",
            "query_by": "title",
            "per_page": 0,
            "facet_by": field,
            "max_facet_values": limit,
        }
        if query and query.strip():
            params["facet_query"] = f"{field}:{query.strip()}"

        response = self.client.collections[COLLECTION_NAME].documents.search(params)

        out: list[tuple[str, int]] = []
        for fc in response.get("facet_counts", []):
            if fc.get("field_name") == field:
                for value in fc.get("counts", []):
                    out.append((value["value"], value["count"]))
        return out
