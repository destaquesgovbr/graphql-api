import logging
from typing import Optional

import httpx
import jwt
from jwt import PyJWKClient, PyJWK
from jwcrypto import jwk as jwcrypto_jwk

from graphql_api.context import User

logger = logging.getLogger(__name__)


async def _fetch_jwks(jwks_url: str) -> dict:
    """Fetch the JWKS document from the given URL."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()


async def verify_jwt(token: str | None, jwks_url: str) -> Optional[User]:
    """Verify a JWT token using JWKS and return a User or None.

    Validates the exp and iss claims. Returns None for any invalid/missing token.
    """
    if token is None:
        return None

    try:
        jwks_data = await _fetch_jwks(jwks_url)

        # Decode header to find kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # Find matching key in JWKS
        signing_key = None
        for key_data in jwks_data.get("keys", []):
            if key_data.get("kid") == kid:
                signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
                break

        if signing_key is None:
            logger.warning("No matching kid found in JWKS: %s", kid)
            return None

        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            options={"require": ["exp", "iss", "sub"], "verify_aud": False},
        )

        return User(
            id=payload["sub"],
            email=payload.get("email", ""),
            roles=payload.get("roles", []),
        )

    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Invalid JWT: %s", e)
        return None
    except Exception as e:
        logger.warning("JWT verification failed: %s", e)
        return None
