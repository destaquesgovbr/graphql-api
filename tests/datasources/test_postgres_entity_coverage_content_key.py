"""entityCoverage sem depender da ordem de deploy da migração 035 (GQL-1b).

`_ENTITY_COVERAGE_SQL` lê `n.content_key` sempre que calcula a janela do dedup,
mesmo quando o cliente não pede `uniqueContentCount`. Sem a coluna, a query
inteira falharia, e com ela os tools `get_entity_profile` e
`get_policy_lifecycle` do gobus. O datasource sonda a coluna (cache com TTL) e
cai na chave por hash exato enquanto ela não existe. Se a coluna sumir com o
cache dizendo que existe (rollback da 035), o erro 42703 é pego, a query é
refeita com a chave por hash e o cache passa a dizer que não existe.
"""

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from graphql_api.datasources.postgres import (
    _CONTENT_DEDUP_KEY_SQL,
    _CONTENT_DEDUP_KEY_SQL_SEM_CONTENT_KEY,
    _CONTENT_KEY_PROBE_TTL_S,
    _ENTITY_COVERAGE_SQL,
    _ENTITY_COVERAGE_SQL_SEM_CONTENT_KEY,
    _NEWS_HAS_CONTENT_KEY_SQL,
    PostgresDatasource,
)

ROW = {
    "period": "2026-10-01 00:00:00+00",
    "agency_key": "mec",
    "agency_name": "Ministério da Educação",
    "article_count": 2,
    "unique_content_count": 1,
    "total_mentions": 3,
    "avg_sentiment_score": None,
}


def _flat(sql: str) -> str:
    return " ".join(sql.split())


class _Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _make_pool(*, has_column, fetch_side_effect=None):
    """`has_column`: retorno da sonda (`conn.fetchval`), um valor ou lista em
    sequência. `fetch_side_effect`: efeitos de `conn.fetch` em sequência
    (padrão: sempre `[ROW]`)."""
    pool = MagicMock()
    conn = AsyncMock()
    if isinstance(has_column, list):
        conn.fetchval = AsyncMock(side_effect=has_column)
    else:
        conn.fetchval = AsyncMock(return_value=has_column)
    if fetch_side_effect is None:
        conn.fetch = AsyncMock(return_value=[ROW])
    else:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    return pool, conn


def _sqls(conn) -> list[str]:
    return [c.args[0] for c in conn.fetch.await_args_list]


class TestSqlSemContentKey:
    def test_chave_legada_e_o_hash_exato(self):
        assert _CONTENT_DEDUP_KEY_SQL_SEM_CONTENT_KEY == "COALESCE(n.content_hash, ne.unique_id)"

    def test_sql_legado_so_troca_a_chave(self):
        # Mesmo SQL, com a chave por hash no lugar da chave com content_key:
        # nada além da chave pode divergir entre os dois caminhos.
        assert _ENTITY_COVERAGE_SQL_SEM_CONTENT_KEY == _ENTITY_COVERAGE_SQL.replace(
            _CONTENT_DEDUP_KEY_SQL, _CONTENT_DEDUP_KEY_SQL_SEM_CONTENT_KEY
        )
        assert "content_key" not in _ENTITY_COVERAGE_SQL_SEM_CONTENT_KEY
        assert "content_key" in _ENTITY_COVERAGE_SQL

    def test_sonda_procura_news_content_key_no_catalogo(self):
        sql = _flat(_NEWS_HAS_CONTENT_KEY_SQL)
        assert sql.startswith("SELECT EXISTS")
        assert "pg_attribute" in sql
        # Mesma resolução de `news` pelo search_path que as queries usam.
        assert "attrelid = to_regclass('news')" in sql
        assert "attname = 'content_key'" in sql
        assert "NOT attisdropped" in sql

    def test_ttl_de_cinco_minutos(self):
        assert _CONTENT_KEY_PROBE_TTL_S == 300


