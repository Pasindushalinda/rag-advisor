"""Aegra authentication: the chat UI authenticates with a shared service key, admins with a separate key."""

import hmac

from langgraph_sdk import Auth
from pydantic import BaseModel, ConfigDict, SecretStr

from shared.config import get_settings

auth = Auth()

SERVICE_IDENTITY = "chat-ui"
ADMIN_IDENTITY = "admin"
ADMIN_PERMISSION = "admin"
INGEST_PERMISSION = "ingest"


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: str
    permissions: list[str] = []


def _matches(token: str, expected: SecretStr | None) -> bool:
    # an unset key must never authenticate (compare_digest("", "") is True)
    secret = expected.get_secret_value() if expected else ""
    return bool(secret) and hmac.compare_digest(token.encode(), secret.encode())


def _unauthorized(detail: str) -> Auth.exceptions.HTTPException:
    return Auth.exceptions.HTTPException(status_code=401, detail=detail)


def _bearer_or_api_key(headers: dict[str, str]) -> str:
    """The key from `Authorization: Bearer` (API clients) or `X-Api-Key` (what Agent Chat UI sends)."""
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return headers.get("x-api-key", "")


@auth.authenticate
async def authenticate(headers: dict[str, str]) -> dict[str, object]:
    lowered = {k.lower(): v for k, v in headers.items()}
    token = _bearer_or_api_key(lowered)
    if not token:
        raise _unauthorized("Missing API key")

    settings = get_settings()
    if _matches(token, settings.admin_api_key):
        user = AuthenticatedUser(identity=ADMIN_IDENTITY, permissions=[ADMIN_PERMISSION])
    elif _matches(token, settings.service_api_key):
        # One shared identity: the chat UI has no per-user login, so all threads are visible to its users.
        user = AuthenticatedUser(identity=SERVICE_IDENTITY, permissions=[INGEST_PERMISSION])
    else:
        raise _unauthorized("Invalid token")
    return user.model_dump()
