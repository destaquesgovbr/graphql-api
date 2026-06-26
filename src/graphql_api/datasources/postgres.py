import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class NewsRecord:
    unique_id: str
    title: str
    url: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    subtitle: Optional[str] = None
    editorial_lead: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    agency_key: Optional[str] = None
    agency_name: Optional[str] = None
    published_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None
    theme_l1_code: Optional[str] = None
    theme_l1_label: Optional[str] = None
    theme_l2_code: Optional[str] = None
    theme_l2_label: Optional[str] = None
    theme_l3_code: Optional[str] = None
    theme_l3_label: Optional[str] = None
    most_specific_theme_code: Optional[str] = None
    most_specific_theme_label: Optional[str] = None
    features: Optional[dict[str, Any]] = None


@dataclass
class BigQueryRecord(NewsRecord):
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    trending_score: Optional[float] = None
    word_count: Optional[int] = None
    has_image: Optional[bool] = None
    has_video: Optional[bool] = None
    image_broken: Optional[bool] = None
    readability_flesch: Optional[float] = None


@dataclass
class SimilarArticleRecord:
    unique_id: str
    similarity: float


@dataclass
class EntityRegistryRecord:
    """Linha canônica de `entity_registry` (Fase 4 — canonicalização).

    `aliases` é JSONB; asyncpg o devolve como str (sem codec), desserializado
    em `_row_to_entity_registry`.
    """

    entity_id: str
    canonical_name: Optional[str] = None
    type: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    wikidata_id: Optional[str] = None
    wikidata_url: Optional[str] = None
    description: Optional[str] = None
    agency_key: Optional[str] = None


@dataclass
class RelatedEntityRecord:
    """Vizinho 1-hop de uma entidade na rede de co-menção (Fase 6c).

    A "outra ponta" da aresta `entity_edges` (kind=co_mention) juntada com
    `entity_registry` para hidratar nome/tipo/wikidata. `weight` = nº de artigos
    em co-menção (campo agregado da aresta)."""

    entity_id: str
    canonical_name: Optional[str] = None
    type: Optional[str] = None
    wikidata_id: Optional[str] = None
    weight: int = 0
    kind: str = "co_mention"


@dataclass
class EntityNetworkNodeRecord:
    """Nó da ego-network de uma entidade (Fase 6c)."""

    entity_id: str
    canonical_name: Optional[str] = None
    type: Optional[str] = None
    wikidata_id: Optional[str] = None


@dataclass
class EntityNetworkEdgeRecord:
    """Aresta da ego-network (Fase 6c). `src`/`dst` são entity_ids."""

    src: str
    dst: str
    weight: int = 0
    kind: str = "co_mention"


@dataclass
class EntityNetworkRecord:
    """Projeção de grafo (nós + arestas) ao redor de uma entidade (Fase 6c)."""

    nodes: list[EntityNetworkNodeRecord] = field(default_factory=list)
    edges: list[EntityNetworkEdgeRecord] = field(default_factory=list)


@dataclass
class IntegrityCandidateRecord:
    unique_id: str
    url: str
    image_url: Optional[str] = None
    published_at: Optional[datetime] = None
    integrity: Optional[dict[str, Any]] = None


@dataclass
class TypesenseDocRecord(NewsRecord):
    content_embedding: Optional[list[float]] = None
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    trending_score: Optional[float] = None
    word_count: Optional[int] = None
    has_image: Optional[bool] = None
    has_video: Optional[bool] = None
    image_broken: Optional[bool] = None
    readability_flesch: Optional[float] = None


_UPSERT_FEATURES_SQL = """
INSERT INTO news_features (unique_id, features, updated_at)
VALUES ($1, $2::jsonb, NOW())
ON CONFLICT (unique_id) DO UPDATE SET
  features = news_features.features || $2::jsonb,
  updated_at = NOW()
"""

_FEATURES_BATCH_SQL = """
SELECT unique_id, features
FROM news_features
WHERE unique_id = ANY($1)
"""

# Fase 4 (canonicalização): linha canônica por entity_id. `aliases` é JSONB
# (asyncpg devolve como str → json.loads). `description`/`wikidata_*` podem ser
# nulos quando a entidade não foi linkada ao Wikidata.
_ENTITY_REGISTRY_SQL = """
SELECT entity_id, canonical_name, type, aliases,
  wikidata_id, wikidata_url, description, agency_key
FROM entity_registry
WHERE entity_id = $1
"""

_ENTITY_REGISTRY_BATCH_SQL = """
SELECT entity_id, canonical_name, type, aliases,
  wikidata_id, wikidata_url, description, agency_key
FROM entity_registry
WHERE entity_id = ANY($1)
"""

# Fase 6c — entidades relacionadas (1-hop). Lê arestas de co-menção em
# `entity_edges` onde a entidade aparece em qualquer ponta (src_id OU dst_id),
# pega a "outra ponta" e junta com `entity_registry` para hidratar
# nome/tipo/wikidata. `entity_id` SEMPRE como `$1` parametrizado (sem
# interpolação — risco de injection). Ordenado por weight desc; LIMIT em $2.
# A ordem canônica src_id < dst_id em entity_edges garante 1 linha por par.
_RELATED_ENTITIES_SQL = """
SELECT
  CASE WHEN e.src_id = $1 THEN e.dst_id ELSE e.src_id END AS other_id,
  e.weight AS weight,
  e.kind AS kind,
  r.canonical_name AS canonical_name,
  r.type AS type,
  r.wikidata_id AS wikidata_id
FROM entity_edges e
JOIN entity_registry r
  ON r.entity_id = CASE WHEN e.src_id = $1 THEN e.dst_id ELSE e.src_id END
WHERE e.kind = 'co_mention'
  AND ($1 IN (e.src_id, e.dst_id))
ORDER BY e.weight DESC, other_id ASC
LIMIT $2
"""

# Fase 6c — ego-network multi-hop (depth<=2) via CTE recursiva em `entity_edges`.
# Parte de `$1` (parametrizado), expande pelas arestas de co-menção até `$2`
# saltos (clampado a 2 no datasource), coletando o conjunto de entity_ids
# alcançáveis. A travessia é não-direcionada: cada aresta conecta src_id↔dst_id
# em ambos os sentidos. Nós e arestas são consultadas em duas queries que
# compartilham a mesma CTE — evita produto cartesiano de juntar ambos numa só.
_ENTITY_NETWORK_NODES_SQL = """
WITH RECURSIVE reachable(entity_id, depth) AS (
  SELECT $1::varchar, 0
  UNION
  SELECT
    CASE WHEN e.src_id = rc.entity_id THEN e.dst_id ELSE e.src_id END,
    rc.depth + 1
  FROM entity_edges e
  JOIN reachable rc
    ON rc.entity_id IN (e.src_id, e.dst_id)
  WHERE e.kind = 'co_mention'
    AND rc.depth < $2
)
SELECT DISTINCT
  r.entity_id AS entity_id,
  r.canonical_name AS canonical_name,
  r.type AS type,
  r.wikidata_id AS wikidata_id
FROM (SELECT DISTINCT entity_id FROM reachable) ni
JOIN entity_registry r ON r.entity_id = ni.entity_id
"""

_ENTITY_NETWORK_EDGES_SQL = """
WITH RECURSIVE reachable(entity_id, depth) AS (
  SELECT $1::varchar, 0
  UNION
  SELECT
    CASE WHEN e.src_id = rc.entity_id THEN e.dst_id ELSE e.src_id END,
    rc.depth + 1
  FROM entity_edges e
  JOIN reachable rc
    ON rc.entity_id IN (e.src_id, e.dst_id)
  WHERE e.kind = 'co_mention'
    AND rc.depth < $2
),
node_ids AS (
  SELECT DISTINCT entity_id FROM reachable
)
SELECT e.src_id AS src, e.dst_id AS dst, e.weight AS weight, e.kind AS kind
FROM entity_edges e
WHERE e.kind = 'co_mention'
  AND e.src_id IN (SELECT entity_id FROM node_ids)
  AND e.dst_id IN (SELECT entity_id FROM node_ids)
ORDER BY e.weight DESC
LIMIT $3
"""

_NEWS_BASE_SQL = """
SELECT n.*,
  t1.code as theme_l1_code, t1.label as theme_l1_label,
  t2.code as theme_l2_code, t2.label as theme_l2_label,
  t3.code as theme_l3_code, t3.label as theme_l3_label,
  tm.code as most_specific_theme_code, tm.label as most_specific_theme_label,
  nf.features
FROM news n
LEFT JOIN themes t1 ON n.theme_l1_id = t1.id
LEFT JOIN themes t2 ON n.theme_l2_id = t2.id
LEFT JOIN themes t3 ON n.theme_l3_id = t3.id
LEFT JOIN themes tm ON n.most_specific_theme_id = tm.id
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
"""

_NEWS_BIGQUERY_SQL = """
SELECT n.*,
  t1.code as theme_l1_code, t1.label as theme_l1_label,
  t2.code as theme_l2_code, t2.label as theme_l2_label,
  t3.code as theme_l3_code, t3.label as theme_l3_label,
  tm.code as most_specific_theme_code, tm.label as most_specific_theme_label,
  nf.features
FROM news n
LEFT JOIN themes t1 ON n.theme_l1_id = t1.id
LEFT JOIN themes t2 ON n.theme_l2_id = t2.id
LEFT JOIN themes t3 ON n.theme_l3_id = t3.id
LEFT JOIN themes tm ON n.most_specific_theme_id = tm.id
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
WHERE n.published_at BETWEEN $1 AND $2
"""

