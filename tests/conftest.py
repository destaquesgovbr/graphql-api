from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from graphql_api.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers para mocks Firestore (Fase A1)
# ---------------------------------------------------------------------------
_DEFAULT_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_firestore_clipping_doc_camel(**overrides) -> dict:
    """Doc Firestore representativo em camelCase (formato producao).

    Use em testes novos que validem o boundary camelCase. Mantenha campos
    extras (nextRunAt, schedule, extraEmails) fora — a partir de A4 serao
    parte do modelo canonico.
    """
    base = {
        "name": "Meu Clipping",
        "description": "Descricao",
        "recortes": [
            {
                "id": "r1",
                "title": "Economia",
                "themes": ["economia"],
                "agencies": ["agencia-brasil"],
                "keywords": ["pib"],
            }
        ],
        "prompt": "Resuma as noticias",
        "scheduleTime": "08:00",
        "deliveryChannels": {"email": True, "telegram": False, "push": False},
        "active": True,
        "createdAt": _DEFAULT_NOW,
        "updatedAt": _DEFAULT_NOW,
    }
    base.update(overrides)
    return base


def make_firestore_clipping_doc_snake(**overrides) -> dict:
    """Doc snake_case (formato historico dos mocks).

    Usado para testar compat — `populate_by_name=True` em `ClippingData`
    permite que esses docs continuem parseando."""
    base = {
        "name": "Meu Clipping",
        "description": "Descricao",
        "recortes": [],
        "prompt": "Resuma",
        "schedule_time": "08:00",
        "delivery_channels": {"email": True, "telegram": False, "push": False},
        "active": True,
        "created_at": _DEFAULT_NOW,
        "updated_at": _DEFAULT_NOW,
    }
    base.update(overrides)
    return base
