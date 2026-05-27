"""Testes do dataloader de subscriptions (Fase A3).

Garante que `Clipping.mySubscription` seja resolvido em batch — uma única
query Firestore por request, independente do número de clippings na response.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from graphql_api.dataloaders import create_subscription_loader
from graphql_api.datasources.firestore import SubscriptionData

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_sub(user_id: str, clipping_id: str, role: str = "subscriber") -> SubscriptionData:
    return SubscriptionData(
        id=f"sub-{user_id}-{clipping_id}",
        clipping_id=clipping_id,
        user_id=user_id,
        role=role,  # type: ignore
        delivery_channels={"email": True, "telegram": False, "push": False, "webhook": False},
        extra_emails=[],
        webhook_url="",
        active=True,
        subscribed_at=NOW,
    )


class TestSubscriptionLoader:
    @pytest.mark.asyncio
    async def test_loader_returns_subscription_for_each_key(self):
        """Para `(user, clip1), (user, clip2)` → retorna 2 subs alinhadas."""
        ds = MagicMock()
        ds.get_subscriptions_for_user_and_clippings.return_value = {
            "clip-1": _make_sub("user-1", "clip-1", "author"),
            "clip-2": _make_sub("user-1", "clip-2", "subscriber"),
        }
        loader = create_subscription_loader(ds)

        results = await loader.load_many([("user-1", "clip-1"), ("user-1", "clip-2")])

        assert len(results) == 2
        assert results[0] is not None
        assert results[0].clipping_id == "clip-1"
        assert results[0].role == "author"
        assert results[1].clipping_id == "clip-2"
        assert results[1].role == "subscriber"

    @pytest.mark.asyncio
    async def test_loader_returns_none_for_missing_subscription(self):
        ds = MagicMock()
        ds.get_subscriptions_for_user_and_clippings.return_value = {
            "clip-1": _make_sub("user-1", "clip-1", "subscriber"),
        }
        loader = create_subscription_loader(ds)

        results = await loader.load_many([("user-1", "clip-1"), ("user-1", "clip-ghost")])
        assert results[0] is not None
        assert results[1] is None

    @pytest.mark.asyncio
    async def test_loader_batches_single_call_for_many_clippings(self):
        """Carregar 5 clippings → 1 chamada ao datasource (no N+1)."""
        ds = MagicMock()
        ds.get_subscriptions_for_user_and_clippings.return_value = {
            f"clip-{i}": _make_sub("user-1", f"clip-{i}") for i in range(5)
        }
        loader = create_subscription_loader(ds)

        keys = [("user-1", f"clip-{i}") for i in range(5)]
        await loader.load_many(keys)

        # Asserção explícita: 1 chamada de query Firestore.
        assert ds.get_subscriptions_for_user_and_clippings.call_count == 1
        # E recebeu user_id="user-1" e lista de 5 clipping_ids
        args, kwargs = ds.get_subscriptions_for_user_and_clippings.call_args
        # aceita kw ou positional
        called_user = kwargs.get("user_id") or (args[0] if args else None)
        called_clippings = kwargs.get("clipping_ids") or (args[1] if len(args) > 1 else None)
        assert called_user == "user-1"
        assert set(called_clippings) == {f"clip-{i}" for i in range(5)}

    @pytest.mark.asyncio
    async def test_loader_groups_by_user_id(self):
        """Se houver dois usuários no mesmo batch, faz duas chamadas (uma por user)."""
        ds = MagicMock()

        def fake_query(user_id, clipping_ids):
            return {cid: _make_sub(user_id, cid) for cid in clipping_ids}

        ds.get_subscriptions_for_user_and_clippings.side_effect = fake_query
        loader = create_subscription_loader(ds)

        results = await loader.load_many(
            [("user-A", "clip-1"), ("user-B", "clip-2"), ("user-A", "clip-3")]
        )
        assert len(results) == 3
        # 2 chamadas: uma para user-A, uma para user-B
        assert ds.get_subscriptions_for_user_and_clippings.call_count == 2
