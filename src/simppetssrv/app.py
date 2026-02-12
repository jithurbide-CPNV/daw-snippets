from __future__ import annotations

import logging
import os
import secrets
from html import escape
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .members import (
    build_member_cookie,
    consume_email_token,
    consume_password_reset_token,
    create_user,
    fetch_user_by_email,
    fetch_user_by_id,
    issue_email_token,
    issue_password_reset_token,
    mark_email_verified,
    parse_member_cookie,
    update_password,
    verify_password,
)
from .snippets import (
    SNIPPET_VISIBILITIES,
    count_private_snippets,
    create_access_request,
    create_snippet,
    delete_snippet,
    fetch_snippet,
    get_request_for_user,
    has_access,
    list_accessible_snippets,
    list_pending_requests,
    list_public_snippets,
    list_snippets_by_ids,
    update_request_status,
    update_snippet,
)

logger = logging.getLogger(__name__)


def create_app(
    *,
    db_url: str | None = None,
    session_secret: str | None = None,
    admin_email: str | None = None,
    admin_password: str | None = None,
    force_https: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="Snippet Vault", version="0.2.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    resolved_force_https = (
        force_https
        if force_https is not None
        else os.getenv("SIMPPETSSRV_FORCE_HTTPS", "true").lower() in {"1", "true", "yes"}
    )

    if resolved_force_https:

        @app.middleware("http")
        async def enforce_https(request: Request, call_next):  # type: ignore[override]
            forwarded_proto = request.headers.get("x-forwarded-proto")
            scheme = (
                forwarded_proto.split(",")[0].strip() if forwarded_proto else request.url.scheme
            )
            if scheme != "https":
                host = request.headers.get("host") or request.url.netloc
                target = f"https://{host}{request.url.path}"
                if request.url.query:
                    target = f"{target}?{request.url.query}"
                return RedirectResponse(url=target, status_code=308)
            return await call_next(request)

    resolved_db_path = _resolve_db_path(db_url)
    app.state.db_path = resolved_db_path

    resolved_secret = (
        session_secret or os.getenv("SIMPPETSSRV_SESSION_SECRET") or secrets.token_urlsafe(40)
    )
    app.state.session_secret = resolved_secret

    _bootstrap_admin_user(resolved_db_path, admin_email, admin_password)

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        member = _get_member(request)
        db_path = _get_db_path(request)
        if member:
            accessible_ids = list_accessible_snippets(db_path, int(member["id"]))
            snippets = list_snippets_by_ids(db_path, accessible_ids)
        else:
            snippets = list_public_snippets(db_path)
        snippets.sort(key=lambda item: item["updated_at"], reverse=True)
        return HTMLResponse(content=_render_home(snippets=snippets, member=member))

    @app.get("/snippets/new", response_class=HTMLResponse)
    async def new_snippet_form(member: dict[str, Any] = Depends(_require_member)) -> HTMLResponse:
        return HTMLResponse(content=_render_snippet_form(member, errors=None))

    @app.post("/snippets/new")
    async def create_snippet_route(
        request: Request,
        title: str = Form(...),
        language: str = Form("text"),
        visibility: str = Form("public"),
        description: str = Form(""),
        body: str = Form(...),
        member: dict[str, Any] = Depends(_require_member),
    ) -> Response:
        db_path = _get_db_path(request)
        if visibility not in SNIPPET_VISIBILITIES:
            errors = {"visibility": "Visibilité invalide"}
            defaults = {
                "title": title,
                "language": language,
                "visibility": visibility,
                "description": description,
                "body": body,
            }
            return HTMLResponse(
                content=_render_snippet_form(member, errors=errors, defaults=defaults),
                status_code=400,
            )
        snippet_id = create_snippet(
            db_path,
            owner_id=int(member["id"]),
            title=title.strip(),
            language=language.strip() or "text",
            description=description.strip(),
            body=body,
            visibility=visibility,
        )
        return RedirectResponse(url=f"/snippets/{snippet_id}", status_code=303)

    @app.get("/snippets/{snippet_id}", response_class=HTMLResponse)
    async def snippet_detail(snippet_id: int, request: Request) -> HTMLResponse:
        db_path = _get_db_path(request)
        snippet = fetch_snippet(db_path, snippet_id)
        if snippet is None:
            raise HTTPException(status_code=404, detail="Snippet introuvable")
        member = _get_member(request)
        allowed = has_access(db_path, snippet_id, user_id=int(member["id"]) if member else None)
        pending = None
        if member:
            pending = get_request_for_user(
                db_path, snippet_id=snippet_id, requester_id=int(member["id"])
            )
        view = _render_snippet_detail(
            snippet=snippet,
            member=member,
            allowed=allowed,
            pending_request=pending,
        )
        status = 200 if allowed or member is None else 403
        return HTMLResponse(content=view, status_code=status)

    @app.post("/snippets/{snippet_id}/request-access")
    async def request_access(
        snippet_id: int,
        request: Request,
        note: str = Form(""),
        member: dict[str, Any] = Depends(_require_member),
    ) -> HTMLResponse:
        db_path = _get_db_path(request)
        snippet = fetch_snippet(db_path, snippet_id)
        if snippet is None:
            raise HTTPException(status_code=404, detail="Snippet introuvable")
        if snippet["owner_id"] == int(member["id"]):
            return HTMLResponse(
                content=_render_snippet_detail(
                    snippet=snippet,
                    member=member,
                    allowed=True,
                    pending_request=None,
                    notice="Vous êtes propriétaire de ce snippet.",
                )
            )
        create_access_request(
            db_path,
            snippet_id=snippet_id,
            requester_id=int(member["id"]),
            note=note.strip() or None,
        )
        pending = get_request_for_user(
            db_path, snippet_id=snippet_id, requester_id=int(member["id"])
        )
        return HTMLResponse(
            content=_render_snippet_detail(
                snippet=snippet,
                member=member,
                allowed=False,
                pending_request=pending,
                notice="Demande envoyée.",
            ),
            status_code=202,
        )

    @app.get("/snippets/{snippet_id}/edit", response_class=HTMLResponse)
    async def edit_snippet_form(
        snippet_id: int, request: Request, member: dict[str, Any] = Depends(_require_member)
    ) -> HTMLResponse:
        db_path = _get_db_path(request)
        snippet = fetch_snippet(db_path, snippet_id)
        if snippet is None:
            raise HTTPException(status_code=404, detail="Snippet introuvable")
        if snippet["owner_id"] != int(member["id"]):
            raise HTTPException(status_code=403, detail="Modification interdite")
        return HTMLResponse(
            content=_render_snippet_form(
                member, errors=None, defaults=snippet, action=f"/snippets/{snippet_id}/edit"
            )
        )

    @app.post("/snippets/{snippet_id}/edit")
    async def edit_snippet_submit(
        snippet_id: int,
        request: Request,
        title: str = Form(...),
        language: str = Form("text"),
        visibility: str = Form("public"),
        description: str = Form(""),
        body: str = Form(...),
        member: dict[str, Any] = Depends(_require_member),
    ) -> Response:
        db_path = _get_db_path(request)
        snippet = fetch_snippet(db_path, snippet_id)
        if snippet is None:
            raise HTTPException(status_code=404, detail="Snippet introuvable")
        if snippet["owner_id"] != int(member["id"]):
            raise HTTPException(status_code=403, detail="Modification interdite")
        if visibility not in SNIPPET_VISIBILITIES:
            errors = {"visibility": "Visibilité invalide"}
            defaults = {
                "title": title,
                "language": language,
                "visibility": visibility,
                "description": description,
                "body": body,
            }
            return HTMLResponse(
                content=_render_snippet_form(
                    member, errors=errors, defaults=defaults, action=f"/snippets/{snippet_id}/edit"
                ),
                status_code=400,
            )
        update_snippet(
            db_path,
            snippet_id,
            owner_id=int(member["id"]),
            title=title.strip(),
            language=language.strip() or "text",
            description=description.strip(),
            body=body,
            visibility=visibility,
        )
        return RedirectResponse(url=f"/snippets/{snippet_id}", status_code=303)

    @app.post("/snippets/{snippet_id}/delete")
    async def delete_snippet_route(
        snippet_id: int,
        request: Request,
        member: dict[str, Any] = Depends(_require_member),
    ) -> Response:
        db_path = _get_db_path(request)
        snippet = fetch_snippet(db_path, snippet_id)
        if snippet is None:
            raise HTTPException(status_code=404, detail="Snippet introuvable")
        if snippet["owner_id"] != int(member["id"]):
            raise HTTPException(status_code=403, detail="Suppression interdite")
        delete_snippet(db_path, snippet_id, owner_id=int(member["id"]))
        return RedirectResponse(url="/", status_code=303)

    @app.get("/signup", response_class=HTMLResponse)
    async def signup_form() -> HTMLResponse:
        return HTMLResponse(content=_render_signup())

    @app.post("/signup", response_class=HTMLResponse)
    async def signup_submit(
        request: Request, email: str = Form(...), password: str = Form(...)
    ) -> HTMLResponse:
        db_path = _get_db_path(request)
        email_normalized = email.strip().lower()
        user_id = create_user(db_path, email=email_normalized, password=password)
        if user_id == -1:
            return HTMLResponse(
                content=_render_signup(error="Adresse déjà utilisée"), status_code=400
            )
        token = issue_email_token(db_path, user_id)
        _send_verification_email(request, email=email_normalized, token=token)
        return HTMLResponse(content=_render_signup(success=email_normalized))

    @app.get("/verify", response_class=HTMLResponse)
    async def verify_email(request: Request, token: str) -> HTMLResponse:
        db_path = _get_db_path(request)
        user_id = consume_email_token(db_path, token)
        if user_id is None:
            return HTMLResponse(
                content=_render_verify(error="Lien invalide ou expiré"), status_code=400
            )
        mark_email_verified(db_path, user_id)
        return HTMLResponse(content=_render_verify())

    @app.get("/login", response_class=HTMLResponse)
    async def login_form() -> HTMLResponse:
        return HTMLResponse(content=_render_login())

    @app.post("/login")
    async def login_submit(
        request: Request, email: str = Form(...), password: str = Form(...)
    ) -> Response:
        db_path = _get_db_path(request)
        secret = request.app.state.session_secret
        user = fetch_user_by_email(db_path, email.strip().lower())
        if user is None or not verify_password(password, user["password_hash"]):
            return HTMLResponse(
                content=_render_login(error="Identifiants invalides"), status_code=401
            )
        if not user.get("email_verified"):
            return HTMLResponse(content=_render_login(error="Email non vérifié"), status_code=401)
        response = RedirectResponse(url="/", status_code=303)
        cookie = build_member_cookie(secret, int(user["id"]))
        response.set_cookie(
            "snippet_member",
            cookie,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=12 * 60 * 60,
        )
        return response

    @app.post("/logout")
    async def logout() -> Response:
        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie("snippet_member")
        return response

    @app.get("/forgot", response_class=HTMLResponse)
    async def forgot_form() -> HTMLResponse:
        return HTMLResponse(content=_render_forgot())

    @app.post("/forgot", response_class=HTMLResponse)
    async def forgot_submit(request: Request, email: str = Form(...)) -> HTMLResponse:
        db_path = _get_db_path(request)
        email_normalized = email.strip().lower()
        user = fetch_user_by_email(db_path, email_normalized)
        if user:
            token = issue_password_reset_token(db_path, int(user["id"]))
            _send_password_reset_email(request, email=email_normalized, token=token)
        return HTMLResponse(content=_render_forgot(sent=True))

    @app.get("/reset", response_class=HTMLResponse)
    async def reset_form(token: str) -> HTMLResponse:
        return HTMLResponse(content=_render_reset(token))

    @app.post("/reset", response_class=HTMLResponse)
    async def reset_submit(
        request: Request, token: str = Form(...), password: str = Form(...)
    ) -> HTMLResponse:
        db_path = _get_db_path(request)
        user_id = consume_password_reset_token(db_path, token)
        if user_id is None:
            return HTMLResponse(
                content=_render_reset(token, error="Lien invalide ou expiré"), status_code=400
            )
        update_password(db_path, user_id, password)
        return HTMLResponse(content=_render_reset_success())

    @app.get("/account", response_class=HTMLResponse)
    async def account_page(
        request: Request, member: dict[str, Any] = Depends(_require_member)
    ) -> HTMLResponse:
        db_path = _get_db_path(request)
        private_count = count_private_snippets(db_path, int(member["id"]))
        requests_summary = []
        return HTMLResponse(
            content=_render_account(member, private_count=private_count, requests=requests_summary)
        )

    @app.get("/admin/requests", response_class=HTMLResponse)
    async def admin_requests(
        request: Request, member: dict[str, Any] = Depends(_require_admin)
    ) -> HTMLResponse:
        db_path = _get_db_path(request)
        pending = list_pending_requests(db_path)
        enriched = []
        for entry in pending:
            snippet = fetch_snippet(db_path, int(entry["snippet_id"]))
            requester = fetch_user_by_id(db_path, int(entry["requester_id"]))
            if not snippet or not requester:
                continue
            enriched.append(
                {
                    "id": entry["id"],
                    "snippet": snippet,
                    "requester": requester,
                    "note": entry.get("note"),
                    "created_at": entry.get("created_at"),
                }
            )
        return HTMLResponse(content=_render_admin_requests(member, enriched))

    @app.post("/admin/requests/{request_id}/approve")
    async def admin_approve(
        request_id: int, request: Request, member: dict[str, Any] = Depends(_require_admin)
    ) -> Response:
        db_path = _get_db_path(request)
        update_request_status(db_path, request_id, status="approved")
        return RedirectResponse(url="/admin/requests", status_code=303)

    @app.post("/admin/requests/{request_id}/deny")
    async def admin_deny(
        request_id: int, request: Request, member: dict[str, Any] = Depends(_require_admin)
    ) -> Response:
        db_path = _get_db_path(request)
        update_request_status(db_path, request_id, status="denied")
        return RedirectResponse(url="/admin/requests", status_code=303)

    return app


