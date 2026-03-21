import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jwcrypto import jwk as jwcrypto_jwk


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
