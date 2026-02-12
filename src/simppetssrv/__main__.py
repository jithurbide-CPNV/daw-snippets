"""CLI entrypoint for the podcast server."""

from __future__ import annotations

import os
import socket
from contextlib import closing

import uvicorn

from .app import create_app


def main() -> None:
    host = os.getenv("SIMPPETSSRV_HOST", "0.0.0.0")
    port = int(os.getenv("SIMPPETSSRV_PORT", "8000"))
    reload_enabled = os.getenv("SIMPPETSSRV_RELOAD", "false").lower() in {"1", "true", "yes"}
    certfile = os.getenv("SIMPPETSSRV_SSL_CERT")
    keyfile = os.getenv("SIMPPETSSRV_SSL_KEY")
    key_password = os.getenv("SIMPPETSSRV_SSL_KEY_PASSWORD")

    use_ssl = bool(certfile and keyfile)
    scheme = "https" if use_ssl else "http"

    access_host = _display_host(host)
    print(f"Snippet server available at {scheme}://{access_host}:{port}")

    force_https_raw = os.getenv("SIMPPETSSRV_FORCE_HTTPS")
    force_https = None
    if force_https_raw is not None:
        force_https = force_https_raw.lower() in {"1", "true", "yes"}

    app = create_app(
        db_url=os.getenv("SIMPPETSSRV_DB_URL"),
        session_secret=os.getenv("SIMPPETSSRV_SESSION_SECRET"),
        admin_email=os.getenv("SIMPPETSSRV_ADMIN_EMAIL"),
        admin_password=os.getenv("SIMPPETSSRV_ADMIN_PASSWORD"),
        force_https=force_https,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload_enabled,
        ssl_certfile=certfile,
        ssl_keyfile=keyfile,
        ssl_keyfile_password=key_password,
    )


def _display_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        detected = _detect_local_ip()
        if detected:
            return detected
    return host


def _detect_local_ip() -> str | None:
    candidates = [(socket.AF_INET, ("8.8.8.8", 80)), (socket.AF_INET, ("1.1.1.1", 80))]
    for family, target in candidates:
        try:
            with closing(socket.socket(family, socket.SOCK_DGRAM)) as sock:
                sock.connect(target)
                present = sock.getsockname()[0]
                if present and not present.startswith("127."):
                    return present
        except OSError:
            continue

    # Fallback to hostname resolution
    try:
        hostname = socket.gethostname()
        present = socket.gethostbyname(hostname)
        if present and not present.startswith("127."):
            return present
    except OSError:
        return None
    return None


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
