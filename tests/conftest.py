import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jwcrypto import jwk as jwcrypto_jwk

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


# ---------------------------------------------------------------------------
# Fixtures de JWT (compartilhadas entre tests/auth/ e tests/test_app_lifespan.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rsa_key_pair():
    """Generate an RSA key pair for test JWT signing/verification."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key


@pytest.fixture(scope="session")
def rsa_private_pem(rsa_key_pair):
    return rsa_key_pair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="session")
def jwks_dict(rsa_key_pair):
    """Build a JWKS dict from the RSA public key."""
    public_key = rsa_key_pair.public_key()
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key = jwcrypto_jwk.JWK.from_pem(pub_pem)
    key_dict = json.loads(key.export_public())
    key_dict["kid"] = "test-key-1"
    key_dict["alg"] = "RS256"
    key_dict["use"] = "sig"
    return {"keys": [key_dict]}


@pytest.fixture
def make_jwt(rsa_private_pem):
    """Factory fixture to create signed JWTs with custom claims."""

    def _make(claims: dict | None = None, headers: dict | None = None) -> str:
        now = datetime.now(tz=timezone.utc)
        default_claims = {
            "sub": "user-123",
            "email": "test@example.com",
            "roles": ["reader"],
            "iss": "https://accounts.example.com",
            "aud": "dgb-api",
            "iat": now,
            "exp": now + timedelta(hours=1),
        }
        if claims:
            default_claims.update(claims)
        default_headers = {"kid": "test-key-1"}
        if headers:
            default_headers.update(headers)
        return jwt.encode(default_claims, rsa_private_pem, algorithm="RS256", headers=default_headers)

    return _make


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
