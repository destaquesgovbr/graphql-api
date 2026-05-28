"""Lifespan FastAPI: wiring real dos datasources + cache JWKS.

Razao: ate a Fase R1 o `graphql_api` tinha o esqueleto pronto
(Strawberry + FastAPI + SSE) mas `get_context()` retornava `GraphQLContext()`
vazio em producao. Resolvers explodiam com `'NoneType' object has no
attribute 'search_articles'` em qualquer query que tocasse Typesense /
Firestore / Postgres.

Este modulo concentra a inicializacao dos quatro datasources a partir de
env vars setadas via Terraform no Cloud Run (ver `infra/terraform/graphql-api.tf`):

| Env var                | Datasource                  |
| ---------------------- | --------------------------- |
| `TYPESENSE_READ_CONN`  | `TypesenseDatasource`       |
| `TYPESENSE_WRITE_CONN` | `TypesenseAdminDatasource`  |
| `DATABASE_URL`         | `PostgresDatasource`        |
| `GCP_PROJECT_ID`       | `FirestoreDatasource` (ADC) |

Falhas individuais NAO quebram o startup: cada DS faz `try/except` na
construcao e loga warning. Isso permite que o app suba em dev local sem
todas as integracoes — resolvers que dependam de um DS=None devem tratar
com erro de runtime apropriado (ja eh o comportamento dos resolvers existentes
quando o context nao tem o DS injetado).

Tudo fica em `app.state.*` (nao globals) — reuso entre requests, isolamento
por instancia do FastAPI (importante para testes que criam multiplos apps).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from graphql_api.datasources.firestore import FirestoreDatasource
from graphql_api.datasources.postgres import PostgresDatasource
from graphql_api.datasources.typesense import TypesenseDatasource
from graphql_api.datasources.typesense_admin import TypesenseAdminDatasource

logger = logging.getLogger(__name__)


async def _prefetch_jwks(jwks_url: str) -> dict | None:
    """Faz um warm-up do JWKS no startup. Falha silenciosa.

    O cache em si vive dentro do `verify_jwt` (que refetcha por chamada). Este
    prefetch eh apenas para detectar problemas de conectividade cedo (log no
    startup, nao no primeiro request).
    """
    try:
        from graphql_api.auth.jwt import _fetch_jwks

        return await _fetch_jwks(jwks_url)
    except Exception as exc:
        logger.warning("falha ao pre-fetchar JWKS de %s: %s", jwks_url, exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown do FastAPI.

    Startup:
      - Instancia datasources a partir de env vars (None se ausente/falhar).
      - Pre-fetcha JWKS (warm-up; nao bloqueia se falhar).
    Shutdown:
      - Fecha o pool asyncpg se aplicavel.
    """
    # ----- Startup -----
    app.state.typesense_ds = TypesenseDatasource.from_env("TYPESENSE_READ_CONN")
    if app.state.typesense_ds is None:
        logger.warning("TYPESENSE_READ_CONN ausente/invalido — TypesenseDatasource=None")

    app.state.typesense_admin_ds = TypesenseAdminDatasource.from_env("TYPESENSE_WRITE_CONN")
    if app.state.typesense_admin_ds is None:
        logger.warning("TYPESENSE_WRITE_CONN ausente/invalido — TypesenseAdminDatasource=None")

    app.state.firestore_ds = FirestoreDatasource.from_env()
    if app.state.firestore_ds is None:
        logger.warning("FirestoreDatasource=None (sem GCP_PROJECT_ID/ADC)")

    app.state.postgres_ds = await PostgresDatasource.from_env("DATABASE_URL")
    if app.state.postgres_ds is None:
        logger.warning("DATABASE_URL ausente/invalido — PostgresDatasource=None")

    # JWKS warm-up
    jwks_url = os.environ.get("AUTH_JWKS_URL")
    if jwks_url:
        await _prefetch_jwks(jwks_url)
    else:
        logger.warning("AUTH_JWKS_URL ausente — validacao JWT desabilitada")

    logger.info(
        "lifespan startup: typesense=%s typesense_admin=%s firestore=%s postgres=%s",
        app.state.typesense_ds is not None,
        app.state.typesense_admin_ds is not None,
        app.state.firestore_ds is not None,
        app.state.postgres_ds is not None,
    )

    try:
        yield
    finally:
        # ----- Shutdown -----
        postgres_ds = getattr(app.state, "postgres_ds", None)
        if postgres_ds is not None and hasattr(postgres_ds, "close"):
            try:
                await postgres_ds.close()
            except Exception as exc:  # pragma: no cover - defensivo
                logger.warning("erro fechando postgres pool: %s", exc)