def _resolve_db_path(url: str | None) -> str:
    if not url:
        default_dir = Path(__file__).resolve().parents[2] / "data"
        default_dir.mkdir(parents=True, exist_ok=True)
        return str(default_dir / "snippets.db")
    if url.startswith("sqlite+pysqlite:///"):
        path = url.replace("sqlite+pysqlite:///", "", 1)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return path or ":memory:"
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return path or ":memory:"
    return url


def _get_db_path(request: Request) -> str:
    db_path = getattr(request.app.state, "db_path", None)
    if db_path is None:
        raise HTTPException(status_code=500, detail="Database non configurée")
    return db_path


def _get_member(request: Request) -> dict[str, Any] | None:
    db_path = getattr(request.app.state, "db_path", None)
    secret = getattr(request.app.state, "session_secret", None)
    if db_path is None or not secret:
        return None
    cookie = request.cookies.get("snippet_member")
    if not cookie:
        return None
    session = parse_member_cookie(secret, cookie)
    if session is None:
        return None
    return fetch_user_by_id(db_path, session.user_id)


def _require_member(request: Request) -> dict[str, Any]:
    member = _get_member(request)
    if member is None:
        raise HTTPException(
            status_code=303, detail="Connexion requise", headers={"Location": "/login"}
        )
    return member


