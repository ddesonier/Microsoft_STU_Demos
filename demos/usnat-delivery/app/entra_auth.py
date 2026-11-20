"""
Entra ID (Azure AD) sign-in for the /admin section of webapp.py.

Protects /admin with an interactive login (OAuth 2.0 authorization code flow
via MSAL), restricted to users explicitly assigned to the
"msdocs-search-app-admin" Entra app registration. appRoleAssignmentRequired
is enabled on that app's service principal, so Entra itself rejects
unassigned users at sign-in -- this module never has to check roles/groups
in code, it just has to require *some* successful sign-in.

This is a different concern from DefaultAzureCredential used elsewhere in
this project: DefaultAzureCredential authenticates *this app* to Azure
resources (Search, OpenAI, Storage); this module authenticates *a human* to
this app's /admin pages, which (unlike managed-identity resource access)
requires a confidential-client secret -- there's no way around that for an
interactive browser sign-in flow.

Env vars (see .env.local.example for local dev; set as App Service
Application Settings in production -- ideally ENTRA_CLIENT_SECRET as a Key
Vault reference rather than a plain setting):
  ENTRA_TENANT_ID       Directory (tenant) ID
  ENTRA_CLIENT_ID       Application (client) ID of the admin app registration
  ENTRA_CLIENT_SECRET   Client secret for that app registration
  FLASK_SECRET_KEY      Random key Flask uses to sign the session cookie
"""

import os
import uuid
from functools import wraps

import msal
from flask import redirect, request, session, url_for

TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET", "")
AUTHORITY_HOST = os.environ.get(
    "ENTRA_AUTHORITY_HOST", ""
).rstrip("/")
AUTHORITY = f"{AUTHORITY_HOST}/{TENANT_ID}"

# "User.Read" is the default delegated Graph permission every app gets
# without needing admin consent. We don't call Graph with it -- we only
# need the ID token's claims -- but MSAL's auth code flow requires at least
# one scope, and this is the conventional choice (matches Microsoft's own
# Flask quickstart) so it never triggers an extra consent prompt.
SCOPES = ["User.Read"]


def configured() -> bool:
    return bool(TENANT_ID and CLIENT_ID and CLIENT_SECRET and AUTHORITY_HOST)


def _msal_app() -> msal.ConfidentialClientApplication:
    if not configured():
        raise RuntimeError(
            "Entra auth isn't configured -- set ENTRA_TENANT_ID, ENTRA_CLIENT_ID, "
            "and ENTRA_CLIENT_SECRET (see .env.local.example)."
        )
    return msal.ConfidentialClientApplication(
        CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET
    )


def build_auth_url() -> str:
    """Start sign-in: stash a CSRF state value and return the Microsoft login URL."""
    session["auth_state"] = str(uuid.uuid4())
    return _msal_app().get_authorization_request_url(
        SCOPES,
        state=session["auth_state"],
        redirect_uri=url_for("auth_callback", _external=True),
    )


def acquire_token_by_auth_code(code: str) -> dict:
    return _msal_app().acquire_token_by_authorization_code(
        code, scopes=SCOPES, redirect_uri=url_for("auth_callback", _external=True)
    )


def handle_callback(args) -> str | None:
    """Validate the redirect from Microsoft login and, on success, store the
    signed-in user's claims in the session. Returns an error message string
    on failure, or None on success."""
    if "error" in args:
        return args.get("error_description", args["error"])
    if args.get("state") != session.pop("auth_state", None):
        return "Login request expired or was tampered with (state mismatch). Please try again."

    code = args.get("code")
    if not code:
        return "Missing authorization code in the response from Microsoft login."

    result = acquire_token_by_auth_code(code)
    if "error" in result:
        return result.get("error_description", result["error"])

    claims = result.get("id_token_claims", {})
    session["user"] = {
        "name": claims.get("name"),
        "preferred_username": claims.get("preferred_username"),
    }
    return None


def pop_post_login_redirect() -> str | None:
    """Return (and clear) the URL login_required stashed before redirecting
    to sign-in, so auth_callback can send the user back where they started."""
    return session.pop("post_login_redirect", None)


def logout() -> str:
    """Clear the local session and return the Entra logout URL to redirect to."""
    session.clear()
    return (
        f"{AUTHORITY}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={url_for('index', _external=True)}"
    )


def current_user() -> dict | None:
    return session.get("user")


def login_required(view):
    """Redirect to sign-in if there's no authenticated user in the session yet."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            session["post_login_redirect"] = request.url
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped
