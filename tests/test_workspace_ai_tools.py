"""AI coding tools and browser AI assistants must be classified as work."""
from __future__ import annotations

from pathlib import Path

from config.workspace_rules import COMPANY, DEVELOPMENT, WorkspaceClassifier
from tracker.categorizer import categorize_activity, extract_website_name


def _classifier(tmp_path: Path) -> WorkspaceClassifier:
    return WorkspaceClassifier(config_path=tmp_path / "workspace_rules.json")


def test_coding_ides_and_agents_are_development(tmp_path: Path):
    classifier = _classifier(tmp_path)
    cases = [
        ("Code.exe", "app.py - companydairy - Visual Studio Code"),
        ("Cursor.exe", "app.py - companydairy - Cursor"),
        ("Antigravity.exe", "app.py - companydairy - Antigravity"),
        ("OpenCode.exe", "WorkSense AI tracking \u00b7 OpenCode"),
        ("Windsurf.exe", "app.py - Windsurf"),
        ("Trae.exe", "app.py - Trae"),
        ("Kiro.exe", "app.py - Kiro"),
    ]
    for process, title in cases:
        assert classifier.classify_application(process=process, title=title) == DEVELOPMENT, (
            process,
            title,
        )


def test_ai_browser_tools_are_company(tmp_path: Path):
    classifier = _classifier(tmp_path)
    titles = [
        "CampusConnect-within-radius | Google AI Studio - Google Chrome",
        "ChatGPT - Google Chrome",
        "Gemini - Google Chrome",
        "Claude - Google Chrome",
        "Microsoft Copilot - Google Chrome",
        "Perplexity - Google Chrome",
        "DeepSeek - Google Chrome",
    ]
    for title in titles:
        assert classifier.classify_application(process="chrome.exe", title=title) == COMPANY, title


def test_ai_tools_get_work_categories_and_site_names():
    assert categorize_activity("OpenCode.exe", "WorkSense \u00b7 OpenCode") == "Coding"
    assert (
        categorize_activity(
            "chrome.exe", "CampusConnect | Google AI Studio - Google Chrome"
        )
        == "AI Tools"
    )
    assert (
        extract_website_name(
            "CampusConnect | Google AI Studio - Google Chrome", "chrome.exe"
        )
        == "Google AI Studio"
    )
    assert extract_website_name("Gemini - Google Chrome", "chrome.exe") == "Gemini AI"
    assert extract_website_name("Claude - Google Chrome", "chrome.exe") == "Claude AI"
    # The browser brand must not become the site name.
    assert (
        extract_website_name(
            "Upwork Login - Log in to your Upwork account - Google Chrome",
            "chrome.exe",
        )
        == "Upwork"
    )


def test_old_rule_file_merges_new_ai_defaults(tmp_path: Path):
    path = tmp_path / "workspace_rules.json"
    path.write_text(
        '{"rules_version": 1, "development_apps": ["code"], '
        '"company_title_keywords": ["outlook"]}',
        encoding="utf-8",
    )
    classifier = WorkspaceClassifier(config_path=path)
    rules = classifier.get_rules()
    assert "opencode" in rules["development_apps"]
    assert "google ai studio" in rules["company_title_keywords"]
    assert rules["rules_version"] >= 2
