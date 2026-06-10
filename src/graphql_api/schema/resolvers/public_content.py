"""Resolvers de conteudo publico (Typesense) — Fase 2A, Stream B3.

Arquivo novo para nao colidir com `articles.py`/`search.py`. Expoe:

- `relatedArticles(uniqueId, limit)` — artigos relacionados por theme-code
  (replica `artigos/[articleId]/actions.ts:getSimilarArticles` do portal,
  NAO a similaridade interna por embedding postgres; nome distinto do
  `similarArticles` interno para evitar colisao no root Query).
- `themeArticleCounts(days, level)` — contagem de artigos por code de tema
  (usa `datasource.theme_counts`).
- `releaseArticles(id)` — artigos de um release (pagina de artigos do release),
  com autorizacao espelhando o resolver `release(id)`.
- `estimateRecorteCount(themes, agencies, keywords, sinceHours)` — estimativa
  real (substitui o mock `clippingEstimate`), replica
  `lib/estimate-recorte-count.ts` do portal.

Convencoes do repo: IDs scalar = String; resolvers podem ser sync; mock de
datasource nos testes.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import strawberry
from strawberry.types import Info

from graphql_api.datasources.typesense import ArticleDocument
from graphql_api.schema.types.article import Article

# Limiar de similaridade (cosine) para "notícias relacionadas" via pgvector.
# Mais baixo que o threshold de clustering das DAGs (0.8) para garantir ~`limit`
# vizinhos; o ORDER BY similarity desc + LIMIT no SQL já prioriza os mais
# próximos, então o threshold só descarta vizinhos claramente não-relacionados.
_RELATED_SIMILARITY_THRESHOLD = 0.6


@strawberry.type
class ThemeCount:
    """Contagem de artigos para um code de tema.

    `label` e best-effort/None — o portal mapeia o label pela config dele
    (`temas` config); o Typesense `group_by` so retorna o code (`group_key`).
    """

    code: str
    label: Optional[str] = None
    count: int = 0


@strawberry.type
class EntityFacet:
    """Sugestão de entidade (valor + nº de artigos) para o typeahead do filtro
    e o header das páginas de entidade.

    Modo texto (default, back-compat): `value` é o texto da menção;
    `entityId`/`label` ficam None. Modo canônico (`type: CANONICAL`): `value` e
    `entityId` são o `canonical_id` (entity_id) e `label` é o `canonical_name`
    resolvido do `entity_registry` (None se a entidade não estiver no registry)."""

    value: str
    count: int
    # Fase 4 (modo canônico): id canônico e nome resolvido. None no modo texto.
    entity_id: Optional[str] = None
    label: Optional[str] = None


@strawberry.type
class EntityNode:
    """Entidade canônica do `entity_registry` (Fase 4 — linked-data).

    Nó cross-artigo: a mesma `Finep` recorrente em milhares de menções resolve
    para uma única linha. `entityId` é o QID Wikidata ("Q216330") quando
    linkado, senão "dgb_<ulid>". Campos de linkagem (`wikidataId`/`wikidataUrl`/
    `description`) são None quando a entidade não foi linkada ao Wikidata;
    `agencyKey` referencia o catálogo `agencies` quando ORG bate."""

    entity_id: str
    canonical_name: Optional[str] = None
    type: Optional[str] = None
    aliases: list[str] = strawberry.field(default_factory=list)
    wikidata_id: Optional[str] = None
    wikidata_url: Optional[str] = None
    description: Optional[str] = None
    agency_key: Optional[str] = None


def _to_graphql_article(doc: ArticleDocument) -> Article:
    """Converte um `ArticleDocument` (datasource) para o tipo Strawberry `Article`,
    populando os 8 campos de tema (este resolver depende deles para similar/release).
    """
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
        theme_1_level_1_code=doc.theme_1_level_1_code,
        theme_1_level_1_label=doc.theme_1_level_1_label,
        theme_1_level_2_code=doc.theme_1_level_2_code,
        theme_1_level_2_label=doc.theme_1_level_2_label,
        theme_1_level_3_code=doc.theme_1_level_3_code,
        theme_1_level_3_label=doc.theme_1_level_3_label,
        most_specific_theme_code=doc.most_specific_theme_code,
        most_specific_theme_label=doc.most_specific_theme_label,
    )


def _published_at_ts(doc: ArticleDocument) -> float:
    """Timestamp (epoch) de `published_at` para ordenacao desc; None -> 0."""
    if doc.published_at is None:
        return 0.0
    return doc.published_at.timestamp()


@strawberry.type
class PublicContentQuery:
    @strawberry.field(
        description=(
            "Artigos relacionados a `uniqueId` por similaridade semântica "
            "(embedding pgvector via news.content_embedding). O SQL exclui o "
            "próprio artigo, aplica threshold de similaridade e ordena por "
            "similaridade desc; os vizinhos são hidratados do índice Typesense "
            "preservando essa ordem. Retorna [] se o artigo não tiver embedding "
            "ou vizinhos. PÚBLICO. (Distinto do `similarArticles` interno, que "
            "devolve apenas unique_id/score.)"
        )
    )
    async def related_articles(
        self,
        info: Info,
        unique_id: str,
        limit: int = 4,
    ) -> list[Article]:
        ctx = info.context
        pg = ctx.postgres_ds
        ts = ctx.typesense_ds
        if pg is None or ts is None:
            return []

        # Sobre-busca (limit + margem) para compensar vizinhos eventualmente
        # ausentes no índice Typesense. O SQL já exclui o próprio artigo,
        # ordena por similaridade desc e respeita o threshold.
        similar = await pg.get_similar_articles(
            unique_id,
            threshold=_RELATED_SIMILARITY_THRESHOLD,
            limit=limit + 5,
        )
        if not similar:
            return []

        ordered_ids = [s.unique_id for s in similar]
        docs_by_id = ts.get_articles_by_ids(ordered_ids)
        ordered_docs = [docs_by_id[i] for i in ordered_ids if i in docs_by_id]
        return [_to_graphql_article(d) for d in ordered_docs[:limit]]

    @strawberry.field(
        description=(
            "Sugestões de entidades (facet) para o filtro de busca e as páginas "
            "de entidade. `type` (ORG/PER/LOC/EVENT/POLICY) restringe ao campo "
            "tipado; ausente usa o campo combinado `entities`. `type: CANONICAL` "
            "ativa o modo canônico: faceta `entity_canonical` e retorna "
            "`{value/entityId = canonical_id, label = canonical_name, count}` "
            "(label resolvido do entity_registry; None se ausente). `query` "
            "filtra por prefixo. Ordenado por nº de artigos desc. PÚBLICO."
        )
    )
    async def entity_suggestions(
        self,
        info: Info,
        query: str = "",
        type: Optional[str] = None,
        limit: int = 10,
    ) -> list[EntityFacet]:
        ctx = info.context
        ds = ctx.typesense_ds
        if ds is None:
            return []
        facets = ds.entity_facets(query=query, entity_type=type, limit=limit)

        # Modo canônico: os valores facetados são canonical_id (entity_id).
        # Resolve canonical_name do entity_registry (Postgres) num batch e
        # popula label/entityId. Sem Postgres, mantém value como fallback (o
        # portal exibe o id) — não-quebrante.
        if (type or "").upper() == "CANONICAL":
            pg = ctx.postgres_ds
            names: dict[str, str] = {}
            if pg is not None and facets:
                records = await pg.get_entities_batch([value for value, _ in facets])
                names = {
                    eid: rec.canonical_name
                    for eid, rec in records.items()
                    if rec.canonical_name
                }
            return [
                EntityFacet(
                    value=value,
                    count=count,
                    entity_id=value,
                    label=names.get(value),
                )
                for value, count in facets
            ]

        return [EntityFacet(value=value, count=count) for value, count in facets]

    @strawberry.field(
        description=(
            "Entidade canônica do entity_registry por `id` (entity_id: QID "
            "Wikidata 'Q216330' ou 'dgb_<ulid>'). None quando não existe. "
            "PÚBLICO."
        )
    )
    async def entity(self, info: Info, id: str) -> Optional[EntityNode]:
        ctx = info.context
        pg = ctx.postgres_ds
        if pg is None:
            return None
        rec = await pg.get_entity(id)
        if rec is None:
            return None
        return EntityNode(
            entity_id=rec.entity_id,
            canonical_name=rec.canonical_name,
            type=rec.type,
            aliases=rec.aliases,
            wikidata_id=rec.wikidata_id,
            wikidata_url=rec.wikidata_url,
            description=rec.description,
            agency_key=rec.agency_key,
        )

    @strawberry.field(
        description=(
            "Contagem de artigos por code de tema (nivel `level`) nos ultimos "
            "`days` dias. `label` e None (o portal mapeia pela config). PUBLICO."
        )
    )
    def theme_article_counts(
        self,
        info: Info,
        days: int = 30,
        level: int = 1,
    ) -> list[ThemeCount]:
        ctx = info.context
        ds = ctx.typesense_ds
        if ds is None:
            return []

        counts = ds.theme_counts(level=level, days=days)
        return [ThemeCount(code=code, label=None, count=count) for code, count in counts]

    @strawberry.field(
        description=(
            "Artigos de um release (pagina de artigos do release). Autorizacao "
            "espelha `release(id)`: PUBLICO se o listing fonte do clipping esta "
            "ativo; caso contrario somente autor ou subscriber. Retorna [] se "
            "negado ou inexistente. Janela temporal [refTime - sinceHours, "
            "refTime] (default ultimas 24h). Para cada recorte: se tem "
            "keywords, uma busca por keyword (q em title,summary) + filtro "
            "(themes OR-levels + agencies + janela); senao, uma busca q=* "
            "filter-only. Une tudo, deduplica por uniqueId, ordena por "
            "publishedAt desc. PUBLICO (sujeito a auth do release)."
        )
    )
    def release_articles(self, info: Info, id: str) -> list[Article]:
        ctx = info.context
        firestore = ctx.firestore_ds
        typesense = ctx.typesense_ds
        if firestore is None or typesense is None:
            return []

        release_data = firestore.get_release(id)
        if release_data is None:
            return []

        # --- Autorizacao espelhando release(id) -----------------------------
        listing = firestore.get_marketplace_listing_for_clipping(
            release_data.clipping_id
        )
        clipping = firestore.get_clipping(release_data.clipping_id)

        if listing is None:
            # Sem listing ativo: so autor ou subscriber. Exige sessao.
            user = getattr(ctx, "user", None)
            if user is None:
                return []
            is_author = (
                clipping is not None
                and clipping.author_user_id is not None
                and clipping.author_user_id == user.id
            )
            if not is_author:
                sub = firestore.get_subscription(user.id, release_data.clipping_id)
                if sub is None:
                    return []

        if clipping is None:
            return []

        # --- Janela temporal [ref_time - since_hours, ref_time] -------------
        ref_time = release_data.ref_time or datetime.now(tz=timezone.utc)
        since_hours = release_data.since_hours or 24
        start = ref_time - timedelta(hours=since_hours)
        start_iso = start.isoformat()
        end_iso = ref_time.isoformat()

        # --- Uniao dos recortes, dedup por unique_id ------------------------
        seen: dict[str, ArticleDocument] = {}

        def _collect(q: str) -> None:
            res = typesense.search_articles(
                page=1,
                limit=100,
                q=q,
                query_by="title,summary" if q != "*" else None,
                themes=themes or None,
                agencies=agencies or None,
                start_date=start_iso,
                end_date=end_iso,
            )
            for doc in res.articles:
                if doc.unique_id not in seen:
                    seen[doc.unique_id] = doc

        for recorte in clipping.recortes:
            themes = recorte.get("themes") or []
            agencies = recorte.get("agencies") or []
            keywords = recorte.get("keywords") or []

            # Sem nenhum filtro o recorte nao contribui (evita varrer todo o
            # indice no intervalo).
            if not themes and not agencies and not keywords:
                continue

            if keywords:
                # Uma busca por keyword (q em title,summary), unindo os hits.
                for kw in keywords:
                    _collect(kw)
            else:
                # Sem keywords: busca filter-only (q=*).
                _collect("*")

        ordered = sorted(seen.values(), key=_published_at_ts, reverse=True)
        return [_to_graphql_article(doc) for doc in ordered]

    @strawberry.field(
        description=(
            "Estima quantos artigos um recorte capturaria nas ultimas "
            "`sinceHours` horas. Replica `lib/estimate-recorte-count.ts`: filtro "
            "= themes OR-levels + agencies OR'd + published_at >= now-sinceHours; "
            "para keywords, conta por keyword (q em title,summary) e retorna o "
            "MAX; sem keywords, uma unica contagem. Substitui o mock "
            "`clippingEstimate`. PUBLICO."
        )
    )
    def estimate_recorte_count(
        self,
        info: Info,
        themes: list[str],
        agencies: list[str],
        keywords: list[str],
        since_hours: int = 24,
    ) -> int:
        ctx = info.context
        ds = ctx.typesense_ds
        if ds is None:
            return 0

        # Sem nenhum filtro -> 0 (espelha `hasFilters` do portal).
        if not themes and not agencies and not keywords:
            return 0

        start = datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)
        start_iso = start.isoformat()

        def _count(q: str) -> int:
            result = ds.search_articles(
                page=1,
                limit=0,
                q=q,
                query_by="title,summary" if q != "*" else None,
                themes=themes or None,
                agencies=agencies or None,
                start_date=start_iso,
            )
            return result.found

        if not keywords:
            return _count("*")

        # MAX across keywords. Cada keyword vira um `q` real sobre title,summary
        # (o datasource agora aceita q/query_by); o filtro estrutural (themes
        # OR-levels + agencies + janela) e o mesmo em todas as buscas. Espelha
        # `lib/estimate-recorte-count.ts` do portal.
        return max(_count(kw) for kw in keywords)
