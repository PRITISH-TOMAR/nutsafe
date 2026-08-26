from fastapi import Request
from coreBundle.auth import authenticate


def require_tenant(request: Request) -> int | None:
    api_key = request.headers.get("api-key", "")
    return authenticate(api_key)
