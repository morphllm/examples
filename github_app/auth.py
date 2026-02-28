"""GitHub App authentication: JWT generation and installation token caching."""

import time
from datetime import datetime, timezone

import httpx
import jwt

# Cache: {installation_id: (token, expires_at_timestamp)}
_token_cache: dict[int, tuple[str, float]] = {}

GITHUB_API = "https://api.github.com"


def generate_jwt(app_id: str, private_key_pem: str) -> str:
    """Generate a JWT for GitHub App authentication (RS256, 10 min TTL)."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued at, 60s in the past for clock drift
        "exp": now + 600,  # 10 minute expiry
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def get_installation_token(
    app_id: str, installation_id: int, private_key_pem: str
) -> str:
    """Get an installation access token, using cache if valid."""
    cached = _token_cache.get(installation_id)
    if cached:
        token, expires_at = cached
        # Return cached if more than 60s remaining
        if time.time() < expires_at - 60:
            return token

    app_jwt = generate_jwt(app_id, private_key_pem)
    resp = httpx.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    token = data["token"]
    expires_at_str = data["expires_at"]  # ISO 8601
    expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00")).timestamp()

    _token_cache[installation_id] = (token, expires_at)
    return token