def _require_admin(
    request: Request, member: dict[str, Any] = Depends(_require_member)
) -> dict[str, Any]:
    if member.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Accès réservé à l'administration")
    return member


def _bootstrap_admin_user(db_path: str, email: str | None, password: str | None) -> None:
    if not (email and password):
        return
    email_normalized = email.strip().lower()
    existing = fetch_user_by_email(db_path, email_normalized)
    if existing and existing.get("role") == "admin":
        return
    if existing:
        update_password(db_path, int(existing["id"]), password)
        mark_email_verified(db_path, int(existing["id"]))
        return
    user_id = create_user(
        db_path, email=email_normalized, password=password, role="admin", verified=True
    )
    if user_id == -1:
        logger.warning("Impossible de créer l'administrateur par défaut")


def _render_home(*, snippets: list[dict[str, Any]], member: dict[str, Any] | None) -> str:
    cards = []
    for snippet in snippets:
        language = escape(snippet.get("language") or "text")
        title = escape(snippet.get("title") or "Snippet sans nom")
        description = escape((snippet.get("description") or "").strip() or "Aucune description")
        cards.append(
            f"""
            <article class=\"snippet-card\">
              <header>
                <a href=\"/snippets/{snippet["id"]}\" class=\"title\">{title}</a>
                <span class=\"language\">{language}</span>
              </header>
              <p class=\"description\">{description}</p>
              <footer>
                <a class=\"link\" href=\"/snippets/{snippet["id"]}\">Voir le détail &rarr;</a>
              </footer>
            </article>
            """
        )
    empty_state = (
        '<p class="empty">Aucun snippet disponible pour le moment.</p>' if not cards else ""
    )
    create_cta = '<a class="cta" href="/snippets/new">Créer un snippet</a>' if member else ""
    return f"""
    <!DOCTYPE html>
    <html lang=\"fr\">
      <head>
        <meta charset=\"utf-8\" />
        <title>Snippet Vault</title>
        {_BASE_STYLES}
      </head>
      <body>
        <nav class=\"topbar\">
          <div class=\"brand\"><a href=\"/\">Snippet Vault</a></div>
          <div class=\"actions\">
            {create_cta}
            {_nav_links(member)}
          </div>
        </nav>
        <main class=\"layout\">
          <h1>Bibliothèque de snippets</h1>
          <p class=\"lead\">Stockez des extraits de code, gérez leur visibilité et partagez-les sur demande.</p>
          <section class=\"grid\">{"".join(cards)}</section>
          {empty_state}
        </main>
      </body>
    </html>
    """


