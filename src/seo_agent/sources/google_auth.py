"""Service-account credentials from GOOGLE_SA_JSON_B64. The secret is never printed."""
import base64
import json

from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPES = (
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
)


def get_token(b64: str | None) -> str:
    if not b64:
        raise RuntimeError("GOOGLE_SA_JSON_B64 is not set")
    try:
        info = json.loads(base64.b64decode(b64))
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except Exception as e:  # do not echo exception text: it may quote the secret
        raise RuntimeError(f"GOOGLE_SA_JSON_B64 is invalid ({type(e).__name__})") from None
    creds.refresh(Request())
    return creds.token