# Vizinhos mais próximos por cosine via índice HNSW (vector_cosine_ops). O
# embedding do artigo base é passado como literal `$1::vector` (não como
# referência a coluna de outra linha) — só assim o planner usa o índice
# `idx_news_content_embedding_hnsw`; comparar com `n1.content_embedding` (linha
# juntada) força Seq Scan em ~333k linhas (~4s). O threshold é aplicado em
# Python sobre o resultado nearest-first.
_SIMILAR_ARTICLES_SQL = """
SELECT unique_id,
  1 - (content_embedding <=> $1::vector) AS similarity
FROM news
WHERE unique_id != $2
  AND content_embedding IS NOT NULL
ORDER BY content_embedding <=> $1::vector
LIMIT $3
"""

_INTEGRITY_BATCH_SQL = """
SELECT n.unique_id, n.url, n.image_url, n.published_at,
  nf.features->'integrity' AS integrity
FROM news n
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
WHERE n.published_at > NOW() - INTERVAL '5 months'
  AND (
    nf.features IS NULL
    OR nf.features->'integrity' IS NULL
    OR nf.features->'integrity'->>'checked_at' IS NULL
    OR (nf.features->'integrity'->>'checked_at')::timestamptz < NOW() - INTERVAL '7 days'
  )
ORDER BY n.published_at DESC
LIMIT $1
"""

_NEWS_TYPESENSE_SQL = """
SELECT n.*, n.content_embedding,
  t1.code as theme_l1_code, t1.label as theme_l1_label,
  t2.code as theme_l2_code, t2.label as theme_l2_label,
  t3.code as theme_l3_code, t3.label as theme_l3_label,
  tm.code as most_specific_theme_code, tm.label as most_specific_theme_label,
  nf.features
FROM news n
LEFT JOIN themes t1 ON n.theme_l1_id = t1.id
LEFT JOIN themes t2 ON n.theme_l2_id = t2.id
LEFT JOIN themes t3 ON n.theme_l3_id = t3.id
LEFT JOIN themes tm ON n.most_specific_theme_id = tm.id
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
"""

_AGENCY_ANALYTICS_SQL = """
SELECT
    DATE_TRUNC($1, n.published_at)::text AS period,
    n.agency_key,
    n.agency_name,
    COUNT(*) AS article_count,
    AVG((nf.features->>'sentiment_score')::float) AS avg_sentiment_score,
    AVG(CASE WHEN (nf.features->>'sentiment_label') = 'positive' THEN 1.0 ELSE 0.0 END) AS pct_positive,
    AVG(CASE WHEN (nf.features->>'sentiment_label') = 'negative' THEN 1.0 ELSE 0.0 END) AS pct_negative,
    AVG((nf.features->>'readability_flesch')::float) AS avg_readability_flesch,
    AVG((nf.features->>'word_count')::float) AS avg_word_count
FROM news n
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
WHERE n.agency_key = ANY($2)
  AND n.published_at BETWEEN $3::timestamptz AND $4::timestamptz
GROUP BY period, n.agency_key, n.agency_name
ORDER BY period, n.agency_key
"""

# Variante para granularidade DAY: generate_series garante que todos os dias do
# intervalo aparecem no resultado, mesmo sem artigos (article_count=0).
# Parâmetros: $1=agencies, $2=date_from (date), $3=date_to (date).
_AGENCY_ANALYTICS_DAY_SQL = """
WITH date_series AS (
    SELECT gs::date AS day
    FROM generate_series($2::date, $3::date, '1 day'::interval) gs
),
agency_names AS (
    SELECT DISTINCT agency_key, agency_name
    FROM news
    WHERE agency_key = ANY($1)
),
daily_agg AS (
    SELECT
        n.published_at::date AS day,
        n.agency_key,
        COUNT(*) AS article_count,
        AVG((nf.features->>'sentiment_score')::float) AS avg_sentiment_score,
        AVG(CASE WHEN (nf.features->>'sentiment_label') = 'positive' THEN 1.0 ELSE 0.0 END) AS pct_positive,
        AVG(CASE WHEN (nf.features->>'sentiment_label') = 'negative' THEN 1.0 ELSE 0.0 END) AS pct_negative,
        AVG((nf.features->>'readability_flesch')::float) AS avg_readability_flesch,
        AVG((nf.features->>'word_count')::float) AS avg_word_count
    FROM news n
    LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
    WHERE n.agency_key = ANY($1)
      AND n.published_at::date BETWEEN $2::date AND $3::date
    GROUP BY n.published_at::date, n.agency_key
)
SELECT
    ds.day::text AS period,
    an.agency_key,
    an.agency_name,
    COALESCE(da.article_count, 0) AS article_count,
    da.avg_sentiment_score,
    da.pct_positive,
    da.pct_negative,
    da.avg_readability_flesch,
    da.avg_word_count
FROM date_series ds
CROSS JOIN agency_names an
LEFT JOIN daily_agg da ON da.day = ds.day AND da.agency_key = an.agency_key
ORDER BY ds.day, an.agency_key
"""

