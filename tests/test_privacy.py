"""Privacy defaults: sensitive windows are excluded and text is redacted."""
from __future__ import annotations

from config.privacy_rules import PrivacyRules, scrub_sensitive_text
from ocr.screen_capture import SENSITIVE_PROCESSES, SENSITIVE_TITLES_REGEX


def test_default_rules_exclude_password_managers_and_banking():
    rules = PrivacyRules()
    assert rules.should_capture("KeePassXC.exe", "Vault") is False
    assert rules.should_capture("chrome.exe", "Online Banking - Account") is False
    assert rules.should_capture("msedge.exe", "Private browsing") is False
    assert rules.should_capture("code.exe", "main.py - Visual Studio Code") is True


def test_screen_capture_sensitive_patterns_cover_common_secrets():
    assert any("keepass" in proc for proc in SENSITIVE_PROCESSES)
    assert any("1password" in proc for proc in SENSITIVE_PROCESSES)
    for title in ("Sign in to account", "Login", "Credit card payment",
                  "Social security number", "InPrivate window"):
        assert SENSITIVE_TITLES_REGEX.search(title), title
    assert SENSITIVE_TITLES_REGEX.search("work notes - editor") is None


def test_sensitive_text_is_redacted():
    scrubbed = scrub_sensitive_text(
        "card 4111-1111-1111-1111 email user@example.com ssn 123-45-6789"
    )
    assert "4111" not in scrubbed
    assert "user@example.com" not in scrubbed
    assert "123-45-6789" not in scrubbed
    assert scrubbed.count("[REDACTED]") == 3
