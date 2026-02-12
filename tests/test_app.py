from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from simppetssrv.app import create_app
from simppetssrv.members import create_user, mark_email_verified
from simppetssrv.snippets import create_snippet


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def app(tmp_path: Path):
    db_path = tmp_path / "snippets.db"
    return create_app(
        db_url=f"sqlite+pysqlite:///{db_path}",
        session_secret="test-secret",
        admin_email="admin@example.com",
        admin_password="adm1n-pass",
        force_https=False,
    )


def make_client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_home_lists_public_snippets(app):
    db_path = app.state.db_path
    owner_id = create_user(db_path, email="owner@example.com", password="secret", verified=True)
    create_snippet(
        db_path,
        owner_id=owner_id,
        title="Hello World",
        language="python",
        description="Snippet public",
        body="print('hello')",
        visibility="public",
    )

    async with make_client(app) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "Hello World" in response.text
    assert "Créer un snippet" not in response.text


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    response = await client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie = response.headers.get("set-cookie")
    assert cookie
    return {"Cookie": cookie}


@pytest.mark.asyncio
async def test_authenticated_user_can_create_snippet(app):
    db_path = app.state.db_path
    user_id = create_user(db_path, email="writer@example.com", password="secret", verified=True)
    mark_email_verified(db_path, user_id)

    async with make_client(app) as client:
        headers = await _login(client, "writer@example.com", "secret")
        response = await client.post(
            "/snippets/new",
            data={
                "title": "API client",
                "language": "python",
                "visibility": "public",
                "description": "client httpx",
                "body": "async with client as c:",
            },
            headers=headers,
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    async with make_client(app) as client:
        detail = await client.get(location, headers=headers)

    assert detail.status_code == 200
    assert "API client" in detail.text
    assert "async with client" in detail.text


@pytest.mark.asyncio
async def test_private_snippet_requires_access_request(app):
    db_path = app.state.db_path
    owner_id = create_user(db_path, email="owner@example.com", password="secret", verified=True)
    mark_email_verified(db_path, owner_id)
    snippet_id = create_snippet(
        db_path,
        owner_id=owner_id,
        title="Private Notes",
        language="python",
        description="secret",
        body="print('secret')",
        visibility="private",
    )

    requester_id = create_user(db_path, email="user@example.com", password="pass123", verified=True)
    mark_email_verified(db_path, requester_id)

    async with make_client(app) as client:
        headers = await _login(client, "user@example.com", "pass123")
        response = await client.get(f"/snippets/{snippet_id}", headers=headers)
        assert response.status_code == 403
        assert "Ce snippet est privé" in response.text

        request_response = await client.post(
            f"/snippets/{snippet_id}/request-access",
            data={"note": "Merci"},
            headers=headers,
        )

    assert request_response.status_code == 202
    assert "Demande envoyée" in request_response.text

    async with make_client(app) as client:
        admin_headers = await _login(client, "admin@example.com", "adm1n-pass")
        pending_page = await client.get("/admin/requests", headers=admin_headers)

    assert pending_page.status_code == 200
    assert "Private Notes" in pending_page.text


@pytest.mark.asyncio
async def test_admin_can_approve_request(app):
    db_path = app.state.db_path
    owner_id = create_user(db_path, email="owner2@example.com", password="secret", verified=True)
    mark_email_verified(db_path, owner_id)
    snippet_id = create_snippet(
        db_path,
        owner_id=owner_id,
        title="Server Config",
        language="yaml",
        description="infra",
        body="settings:\n  debug: false",
        visibility="private",
    )
    requester_id = create_user(db_path, email="dev@example.com", password="secret", verified=True)
    mark_email_verified(db_path, requester_id)

    async with make_client(app) as client:
        requester_headers = await _login(client, "dev@example.com", "secret")
        await client.post(
            f"/snippets/{snippet_id}/request-access",
            data={},
            headers=requester_headers,
        )

    async with make_client(app) as client:
        admin_headers = await _login(client, "admin@example.com", "adm1n-pass")
        pending_page = await client.get("/admin/requests", headers=admin_headers)
        assert "Server Config" in pending_page.text

        approve_response = await client.post(
            "/admin/requests/1/approve",
            headers=admin_headers,
            follow_redirects=False,
        )

    assert approve_response.status_code == 303

    async with make_client(app) as client:
        requester_headers = await _login(client, "dev@example.com", "secret")
        detail = await client.get(f"/snippets/{snippet_id}", headers=requester_headers)

    assert detail.status_code == 200
    assert "Server Config" in detail.text
    assert "settings" in detail.text