# Série temporal de cobertura de uma entidade por agência e período.
# `$1` = granularidade (day/week/month), `$2` = entity_id, `$3` = date_from
# (NULL = sem limite inferior), `$4` = date_to (NULL = sem limite superior).
_ENTITY_COVERAGE_SQL = """
SELECT
    DATE_TRUNC($1, ne.published_at)::text AS period,
    n.agency_key,
    n.agency_name,
    COUNT(DISTINCT ne.unique_id) AS article_count,
    SUM(ne.count) AS total_mentions,
    AVG((nf.features->>'sentiment_score')::float) AS avg_sentiment_score
FROM news_entities ne
JOIN news n ON ne.unique_id = n.unique_id
LEFT JOIN news_features nf ON ne.unique_id = nf.unique_id
WHERE ne.entity_id = $2
  AND ($3::timestamptz IS NULL OR ne.published_at >= $3::timestamptz)
  AND ($4::timestamptz IS NULL OR ne.published_at <= $4::timestamptz)
GROUP BY period, n.agency_key, n.agency_name
ORDER BY period
"""

# Busca de entidades por nome ou alias: UNION entre match exato por alias
# normalizado (unaccent + lower) e match fuzzy por trigrama (pg_trgm %).
# `$1` = query, `$2` = tipo (NULL = todos), `$3` = limit.
# Ordenado por confidence DESC, article_count DESC.
_ENTITY_SEARCH_SQL = """
SELECT
    er.entity_id,
    er.canonical_name,
    er.type,
    er.description,
    er.wikidata_url,
    er.agency_key,
    COALESCE(er.aliases::text, '[]') AS aliases,
    COALESCE(counts.article_count, 0) AS article_count,
    1.0 AS confidence,
    'alias_exact' AS match_type
FROM entity_alias ea
JOIN entity_registry er ON ea.entity_id = er.entity_id
LEFT JOIN (
    SELECT entity_id, COUNT(*) AS article_count FROM news_entities GROUP BY entity_id
) counts ON er.entity_id = counts.entity_id
WHERE ea.alias_norm = lower($1)
  AND ($2::text IS NULL OR er.type = $2)
UNION ALL
SELECT
    er.entity_id,
    er.canonical_name,
    er.type,
    er.description,
    er.wikidata_url,
    er.agency_key,
    COALESCE(er.aliases::text, '[]') AS aliases,
    COALESCE(counts.article_count, 0) AS article_count,
    similarity(er.canonical_name, $1) AS confidence,
    'trgm_fuzzy' AS match_type
FROM entity_registry er
LEFT JOIN (
    SELECT entity_id, COUNT(*) AS article_count FROM news_entities GROUP BY entity_id
) counts ON er.entity_id = counts.entity_id
WHERE er.canonical_name % $1
  AND ($2::text IS NULL OR er.type = $2)
ORDER BY confidence DESC, article_count DESC
LIMIT $3
"""


_TRENDING_ENTITIES_SQL = """
    SELECT entity_id, canonical_name, type,
           trending_score, volume_ratio, window_count, window_agencies,
           computed_at::text
    FROM entity_trending_scores
    ORDER BY trending_score DESC
    LIMIT $1
"""

# Artigos de uma entidade canônica: deduplica por unique_id (news_entities pode
# ter múltiplas linhas por artigo), ordena por published_at desc, pagina.
# total_count = total sem paginação (subquery — evita window fn sobre outer LIMIT).
_ENTITY_ARTICLES_SQL = """
WITH entity_news AS (
    SELECT DISTINCT ne.unique_id
    FROM news_entities ne
    WHERE ne.entity_id = $1
)
SELECT n.*,
  t1.code AS theme_l1_code, t1.label AS theme_l1_label,
  t2.code AS theme_l2_code, t2.label AS theme_l2_label,
  t3.code AS theme_l3_code, t3.label AS theme_l3_label,
  tm.code AS most_specific_theme_code, tm.label AS most_specific_theme_label,
  nf.features,
  (SELECT COUNT(*) FROM entity_news) AS total_count
FROM entity_news en
JOIN news n ON n.unique_id = en.unique_id
LEFT JOIN themes t1 ON n.theme_l1_id = t1.id
LEFT JOIN themes t2 ON n.theme_l2_id = t2.id
LEFT JOIN themes t3 ON n.theme_l3_id = t3.id
LEFT JOIN themes tm ON n.most_specific_theme_id = tm.id
LEFT JOIN news_features nf ON n.unique_id = nf.unique_id
ORDER BY n.published_at DESC
LIMIT $2 OFFSET $3
"""