def _render_snippet_detail(
    *,
    snippet: dict[str, Any],
    member: dict[str, Any] | None,
    allowed: bool,
    pending_request: dict[str, Any] | None,
    notice: str | None = None,
) -> str:
    title = escape(snippet.get("title") or "Snippet sans nom")
    language = escape(snippet.get("language") or "text")
    description = escape(snippet.get("description") or "Aucune description")
    body = escape(snippet.get("body") or "")
    visibility = escape(snippet.get("visibility") or "public")
    owner = snippet.get("owner_id")
    owner_actions = ""
    if member and owner == int(member["id"]):
        owner_actions = (
            f"<div class=\"owner-actions\"><a href='/snippets/{snippet['id']}/edit'>Modifier</a>"
            f"<form method='post' action='/snippets/{snippet['id']}/delete' onsubmit='return confirm(\"Supprimer ce snippet ?\")'>"
            "<button type='submit'>Supprimer</button></form></div>"
        )
    access_block = ""
    if not allowed:
        if not member:
            access_block = "<p class='notice'>Connectez-vous pour demander l'accès.</p>"
        else:
            status = (
                "<p class='info'>Demande en attente.</p>"
                if pending_request and pending_request.get("status") == "pending"
                else ""
            )
            button = ""
            if not pending_request or pending_request.get("status") == "denied":
                button = (
                    f"<form method='post' action='/snippets/{snippet['id']}/request-access'>"
                    "<label for='note'>Message pour le propriétaire (optionnel)</label>"
                    "<textarea id='note' name='note' rows='3'></textarea>"
                    "<button type='submit'>Demander l'accès</button>"
                    "</form>"
                )
            access_block = f"<div class='restricted'><p class='notice'>Ce snippet est privé.</p>{status}{button}</div>"
    body_block = f"<pre class='code'><code>{body}</code></pre>" if allowed else ""
    info = notice or ""
    return f"""
    <!DOCTYPE html>
    <html lang=\"fr\">
      <head>
        <meta charset=\"utf-8\" />
        <title>{title} · Snippet</title>
        {_BASE_STYLES}
      </head>
      <body>
        <nav class=\"topbar\">
          <div class=\"brand\"><a href=\"/\">Snippet Vault</a></div>
          <div class=\"actions\">{_nav_links(member)}</div>
        </nav>
        <main class=\"layout\">
          <header class=\"snippet-header\">
            <h1>{title}</h1>
            <div class=\"meta\">
              <span class=\"pill\">{language}</span>
              <span class=\"pill visibility\">{visibility}</span>
            </div>
          </header>
          {owner_actions}
          <p class=\"description\">{description}</p>
          {access_block}
          {body_block}
          <p class=\"info\">{info}</p>
        </main>
      </body>
    </html>
    """


