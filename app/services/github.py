import httpx

from app.core.exceptions import UnauthorizedError

GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


async def exchange_code_for_token(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> str:
    async with httpx.AsyncClient() as http:
        response = await http.post(
            GITHUB_TOKEN_URL,
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    data = response.json()
    if "access_token" not in data:
        raise UnauthorizedError("GitHub OAuth failed: invalid or expired code")
    return data["access_token"]


async def fetch_github_profile(access_token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient() as http:
        user_resp = await http.get(GITHUB_USER_URL, headers=headers)
        user_data = user_resp.json()

        if not user_data.get("email"):
            emails_resp = await http.get(GITHUB_EMAILS_URL, headers=headers)
            primary = next(
                (
                    e["email"]
                    for e in emails_resp.json()
                    if e["primary"] and e["verified"]
                ),
                None,
            )
            user_data["email"] = primary

    return user_data
