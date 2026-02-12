"""Tests for CLI helper utilities."""

import simppetssrv.__main__ as cli


def test_display_host_returns_custom_host(monkeypatch):
    monkeypatch.setattr(cli, "_detect_local_ip", lambda: "192.0.2.1")
    assert cli._display_host("192.168.1.1") == "192.168.1.1"


def test_display_host_uses_detected_ip(monkeypatch):
    monkeypatch.setattr(cli, "_detect_local_ip", lambda: "192.0.2.1")
    assert cli._display_host("0.0.0.0") == "192.0.2.1"


def test_display_host_falls_back_to_original(monkeypatch):
    monkeypatch.setattr(cli, "_detect_local_ip", lambda: None)
    assert cli._display_host("0.0.0.0") == "0.0.0.0"