def _render_snippet_form(
    member: dict[str, Any],
    errors: dict[str, str] | None,
    defaults: dict[str, Any] | None = None,
    action: str = "/snippets/new",
) -> str:
    defaults = defaults or {}
    title = escape(defaults.get("title") or "")
    language = escape(defaults.get("language") or "text")
    visibility = defaults.get("visibility") or "public"
    description = escape(defaults.get("description") or "")
    body = escape(defaults.get("body") or "")
    error_visibility = (errors or {}).get("visibility")
    options = "".join(
        f"<option value='{vis}' {'selected' if vis == visibility else ''}>{vis.title()}</option>"
        for vis in SNIPPET_VISIBILITIES
    )
    return f"""
    <!DOCTYPE html>
    <html lang=\"fr\">
      <head>
        <meta charset=\"utf-8\" />
        <title>Créer un snippet</title>
        {_BASE_STYLES}
      </head>
      <body>
        <nav class=\"topbar\">
          <div class=\"brand\"><a href=\"/\">Snippet Vault</a></div>
          <div class=\"actions\">{_nav_links(member)}</div>
        </nav>
        <main class=\"layout\">
          <h1>Nouvel extrait</h1>
          <form class=\"form\" method=\"post\" action=\"{action}\">
            <label>Titre<input name=\"title\" value=\"{title}\" required /></label>
            <label>Langage<input name=\"language\" value=\"{language}\" /></label>
            <label>Visibilité<select name=\"visibility\">{options}</select></label>
            <p class=\"error\">{error_visibility or ""}</p>
            <label>Description<textarea name=\"description\" rows=\"3\">{description}</textarea></label>
            <label>Code<textarea name=\"body\" rows=\"12\" required>{body}</textarea></label>
            <button type=\"submit\">Enregistrer</button>
          </form>
        </main>
      </body>
    </html>
    """


