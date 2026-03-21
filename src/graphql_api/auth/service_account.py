import logging
from typing import Optional

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from graphql_api.context import ServiceAccount

logger = logging.getLogger(__name__)


async def verify_service_account(token: str | None, audience: str) -> Optional[ServiceAccount]:
    """Verify a Google OIDC token and return a ServiceAccount or None.

    Uses google.oauth2.id_token.verify_oauth2_token for validation.
    """
    if token is None:
        return None

    try:
        id_info = id_token.verify_oauth2_token(token, google_requests.Request(), audience)

        email = id_info.get("email", "")
        return ServiceAccount(email=email)

    except ValueError as e:
        logger.debug("OIDC token verification failed: %s", e)
        return None
    except Exception as e:
        logger.warning("OIDC verification error: %s", e)
        return None