class TestEntityCoverageEscolheAChave:
    @pytest.mark.asyncio
    async def test_com_a_coluna_usa_content_key(self):
        pool, conn = _make_pool(has_column=True)
        ds = PostgresDatasource(pool, clock=_Clock())

        rows = await ds.entity_coverage("Q1", "day", "2026-10-01", "2026-10-08")

        assert rows == [ROW]
        conn.fetchval.assert_awaited_once_with(_NEWS_HAS_CONTENT_KEY_SQL)
        assert _sqls(conn) == [_ENTITY_COVERAGE_SQL]

    @pytest.mark.asyncio
    async def test_sem_a_coluna_cai_no_hash_sem_erro(self):
        # Merge do GQL-1b antes da 035: o entityCoverage não quebra.
        pool, conn = _make_pool(has_column=False)
        ds = PostgresDatasource(pool, clock=_Clock())

        rows = await ds.entity_coverage("Q1", "day", "2026-10-01", "2026-10-08")

        assert rows == [ROW]
        assert _sqls(conn) == [_ENTITY_COVERAGE_SQL_SEM_CONTENT_KEY]
        assert conn.fetch.await_args.args[1:3] == ("day", "Q1")

    @pytest.mark.asyncio
    async def test_sonda_uma_vez_dentro_do_ttl(self):
        clock = _Clock()
        pool, conn = _make_pool(has_column=True)
        ds = PostgresDatasource(pool, clock=clock)

        await ds.entity_coverage("Q1")
        clock.now += _CONTENT_KEY_PROBE_TTL_S - 1
        await ds.entity_coverage("Q2")

        assert conn.fetchval.await_count == 1
        assert _sqls(conn) == [_ENTITY_COVERAGE_SQL, _ENTITY_COVERAGE_SQL]

    @pytest.mark.asyncio
    async def test_coluna_criada_depois_entra_em_uso_apos_o_ttl(self):
        # 035 aplicada com o serviço no ar: sem novo deploy, a chave nova
        # passa a valer na primeira consulta depois do TTL.
        clock = _Clock()
        pool, conn = _make_pool(has_column=[False, True])
        ds = PostgresDatasource(pool, clock=clock)

        await ds.entity_coverage("Q1")
        clock.now += _CONTENT_KEY_PROBE_TTL_S
        await ds.entity_coverage("Q1")

        assert conn.fetchval.await_count == 2
        assert _sqls(conn) == [_ENTITY_COVERAGE_SQL_SEM_CONTENT_KEY, _ENTITY_COVERAGE_SQL]


class TestEntityCoverageColunaRemovida:
    @pytest.mark.asyncio
    async def test_coluna_removida_com_cache_positivo_refaz_com_hash(self):
        # Rollback da 035 (DROP COLUMN) com o GQL-1b no ar e o cache ainda
        # dizendo que a coluna existe: refaz com a chave por hash, sem erro.
        erro = asyncpg.exceptions.UndefinedColumnError("column n.content_key does not exist")
        pool, conn = _make_pool(has_column=True, fetch_side_effect=[erro, [ROW], [ROW]])
        ds = PostgresDatasource(pool, clock=_Clock())

        rows = await ds.entity_coverage("Q1", "day", "2026-10-01", "2026-10-08")

        assert rows == [ROW]
        assert _sqls(conn) == [_ENTITY_COVERAGE_SQL, _ENTITY_COVERAGE_SQL_SEM_CONTENT_KEY]
        assert conn.fetch.await_args_list[0].args[1:] == conn.fetch.await_args_list[1].args[1:]

        # O cache passa a dizer que a coluna não existe: a próxima consulta,
        # dentro do TTL, vai direto pela chave por hash, sem sondar de novo.
        await ds.entity_coverage("Q1")
        assert conn.fetchval.await_count == 1
        assert _sqls(conn)[-1] == _ENTITY_COVERAGE_SQL_SEM_CONTENT_KEY

    @pytest.mark.asyncio
    async def test_outros_erros_propagam_sem_refazer(self):
        erro = asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")
        pool, conn = _make_pool(has_column=True, fetch_side_effect=[erro])
        ds = PostgresDatasource(pool, clock=_Clock())

        with pytest.raises(asyncpg.exceptions.QueryCanceledError):
            await ds.entity_coverage("Q1")

        assert conn.fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_42703_no_sql_legado_propaga(self):
        # Coluna ausente que não é a content_key: o SQL legado também falha, e
        # o erro sobe em vez de entrar em laço.
        erro = asyncpg.exceptions.UndefinedColumnError("column n.agency_key does not exist")
        pool, conn = _make_pool(has_column=False, fetch_side_effect=[erro])
        ds = PostgresDatasource(pool, clock=_Clock())

        with pytest.raises(asyncpg.exceptions.UndefinedColumnError):
            await ds.entity_coverage("Q1")

        assert conn.fetch.await_count == 1