def _render_signup(error: str | None = None, success: str | None = None) -> str:
    message = ""
    if error:
        message = f"<p class='error'>{escape(error)}</p>"
    elif success:
        message = f"<p class='info'>Nous avons envoyé un email à {escape(success)}.</p>"
    return _auth_page("Créer un compte", "signup", message)


def _render_login(error: str | None = None) -> str:
    message = f"<p class='error'>{escape(error)}</p>" if error else ""
    return _auth_page("Se connecter", "login", message)


def _render_forgot(sent: bool = False) -> str:
    message = "<p class='info'>Si un compte existe, un email a été envoyé.</p>" if sent else ""
    return _auth_page("Réinitialiser le mot de passe", "forgot", message)


def _render_reset(token: str, error: str | None = None) -> str:
    message = f"<p class='error'>{escape(error)}</p>" if error else ""
    extra = f"<input type='hidden' name='token' value='{escape(token)}' />"
    return _auth_page("Nouveau mot de passe", "reset", message, extra_fields=extra)


def _render_reset_success() -> str:
    return _auth_page(
        "Mot de passe mis à jour",
        "login",
        "<p class='info'>Vous pouvez maintenant vous connecter.</p>",
    )


def _render_verify(error: str | None = None) -> str:
    message = (
        f"<p class='error'>{escape(error)}</p>"
        if error
        else "<p class='info'>Email vérifié. Vous pouvez vous connecter.</p>"
    )
    return _auth_page("Vérification", "login", message)


def _auth_page(title: str, form_action: str, message: str, *, extra_fields: str = "") -> str:
    password_field = ""
    if form_action in {"signup", "login", "reset"}:
        password_field = (
            "<label>Mot de passe<input type='password' name='password' required /></label>"
        )
    email_field = ""
    if form_action in {"signup", "login", "forgot"}:
        email_field = "<label>Email<input type='email' name='email' required /></label>"
    return f"""
    <!DOCTYPE html>
    <html lang=\"fr\">
      <head>
        <meta charset=\"utf-8\" />
        <title>{escape(title)}</title>
        {_BASE_STYLES}
      </head>
      <body class=\"auth\">
        <main class=\"auth-card\">
          <h1>{escape(title)}</h1>
          {message}
          <form method=\"post\" action=\"/{form_action}\">
            {extra_fields}
            {email_field}
            {password_field}
            <button type=\"submit\">Valider</button>
          </form>
          <div class=\"switch\">
            <a href=\"/login\">Connexion</a>
            <a href=\"/signup\">Inscription</a>
            <a href=\"/forgot\">Mot de passe oublié</a>
          </div>
        </main>
      </body>
    </html>
    """


