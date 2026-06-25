from __future__ import annotations

import pathlib
from types import SimpleNamespace


def test_telegram_command_invokes_launcher(monkeypatch):
    from ouroboros import cli

    seen = {}

    def fake_launch_telegram_runtime(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("ouroboros.telegram_bootstrap.launch_telegram_runtime", fake_launch_telegram_runtime)

    result = cli._telegram_command(
        SimpleNamespace(
            host="127.0.0.1",
            port=9000,
            command_mode="full_access",
            timeout=12.5,
            review_retries=2,
            review_retry_delay=1.0,
        )
    )

    assert result == 0
    assert seen == {
        "host": "127.0.0.1",
        "port": 9000,
        "command_mode": "full_access",
        "timeout": 12.5,
        "review_retries": 2,
        "review_retry_delay": 1.0,
    }


def test_telegram_launcher_clears_persisted_skills_repo_path(monkeypatch, tmp_path):
    import ouroboros.telegram_bootstrap as bootstrap

    settings = {
        "OUROBOROS_DATA_DIR": str(tmp_path / "data"),
        "OUROBOROS_REPO_DIR": str(tmp_path / "repo"),
        "OUROBOROS_SKILLS_REPO_PATH": str(tmp_path / "external"),
    }
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setattr(bootstrap, "load_settings", lambda: dict(settings))
    monkeypatch.setattr(bootstrap, "apply_settings_to_env", lambda _settings: None)
    saved = {}
    monkeypatch.setattr(bootstrap, "save_settings", lambda payload, allow_elevation=False: saved.update({"payload": dict(payload), "allow_elevation": allow_elevation}))
    monkeypatch.setattr(bootstrap, "_start_server", lambda *a, **k: SimpleNamespace(poll=lambda: 0, wait=lambda timeout=None: 0))
    monkeypatch.setattr(bootstrap, "_wait_for_port_file", lambda *a, **k: 8765)

    captured = {}
    captured_digest = {}
    captured_broadcast = {}

    def fake_ensure(*, host, port, settings, data_dir, command_mode, timeout, review_retries, review_retry_delay):
        captured.update(
            {
                "host": host,
                "port": port,
                "settings": dict(settings),
                "data_dir": str(data_dir),
                "command_mode": command_mode,
                "timeout": timeout,
                "review_retries": review_retries,
                "review_retry_delay": review_retry_delay,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(bootstrap, "ensure_telegram_bridge_live", fake_ensure)
    monkeypatch.setattr(
        bootstrap,
        "ensure_research_digest_live",
        lambda *, host, port, data_dir, timeout, review_retries, review_retry_delay: captured_digest.update(
            {
                "host": host,
                "port": port,
                "data_dir": str(data_dir),
                "timeout": timeout,
                "review_retries": review_retries,
                "review_retry_delay": review_retry_delay,
            }
        )
        or {"ok": True},
    )
    monkeypatch.setattr(
        bootstrap,
        "ensure_post_broadcast_live",
        lambda *, host, port, data_dir, timeout, review_retries, review_retry_delay: captured_broadcast.update(
            {
                "host": host,
                "port": port,
                "data_dir": str(data_dir),
                "timeout": timeout,
                "review_retries": review_retries,
                "review_retry_delay": review_retry_delay,
            }
        )
        or {"ok": True},
    )

    result = bootstrap.launch_telegram_runtime(timeout=5, review_retries=1, review_retry_delay=2.0)

    assert result == 0
    assert captured["port"] == 8765
    assert saved["payload"]["TELEGRAM_BOT_TOKEN"] == "token-123"
    assert saved["payload"]["OUROBOROS_SKILLS_REPO_PATH"] == ""
    assert saved["allow_elevation"] is True
    assert captured["settings"]["OUROBOROS_SKILLS_REPO_PATH"] == ""
    assert captured["data_dir"] == str(pathlib.Path(settings["OUROBOROS_DATA_DIR"]))
    assert captured_digest == {
        "host": "127.0.0.1",
        "port": 8765,
        "data_dir": str(pathlib.Path(settings["OUROBOROS_DATA_DIR"])),
        "timeout": 5,
        "review_retries": 1,
        "review_retry_delay": 2.0,
    }
    assert captured_broadcast == captured_digest


def test_telegram_launcher_reads_token_from_dotenv(monkeypatch, tmp_path):
    import ouroboros.telegram_bootstrap as bootstrap

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=token-from-dotenv\n", encoding="utf-8")

    settings = {
        "OUROBOROS_DATA_DIR": str(tmp_path / "data"),
        "OUROBOROS_REPO_DIR": str(tmp_path / "repo"),
        "OUROBOROS_SKILLS_REPO_PATH": "",
    }
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.setattr(bootstrap, "load_settings", lambda: dict(settings))
    monkeypatch.setattr(bootstrap, "apply_settings_to_env", lambda _settings: None)
    saved = {}
    monkeypatch.setattr(bootstrap, "save_settings", lambda payload, allow_elevation=False: saved.update({"payload": dict(payload), "allow_elevation": allow_elevation}))
    monkeypatch.setattr(bootstrap, "_start_server", lambda *a, **k: SimpleNamespace(poll=lambda: 0, wait=lambda timeout=None: 0))
    monkeypatch.setattr(bootstrap, "_wait_for_port_file", lambda *a, **k: 8765)
    monkeypatch.setattr(bootstrap, "ensure_telegram_bridge_live", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(bootstrap, "ensure_research_digest_live", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(bootstrap, "ensure_post_broadcast_live", lambda **kwargs: {"ok": True})

    result = bootstrap.launch_telegram_runtime(timeout=5, review_retries=0, review_retry_delay=0.1)

    assert result == 0
    assert saved["payload"]["TELEGRAM_BOT_TOKEN"] == "token-from-dotenv"
    assert saved["allow_elevation"] is True


def test_repo_gitignore_appends_runtime_data_for_existing_file(tmp_path):
    from supervisor import git_ops

    repo = tmp_path / "repo"
    repo.mkdir()
    gitignore = repo / ".gitignore"
    gitignore.write_text("# existing\n.env\n", encoding="utf-8")

    git_ops._ensure_repo_gitignore(repo)

    text = gitignore.read_text(encoding="utf-8")
    assert ".env" in text
    assert "/data/" in text


def test_duckduckgo_source_filter_patch_injects_domain_filter(tmp_path):
    from ouroboros.telegram_bootstrap import patch_duckduckgo_source_filter

    skill_dir = tmp_path / "skills" / "ouroboroshub" / "duckduckgo"
    skill_dir.mkdir(parents=True)
    (skill_dir / ".ouroboroshub.json").write_text('{"source":"ouroboroshub","slug":"duckduckgo"}', encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("---\nname: duckduckgo\ntype: extension\nentry: plugin.py\n---\n", encoding="utf-8")
    (skill_dir / "plugin.py").write_text(
        '''import asyncio
import json
from typing import Any, Dict, List

_MAX_RESULTS_CAP = 20
_DEFAULT_RESULTS = 5

def _search(query: str, max_results: int = _DEFAULT_RESULTS) -> Dict[str, Any]:
    cleaned = (query or "").strip()
    from ddgs import DDGS
    with DDGS() as ddgs:
        raw = ddgs.text(cleaned, max_results=max_results)
    results: List[Dict[str, str]] = []
    for item in (raw or []):
        results.append({
            "title": str(item.get("title", "")),
            "url": str(item.get("href", "")),
            "snippet": str(item.get("body", "")),
        })

    return {"query": cleaned, "results": results, "count": len(results)}
''',
        encoding="utf-8",
    )

    result = patch_duckduckgo_source_filter(tmp_path)

    assert result["ok"] is True
    assert result["changed"] is True
    text = (skill_dir / "plugin.py").read_text(encoding="utf-8")
    assert "urllib.parse" in text
    assert "_DEFAULT_BLOCKED_DOMAINS" in text
    assert "dailymail.co.uk" in text
    assert "filtered_domains" in text