def _row_to_news_record(row: dict) -> NewsRecord:
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return NewsRecord(
        unique_id=row["unique_id"],
        title=row["title"],
        url=row["url"],
        image_url=row.get("image_url"),
        video_url=row.get("video_url"),
        content=row.get("content"),
        summary=row.get("summary"),
        subtitle=row.get("subtitle"),
        editorial_lead=row.get("editorial_lead"),
        category=row.get("category"),
        tags=tags,
        agency_key=row.get("agency_key"),
        agency_name=row.get("agency_name"),
        published_at=row.get("published_at"),
        extracted_at=row.get("extracted_at"),
        theme_l1_code=row.get("theme_l1_code"),
        theme_l1_label=row.get("theme_l1_label"),
        theme_l2_code=row.get("theme_l2_code"),
        theme_l2_label=row.get("theme_l2_label"),
        theme_l3_code=row.get("theme_l3_code"),
        theme_l3_label=row.get("theme_l3_label"),
        most_specific_theme_code=row.get("most_specific_theme_code"),
        most_specific_theme_label=row.get("most_specific_theme_label"),
        features=row.get("features"),
    )


def _row_to_bigquery_record(row: dict) -> BigQueryRecord:
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    features = row.get("features") or {}
    return BigQueryRecord(
        unique_id=row["unique_id"],
        title=row["title"],
        url=row["url"],
        image_url=row.get("image_url"),
        video_url=row.get("video_url"),
        content=row.get("content"),
        summary=row.get("summary"),
        subtitle=row.get("subtitle"),
        editorial_lead=row.get("editorial_lead"),
        category=row.get("category"),
        tags=tags,
        agency_key=row.get("agency_key"),
        agency_name=row.get("agency_name"),
        published_at=row.get("published_at"),
        extracted_at=row.get("extracted_at"),
        theme_l1_code=row.get("theme_l1_code"),
        theme_l1_label=row.get("theme_l1_label"),
        theme_l2_code=row.get("theme_l2_code"),
        theme_l2_label=row.get("theme_l2_label"),
        theme_l3_code=row.get("theme_l3_code"),
        theme_l3_label=row.get("theme_l3_label"),
        most_specific_theme_code=row.get("most_specific_theme_code"),
        most_specific_theme_label=row.get("most_specific_theme_label"),
        features=features,
        sentiment_label=features.get("sentiment_label"),
        sentiment_score=features.get("sentiment_score"),
        trending_score=features.get("trending_score"),
        word_count=features.get("word_count"),
        has_image=features.get("has_image"),
        has_video=features.get("has_video"),
        image_broken=features.get("image_broken"),
        readability_flesch=features.get("readability_flesch"),
    )


def _row_to_similar_article(row: dict) -> SimilarArticleRecord:
    return SimilarArticleRecord(
        unique_id=row["unique_id"],
        similarity=row["similarity"],
    )


