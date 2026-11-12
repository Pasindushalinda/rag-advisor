"""Aegra authentication: Open WebUI authenticates with a shared service key, admins with a separate key."""

import hmac

from langgraph_sdk import Auth
from pydantic import BaseModel, ConfigDict

from shared.config import get_settings

auth = Auth()

OPEN_WEBUI_USER_HEADER = "x-openwebui-user-id"
SERVICE_IDENTITY = "open-webui"
ADMIN_IDENTITY = "admin"
ADMIN_PERMISSION = "admin"


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: str
    permissions: list[str] = []


def _matches(token: str, expected: str) -> bool:
    # an unset key must never authenticate (compare_digest("", "") is True)
    return bool(expected) and hmac.compare_digest(token.encode(), expected.encode())


def _unauthorized(detail: str) -> Auth.exceptions.HTTPException:
    return Auth.exceptions.HTTPException(status_code=401, detail=detail)


@auth.authenticate
async def authenticate(headers: dict[str, str]) -> dict[str, object]:
    lowered = {k.lower(): v for k, v in headers.items()}
    scheme, _, token = lowered.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        token = ""
    # Agent Chat UI (LangGraph SDK) sends its key as X-Api-Key, not as a bearer token, so accept both.
    token = token or lowered.get("x-api-key", "")
    if not token:
        raise _unauthorized("Missing bearer token")

    settings = get_settings()
    if _matches(token, settings.admin_api_key):
        user = AuthenticatedUser(
            identity=ADMIN_IDENTITY, permissions=[ADMIN_PERMISSION]
        )
    elif _matches(token, settings.service_api_key):
        # Open WebUI forwards the signed-in user's id; it is only trusted after the key check.
        user = AuthenticatedUser(
            identity=lowered.get(OPEN_WEBUI_USER_HEADER) or SERVICE_IDENTITY
        )
    else:
        raise _unauthorized("Invalid token")
    return user.model_dump()
