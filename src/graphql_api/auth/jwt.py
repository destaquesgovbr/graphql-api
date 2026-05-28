import logging
from typing import Optional

import httpx
import jwt

from graphql_api.context import User

logger = logging.getLogger(__name__)


async def _fetch_jwks(jwks_url: str) -> dict:
    """Fetch the JWKS document from the given URL."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()


def _extract_roles(payload: dict) -> list[str]:
    """Coleta roles do payload do JWT.

    Suporta três formatos:
    - `roles: [...]` (custom / nosso encoding antigo).
    - `realm_access.roles: [...]` (Keycloak — roles do realm).
    - `resource_access.<client_id>.roles: [...]` (Keycloak — roles por client).

    Os três sao mesclados (set-union, ordem preservada via dedupe O(n)). Em
    Keycloak, o realm `destaquesgovbr` populamos `realm_access.roles`.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(values):
        if not isinstance(values, list):
            return
        for v in values:
            if isinstance(v, str) and v not in seen:
                seen.add(v)
                out.append(v)

    _add(payload.get("roles"))

    realm_access = payload.get("realm_access") or {}
    if isinstance(realm_access, dict):
        _add(realm_access.get("roles"))

    resource_access = payload.get("resource_access") or {}
    if isinstance(resource_access, dict):
        for client_data in resource_access.values():
            if isinstance(client_data, dict):
                _add(client_data.get("roles"))

    return out


async def verify_jwt(
    token: str | None,
    jwks_url: str,
    *,
    issuer: Optional[str] = None,
) -> Optional[User]:
    """Verify a JWT token using JWKS and return a User or None.

    Valida `exp` e `sub` sempre. Quando `issuer` for fornecido, exige `iss`
    presente e igual (PyJWT faz a comparacao internamente via `issuer=`).
    `aud` nao e validado (multi-client; cada client tem audience proprio).
    Retorna None para qualquer token invalido/ausente.
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

        require_claims = ["exp", "sub"]
        decode_kwargs: dict = {
            "algorithms": ["RS256"],
            "options": {"require": require_claims, "verify_aud": False},
        }
        if issuer:
            decode_kwargs["issuer"] = issuer
            decode_kwargs["options"]["require"].append("iss")

        payload = jwt.decode(token, signing_key, **decode_kwargs)

        return User(
            id=payload["sub"],
            email=payload.get("email", ""),
            roles=_extract_roles(payload),
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