def _coerce_alias_list(raw: Any) -> list[str]:
    """Normaliza `aliases` (JSONB) para `list[str]`. asyncpg devolve JSONB como
    str → json.loads defensivo; valores não-lista ou itens não-string viram
    lista vazia / são descartados (tolerante, nunca levanta)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item)
    return out


def _row_to_entity_registry(row: dict) -> EntityRegistryRecord:
    return EntityRegistryRecord(
        entity_id=row["entity_id"],
        canonical_name=row.get("canonical_name"),
        type=row.get("type"),
        aliases=_coerce_alias_list(row.get("aliases")),
        wikidata_id=row.get("wikidata_id"),
        wikidata_url=row.get("wikidata_url"),
        description=row.get("description"),
        agency_key=row.get("agency_key"),
    )


def _row_to_related_entity(row: dict) -> RelatedEntityRecord:
    return RelatedEntityRecord(
        entity_id=row["other_id"],
        canonical_name=row.get("canonical_name"),
        type=row.get("type"),
        wikidata_id=row.get("wikidata_id"),
        weight=row.get("weight") or 0,
        kind=row.get("kind") or "co_mention",
    )


def _row_to_network_node(row: dict) -> EntityNetworkNodeRecord:
    return EntityNetworkNodeRecord(
        entity_id=row["entity_id"],
        canonical_name=row.get("canonical_name"),
        type=row.get("type"),
        wikidata_id=row.get("wikidata_id"),
    )


def _row_to_network_edge(row: dict) -> EntityNetworkEdgeRecord:
    return EntityNetworkEdgeRecord(
        src=row["src"],
        dst=row["dst"],
        weight=row.get("weight") or 0,
        kind=row.get("kind") or "co_mention",
    )


def _row_to_integrity_candidate(row: dict) -> IntegrityCandidateRecord:
    integrity = row.get("integrity")
    if isinstance(integrity, str):
        integrity = json.loads(integrity)
    return IntegrityCandidateRecord(
        unique_id=row["unique_id"],
        url=row["url"],
        image_url=row.get("image_url"),
        published_at=row.get("published_at"),
        integrity=integrity,
    )


def _row_to_typesense_doc(row: dict) -> TypesenseDocRecord:
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    features = row.get("features") or {}
    return TypesenseDocRecord(
        unique_id=row["unique_id"],
        title=row["title"],
        url=row["url"],
        image_url=row.get("image_url"),
        video_url=row.get("video_url"),
        content=row.get("content"),
        summary=row.get("summary"),
        subtitle=row.get("subtitle"),
        editorial_lead=row.get("editorial_lead"),
        category=row.get("category"),
        tags=tags,
        agency_key=row.get("agency_key"),
        agency_name=row.get("agency_name"),
        published_at=row.get("published_at"),
        extracted_at=row.get("extracted_at"),
        theme_l1_code=row.get("theme_l1_code"),
        theme_l1_label=row.get("theme_l1_label"),
        theme_l2_code=row.get("theme_l2_code"),
        theme_l2_label=row.get("theme_l2_label"),
        theme_l3_code=row.get("theme_l3_code"),
        theme_l3_label=row.get("theme_l3_label"),
        most_specific_theme_code=row.get("most_specific_theme_code"),
        most_specific_theme_label=row.get("most_specific_theme_label"),
        features=features,
        content_embedding=row.get("content_embedding"),
        sentiment_label=features.get("sentiment_label"),
        sentiment_score=features.get("sentiment_score"),
        trending_score=features.get("trending_score"),
        word_count=features.get("word_count"),
        has_image=features.get("has_image"),
        has_video=features.get("has_video"),
        image_broken=features.get("image_broken"),
        readability_flesch=features.get("readability_flesch"),
    )


class PostgresDatasource:
    def __init__(self, pool):
        self._pool = pool

    @classmethod
    async def from_env(
        cls,
        env_name: str = "DATABASE_URL",
        min_size: int = 1,
        max_size: int = 10,
    ) -> Optional["PostgresDatasource"]:
        """Cria datasource asyncpg a partir da env var `DATABASE_URL`.

        Retorna None se a env var nao estiver setada ou se a criacao do pool
        falhar (mantém o app subindo sem Postgres em dev local).
        """
        dsn = os.environ.get(env_name)
        if not dsn:
            return None
        try:
            import asyncpg

            pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("falha ao criar pool asyncpg a partir de %s: %s", env_name, exc)
            return None
        return cls(pool=pool)

    async def close(self) -> None:
        """Fecha o pool de conexões (chamado no shutdown do FastAPI)."""
        pool = self._pool
        if pool is None:
            return
        close = getattr(pool, "close", None)
        if close is None:
            return
        try:
            result = close()
            # asyncpg.Pool.close() é async; pools de testes podem ser sync.
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("erro ao fechar pool postgres: %s", exc)

    async def get_news_by_id(self, unique_id: str) -> Optional[NewsRecord]:
        query = _NEWS_BASE_SQL + " WHERE n.unique_id = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, unique_id)
        if row is None:
            return None
        return _row_to_news_record(dict(row))

    async def get_news_batch(self, unique_ids: list[str]) -> list[NewsRecord]:
        if not unique_ids:
            return []
        query = _NEWS_BASE_SQL + " WHERE n.unique_id = ANY($1)"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, unique_ids)
        return [_row_to_news_record(dict(r)) for r in rows]

    async def get_features_batch(self, unique_ids: list[str]) -> dict[str, dict]:
        """Carrega `news_features.features` para vários `unique_id` numa query.

        Retorna `{unique_id: features_dict}` apenas para os ids presentes em
        `news_features`. asyncpg devolve JSONB como str (sem codec de tipo),
        por isso desserializamos defensivamente.
        """
        if not unique_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_FEATURES_BATCH_SQL, unique_ids)
        result: dict[str, dict] = {}
        for r in rows:
            row = dict(r)
            feats = row.get("features")
            if isinstance(feats, str):
                feats = json.loads(feats)
            result[row["unique_id"]] = feats or {}
        return result

    async def get_entity(self, entity_id: str) -> Optional[EntityRegistryRecord]:
        """Carrega a linha canônica de `entity_registry` por `entity_id`
        (QID Wikidata ou `dgb_<ulid>`). Retorna None quando não existe.

        `aliases` (JSONB) é desserializado de str → list[str] (asyncpg não tem
        codec de JSONB)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_ENTITY_REGISTRY_SQL, entity_id)
        if row is None:
            return None
        return _row_to_entity_registry(dict(row))

    async def get_entities_batch(
        self, entity_ids: list[str]
    ) -> dict[str, EntityRegistryRecord]:
        """Carrega várias linhas de `entity_registry` numa query.

        Retorna `{entity_id: EntityRegistryRecord}` só para os ids presentes —
        usado para resolver `canonical_name` no modo canônico de
        `entitySuggestions` sem N+1."""
        if not entity_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_ENTITY_REGISTRY_BATCH_SQL, entity_ids)
        result: dict[str, EntityRegistryRecord] = {}
        for r in rows:
            rec = _row_to_entity_registry(dict(r))
            result[rec.entity_id] = rec
        return result

    async def get_related_entities(
        self, entity_id: str, limit: int = 12
    ) -> list[RelatedEntityRecord]:
        """Vizinhos 1-hop de `entity_id` na rede de co-menção (Fase 6c).

        Lê `entity_edges` (kind=co_mention) onde a entidade aparece em qualquer
        ponta, pega a "outra ponta" e junta com `entity_registry` para hidratar
        nome/tipo/wikidata. Ordenado por weight desc; até `limit`. `entity_id`
        sempre parametrizado ($1) — nunca interpolado (anti-injection)."""
        if not entity_id:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_RELATED_ENTITIES_SQL, entity_id, limit)
        return [_row_to_related_entity(dict(r)) for r in rows]

    async def get_entity_network(
        self, entity_id: str, depth: int = 1, limit: int = 50
    ) -> EntityNetworkRecord:
        """Ego-network (nós + arestas) ao redor de `entity_id` (Fase 6c).

        CTE recursiva em `entity_edges` (kind=co_mention), travessia
        não-direcionada até `depth` saltos. `depth` é CLAMPado a [0, 2] aqui
        (profundidades maiores ficam para o Neo4j numa iteração futura). `limit`
        limita o nº de arestas (cap de tamanho do grafo). `entity_id` sempre
        parametrizado ($1) — nunca interpolado (anti-injection). Nós e arestas
        em duas queries que compartilham a mesma CTE."""
        if not entity_id:
            return EntityNetworkRecord(nodes=[], edges=[])
        # Clamp defensivo: depth no máximo 2 (e mínimo 0).
        safe_depth = max(0, min(int(depth), 2))
        async with self._pool.acquire() as conn:
            node_rows = await conn.fetch(
                _ENTITY_NETWORK_NODES_SQL, entity_id, safe_depth
            )
            edge_rows = await conn.fetch(
                _ENTITY_NETWORK_EDGES_SQL, entity_id, safe_depth, limit
            )
        return EntityNetworkRecord(
            nodes=[_row_to_network_node(dict(r)) for r in node_rows],
            edges=[_row_to_network_edge(dict(r)) for r in edge_rows],
        )

    async def get_news_for_typesense(self, unique_id: str) -> Optional[TypesenseDocRecord]:
        query = _NEWS_TYPESENSE_SQL + " WHERE n.unique_id = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, unique_id)
        if row is None:
            return None
        return _row_to_typesense_doc(dict(row))

    async def get_news_batch_for_bigquery(
        self,
        start_date: str,
        end_date: str,
        batch_size: int = 100,
        cursor: str | None = None,
    ) -> list[BigQueryRecord]:
        if cursor:
            query = _NEWS_BIGQUERY_SQL + " AND n.unique_id > $3 ORDER BY n.unique_id LIMIT $4"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, start_date, end_date, cursor, batch_size)
        else:
            query = _NEWS_BIGQUERY_SQL + " ORDER BY n.unique_id LIMIT $3"
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, start_date, end_date, batch_size)
        return [_row_to_bigquery_record(dict(r)) for r in rows]

    async def get_similar_articles(
        self,
        unique_id: str,
        threshold: float = 0.8,
        limit: int = 5,
    ) -> list[SimilarArticleRecord]:
        async with self._pool.acquire() as conn:
            # Embedding do artigo base (lookup indexado por unique_id), lido como
            # texto pgvector ('[...]'); reusado como literal `$1::vector` para
            # que o ORDER BY use o índice HNSW. None = artigo sem embedding.
            embedding = await conn.fetchval(
                "SELECT content_embedding::text FROM news WHERE unique_id = $1",
                unique_id,
            )
            if embedding is None:
                return []
            rows = await conn.fetch(_SIMILAR_ARTICLES_SQL, embedding, unique_id, limit)
        records = [_row_to_similar_article(dict(r)) for r in rows]
        # Threshold em Python: o resultado já vem nearest-first (HNSW), então
        # cortar abaixo do threshold preserva o contrato (top-N acima do limiar).
        return [r for r in records if r.similarity > threshold]

    async def get_integrity_batch(
        self, batch_size: int = 50
    ) -> list[IntegrityCandidateRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_INTEGRITY_BATCH_SQL, batch_size)
        return [_row_to_integrity_candidate(dict(r)) for r in rows]

    async def upsert_features(self, unique_id: str, features: dict) -> bool:
        features_json = json.dumps(features)
        result = await self._pool.execute(
            _UPSERT_FEATURES_SQL, unique_id, features_json
        )
        # asyncpg execute returns a command tag like "INSERT 0 1"
        return result is not None

    async def batch_upsert_features(
        self, items: list[tuple[str, dict]]
    ) -> tuple[int, int]:
        if not items:
            return (0, 0)
        processed = 0
        failed = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for unique_id, features in items:
                    try:
                        features_json = json.dumps(features)
                        await conn.execute(
                            _UPSERT_FEATURES_SQL, unique_id, features_json
                        )
                        processed += 1
                    except Exception:
                        failed += 1
        return (processed, failed)

    async def agency_analytics(
        self,
        granularity: str,
        agencies: list[str],
        date_from: str,
        date_to: str,
    ) -> list[dict]:
        date_from_d = date.fromisoformat(date_from)
        date_to_d = date.fromisoformat(date_to)
        async with self._pool.acquire() as conn:
            if granularity == "day":
                rows = await conn.fetch(
                    _AGENCY_ANALYTICS_DAY_SQL,
                    agencies,
                    date_from_d,
                    date_to_d,
                )
            else:
                rows = await conn.fetch(
                    _AGENCY_ANALYTICS_SQL,
                    granularity,
                    agencies,
                    date_from_d,
                    date_to_d,
                )
        return [dict(r) for r in rows]

    async def entity_coverage(
        self,
        entity_id: str,
        granularity: str = "month",
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Série temporal de cobertura de uma entidade por agência e período.

        Parâmetros:
        - `entity_id`: QID Wikidata ou `dgb_<ulid>` da entidade.
        - `granularity`: resolução temporal — `day`, `week` ou `month` (padrão).
        - `date_from`/`date_to`: filtros de data ISO 8601 (None = sem limite).

        Retorna lista de dicts com chaves: `period`, `agency_key`, `agency_name`,
        `article_count`, `total_mentions`, `avg_sentiment_score`.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _ENTITY_COVERAGE_SQL,
                granularity,
                entity_id,
                date.fromisoformat(date_from) if date_from else None,
                date.fromisoformat(date_to) if date_to else None,
            )
        return [dict(r) for r in rows]

    async def entity_search(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Busca fuzzy de entidades por nome ou alias.

        UNION entre match exato no `entity_alias` (alias_norm) e match por
        trigrama (pg_trgm %) no `canonical_name` de `entity_registry`.
        `aliases` (JSONB) é desserializado de str → list[str].

        Parâmetros:
        - `query`: texto de busca (normalizado internamente pelo SQL via unaccent).
        - `entity_type`: filtra por tipo (`ORG`, `PER`, `LOC`, etc.). None = todos.
        - `limit`: número máximo de resultados (padrão 5; clampad a 20 no resolver).

        Retorna lista de dicts com chaves: `entity_id`, `canonical_name`, `type`,
        `description`, `wikidata_url`, `agency_key`, `aliases`, `article_count`,
        `confidence`, `match_type`.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_ENTITY_SEARCH_SQL, query, entity_type, limit)
        results = []
        for r in rows:
            row = dict(r)
            aliases_raw = row.get("aliases", "[]")
            if isinstance(aliases_raw, str):
                try:
                    row["aliases"] = json.loads(aliases_raw)
                except Exception:
                    row["aliases"] = []
            results.append(row)
        return results

    async def get_trending_entities(self, limit: int = 10) -> list[dict]:
        """Retorna entidades NER com maior trending score (pré-computado pelo DAG)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_TRENDING_ENTITIES_SQL, limit)
        return [dict(r) for r in rows]

    async def get_entity_articles(
        self,
        entity_id: str,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[NewsRecord], int]:
        """Artigos de uma entidade canônica via `news_entities` (Postgres direto).

        Retorna `(records, total_count)`. Deduplica por `unique_id` (news_entities
        pode ter múltiplas linhas por artigo). Ordenado por `published_at DESC`.
        Usa subquery para o total sem janelas desnecessárias."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_ENTITY_ARTICLES_SQL, entity_id, limit, offset)
        if not rows:
            return [], 0
        records = [_row_to_news_record(dict(r)) for r in rows]
        total = int(rows[0]["total_count"])
        return records, total