def _render_account(
    member: dict[str, Any], *, private_count: int, requests: list[dict[str, Any]]
) -> str:
    summary = f"<p class='info'>Vous possédez {private_count} snippet(s) privé(s).</p>"
    request_items = "".join(
        f"<li>Demande #{req['id']} · statut {escape(req.get('status', 'inconnu'))}</li>"
        for req in requests
    )
    requests_html = (
        f"<ul class='requests'>{request_items}</ul>"
        if request_items
        else "<p>Aucune demande émise.</p>"
    )
    admin_link = (
        "<a class='cta' href='/admin/requests'>Demandes à valider</a>"
        if member.get("role") == "admin"
        else ""
    )
    return f"""
    <!DOCTYPE html>
    <html lang=\"fr\">
      <head>
        <meta charset=\"utf-8\" />
        <title>Mon compte</title>
        {_BASE_STYLES}
      </head>
      <body>
        <nav class=\"topbar\">
          <div class=\"brand\"><a href=\"/\">Snippet Vault</a></div>
          <div class=\"actions\">{_nav_links(member)}</div>
        </nav>
        <main class=\"layout\">
          <h1>Bonjour {escape(member.get("email", ""))}</h1>
          {summary}
          <section>
            <h2>Mes demandes</h2>
            {requests_html}
          </section>
          {admin_link}
        </main>
      </body>
    </html>
    """


def _render_admin_requests(member: dict[str, Any], requests: list[dict[str, Any]]) -> str:
    rows = []
    for item in requests:
        notes = escape(item.get("note") or "")
        snippet = item["snippet"]
        requester = item["requester"]
        rows.append(
            f"""
            <article class=\"request\">
              <h2>{escape(snippet.get("title") or "Snippet")}</h2>
              <p>Demande de {escape(requester.get("email") or "utilisateur")}.</p>
              <p>{notes}</p>
              <form method='post' action='/admin/requests/{item["id"]}/approve'>
                <button type='submit'>Approuver</button>
              </form>
              <form method='post' action='/admin/requests/{item["id"]}/deny'>
                <button type='submit'>Refuser</button>
              </form>
            </article>
            """
        )
    placeholder = "<p>Aucune demande en attente.</p>" if not rows else ""
    return f"""
    <!DOCTYPE html>
    <html lang=\"fr\">
      <head>
        <meta charset=\"utf-8\" />
        <title>Demandes d'accès</title>
        {_BASE_STYLES}
      </head>
      <body>
        <nav class=\"topbar\">
          <div class=\"brand\"><a href=\"/\">Snippet Vault</a></div>
          <div class=\"actions\">{_nav_links(member)}</div>
        </nav>
        <main class=\"layout\">
          <h1>Demandes d'accès</h1>
          {placeholder}
          {"".join(rows)}
        </main>
      </body>
    </html>
    """


def _nav_links(member: dict[str, Any] | None) -> str:
    if member:
        return (
            "<a href='/account'>Mon compte</a>"
            "<form method='post' action='/logout'><button type='submit'>Déconnexion</button></form>"
        )
    return "<a href='/login'>Connexion</a><a href='/signup'>Inscription</a>"


