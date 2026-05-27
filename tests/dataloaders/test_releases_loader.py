"""Testes do dataloader de releases (Fase A5).

Garante batching por `(clipping_id, limit, before)` e ausencia de N+1 quando
varios clippings sao listados com `releases` selecionado.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from graphql_api.dataloaders import create_releases_loader
from graphql_api.datasources.firestore import ReleaseData

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_release(release_id: str, clipping_id: str = "clip-1") -> ReleaseData:
    return ReleaseData(
        id=release_id,
        clipping_id=clipping_id,
        clipping_name="Meu Clipping",
        digest_html="<p>x</p>",
        articles_count=1,
        created_at=NOW,
        release_url=f"/clipping/release/{release_id}",
        ref_time=NOW,
        since_hours=24,
    )


class TestReleasesLoader:
    @pytest.mark.asyncio
    async def test_loader_returns_list_for_each_key(self):
        ds = MagicMock()
        ds.get_releases_batch.return_value = {
            "clip-1": [_make_release("rel-1")],
            "clip-2": [_make_release("rel-2", "clip-2")],
        }
        loader = create_releases_loader(ds)
        results = await loader.load_many(
            [("clip-1", 20, None), ("clip-2", 20, None)]
        )
        assert len(results) == 2
        assert [r.id for r in results[0]] == ["rel-1"]
        assert [r.id for r in results[1]] == ["rel-2"]

    @pytest.mark.asyncio
    async def test_loader_batches_single_call_for_same_pagination(self):
        """5 clippings com mesmo (limit, before) -> 1 chamada ao ds."""
        ds = MagicMock()
        ds.get_releases_batch.return_value = {
            f"clip-{i}": [_make_release(f"rel-{i}", f"clip-{i}")] for i in range(5)
        }
        loader = create_releases_loader(ds)
        await loader.load_many([(f"clip-{i}", 20, None) for i in range(5)])
        assert ds.get_releases_batch.call_count == 1

    @pytest.mark.asyncio
    async def test_loader_separate_batches_when_pagination_differs(self):
        """Keys com `limit` diferentes geram 2 chamadas ao ds."""
        ds = MagicMock()
        ds.get_releases_batch.side_effect = lambda clipping_ids, limit, before: {
            cid: [] for cid in clipping_ids
        }
        loader = create_releases_loader(ds)
        await loader.load_many([("clip-1", 5, None), ("clip-2", 20, None)])
        assert ds.get_releases_batch.call_count == 2

    @pytest.mark.asyncio
    async def test_loader_returns_empty_list_for_missing_clipping(self):
        ds = MagicMock()
        ds.get_releases_batch.return_value = {"clip-1": [], "clip-2": []}
        loader = create_releases_loader(ds)
        results = await loader.load_many([("clip-1", 20, None), ("clip-2", 20, None)])
        assert results == [[], []]