_BASE_STYLES = """
<style>
  :root {
    color-scheme: light;
    --bg: radial-gradient(circle at top, #f5f7fb 0%, #ffffff 65%);
    --text: #101323;
    --muted: #5b6275;
    --accent: #2f6fed;
    --accent-soft: rgba(47, 111, 237, 0.12);
    --border: rgba(16, 19, 35, 0.08);
    --card: rgba(255, 255, 255, 0.75);
    --shadow: 0 18px 40px rgba(16, 19, 35, 0.08);
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: 'Inter', 'Helvetica Neue', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .topbar { display: flex; justify-content: space-between; align-items: center; padding: 24px; }
  .topbar .brand a { font-weight: 700; letter-spacing: -0.4px; font-size: 22px; }
  .topbar .actions { display: flex; gap: 16px; align-items: center; }
  .topbar form { margin: 0; }
  .topbar button { background: transparent; border: none; color: var(--accent); cursor: pointer; font-size: 16px; }
  .layout { width: min(900px, 94%); margin: 0 auto 80px; background: var(--card); padding: 48px; border-radius: 28px; box-shadow: var(--shadow); backdrop-filter: blur(10px); }
  .layout h1 { margin-top: 0; font-size: 34px; letter-spacing: -0.6px; }
  .lead { color: var(--muted); max-width: 540px; }
  .grid { display: grid; gap: 24px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); margin-top: 32px; }
  .snippet-card { background: white; border-radius: 20px; padding: 24px; box-shadow: 0 16px 40px rgba(31, 41, 55, 0.12); display: flex; flex-direction: column; gap: 12px; }
  .snippet-card header { display: flex; justify-content: space-between; align-items: center; }
  .snippet-card .title { font-weight: 600; font-size: 18px; }
  .snippet-card .language { background: var(--accent-soft); color: var(--accent); padding: 4px 10px; border-radius: 999px; font-size: 12px; }
  .snippet-card .description { color: var(--muted); font-size: 15px; height: 72px; overflow: hidden; }
  .snippet-card footer { margin-top: auto; display: flex; justify-content: space-between; align-items: center; }
  .cta { background: var(--accent); color: white; padding: 10px 18px; border-radius: 999px; font-weight: 600; }
  .cta:hover { text-decoration: none; }
  .empty { margin-top: 40px; color: var(--muted); font-style: italic; }
  .snippet-header { display: flex; flex-direction: column; gap: 16px; }
  .meta { display: flex; gap: 12px; }
  .pill { background: var(--accent-soft); color: var(--accent); padding: 6px 14px; border-radius: 999px; font-size: 13px; }
  .pill.visibility { background: rgba(250, 176, 5, 0.14); color: #b54708; }
  .description { color: var(--muted); line-height: 1.6; }
  .code { background: #0f172a; color: #e2e8f0; padding: 24px; border-radius: 16px; overflow-x: auto; font-family: 'JetBrains Mono', monospace; font-size: 14px; }
  .restricted { margin: 24px 0; padding: 24px; border: 1px dashed var(--accent); border-radius: 16px; background: rgba(47, 111, 237, 0.05); }
  .restricted form { display: flex; flex-direction: column; gap: 12px; }
  .restricted textarea { width: 100%; min-height: 80px; }
  .restricted button { align-self: flex-start; }
  button { background: var(--accent); color: white; border: none; padding: 10px 18px; border-radius: 999px; cursor: pointer; font-weight: 600; }
  button:hover { opacity: 0.9; }
  .form { display: flex; flex-direction: column; gap: 18px; }
  .form input, .form textarea, .form select { width: 100%; padding: 12px; border-radius: 12px; border: 1px solid var(--border); background: rgba(255,255,255,0.9); }
  label { display: flex; flex-direction: column; gap: 8px; font-weight: 500; }
  .error { color: #b42318; }
  .info { color: var(--muted); }
  .auth { display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  .auth-card { width: min(420px, 92%); background: var(--card); padding: 48px; border-radius: 24px; box-shadow: var(--shadow); }
  .auth-card form { display: flex; flex-direction: column; gap: 18px; }
  .auth-card input { padding: 12px; border-radius: 12px; border: 1px solid var(--border); }
  .switch { display: flex; justify-content: space-between; margin-top: 16px; font-size: 14px; }
  .owner-actions { display: flex; gap: 12px; margin: 16px 0; }
  .owner-actions form { margin: 0; }
  .owner-actions button { background: rgba(220,38,38,0.85); }
  .requests { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 10px; }
  .request { background: white; padding: 24px; border-radius: 18px; margin-bottom: 16px; display: grid; gap: 12px; }
  @media (max-width: 720px) {
    .layout { padding: 32px 22px; }
    .snippet-card { padding: 20px; }
    .topbar { flex-direction: column; gap: 12px; }
  }
</style>
"""


def _send_verification_email(request: Request, *, email: str, token: str) -> None:
    logger.info("Envoi fictif du mail de vérification pour %s avec token %s", email, token)


def _send_password_reset_email(request: Request, *, email: str, token: str) -> None:
    logger.info("Envoi fictif du mail de réinitialisation pour %s avec token %s", email, token)
