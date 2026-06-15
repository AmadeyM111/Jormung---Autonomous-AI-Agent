from __future__ import annotations
import subprocess

def test_build_colab_settings_defaults_auto_grant_and_runtime():
    from ouroboros.colab_bootstrap import build_colab_settings, masked_secret_status
    settings = build_colab_settings({"OPENROUTER_API_KEY": "or-key", "TELEGRAM_BOT_TOKEN": "tg-token", "GITHUB_TOKEN": "gh-token"}, github_repo="anton/ouroboros", total_budget=25, runtime_mode="pro", max_workers=2)
    assert settings["GITHUB_REPO"] == "anton/ouroboros"
    assert settings["OUROBOROS_AUTO_GRANT_REVIEWED_SKILLS"] == "true"
    assert masked_secret_status(settings)["TELEGRAM_BOT_TOKEN"] is True

def test_build_colab_settings_merges_existing_owner_choices():
    # A Colab re-run must preserve prior owner choices not set by the launch knobs
    # (pinned chat, tweaked model) and drop private sentinel keys.
    from ouroboros.colab_bootstrap import build_colab_settings
    existing = {"TELEGRAM_CHAT_ID": "12345", "OUROBOROS_MODEL": "custom/model", "_settings_file_exists": True}
    out = build_colab_settings({"OPENROUTER_API_KEY": "k"}, existing=existing)
    assert out["TELEGRAM_CHAT_ID"] == "12345"
    assert out["OUROBOROS_MODEL"] == "custom/model"
    assert "_settings_file_exists" not in out
    assert out["OPENROUTER_API_KEY"] == "k"

def test_build_colab_settings_groq_profile_clears_local_runtime():
    from ouroboros.colab_bootstrap import build_colab_settings
    existing = {
        "LOCAL_MODEL_SOURCE": "Qwen/Qwen2.5-72B-Instruct-GGUF",
        "LOCAL_MODEL_FILENAME": "huge.gguf",
        "USE_LOCAL_MAIN": True,
        "USE_LOCAL_CODE": True,
        "USE_LOCAL_LIGHT": True,
        "USE_LOCAL_CONSCIOUSNESS": True,
        "USE_LOCAL_FALLBACK": True,
    }
    out = build_colab_settings({
        "GROQ_API_KEY": "gsk_test_key_1234567890",
        "GROQ_MODEL": "openai/gpt-oss-120b",
    }, existing=existing)
    expected_model = "openai-compatible::openai/gpt-oss-120b"
    assert out["OPENAI_COMPATIBLE_API_KEY"] == "gsk_test_key_1234567890"
    assert out["OPENAI_COMPATIBLE_BASE_URL"] == "https://api.groq.com/openai/v1"
    assert out["OPENAI_COMPATIBLE_CONTEXT_LENGTH"] == "8192"
    assert out["OPENAI_COMPATIBLE_MAX_TOKENS"] == "1024"
    assert out["OUROBOROS_MODEL"] == expected_model
    assert out["OUROBOROS_MODEL_CODE"] == expected_model
    assert out["OUROBOROS_REVIEW_MODELS"] == f"{expected_model},{expected_model}"
    assert out["LOCAL_MODEL_SOURCE"] == ""
    assert out["LOCAL_MODEL_FILENAME"] == ""
    assert out["LOCAL_MODEL_CHAT_FORMAT"] == ""
    assert out["USE_LOCAL_MAIN"] is False
    assert out["USE_LOCAL_CODE"] is False
    assert out["USE_LOCAL_LIGHT"] is False
    assert out["USE_LOCAL_CONSCIOUSNESS"] is False
    assert out["USE_LOCAL_FALLBACK"] is False

def test_build_colab_settings_groq_profile_defaults_to_low_context_mode():
    from ouroboros.colab_bootstrap import build_colab_settings
    out = build_colab_settings({
        "GROQ_API_KEY": "gsk_test_key_1234567890",
    }, existing={
        "OUROBOROS_CONTEXT_MODE": "max",
    })
    assert out["OUROBOROS_CONTEXT_MODE"] == "low"

def test_build_colab_settings_groq_profile_overrides_stale_large_drive_limits():
    from ouroboros.colab_bootstrap import build_colab_settings
    out = build_colab_settings({
        "GROQ_API_KEY": "gsk_test_key_1234567890",
    }, existing={
        "OPENAI_COMPATIBLE_CONTEXT_LENGTH": "131072",
        "OPENAI_COMPATIBLE_MAX_TOKENS": "8192",
    })
    assert out["OPENAI_COMPATIBLE_CONTEXT_LENGTH"] == "8192"
    assert out["OPENAI_COMPATIBLE_MAX_TOKENS"] == "1024"

def test_build_colab_settings_groq_profile_allows_explicit_secret_limits():
    from ouroboros.colab_bootstrap import build_colab_settings
    out = build_colab_settings({
        "GROQ_API_KEY": "gsk_test_key_1234567890",
        "GROQ_CONTEXT_LENGTH": "12000",
        "GROQ_MAX_TOKENS": "2048",
    })
    assert out["OPENAI_COMPATIBLE_CONTEXT_LENGTH"] == "12000"
    assert out["OPENAI_COMPATIBLE_MAX_TOKENS"] == "2048"

def test_quickstart_runs_groq_smoke_before_server():
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath("notebooks", "colab_quickstart.py").read_text(encoding="utf-8")
    assert "ouroboros.groq_api_smoke" in source
    assert source.index("apply_settings_to_env(settings)") < source.index("ouroboros.groq_api_smoke")
    assert "smoke_env" in source
    assert "OPENAI_COMPATIBLE_API_KEY" in source
    assert "capture_output=True" in source
    assert source.index("ouroboros.groq_api_smoke") < source.index("server = subprocess.Popen")
    assert "colab_server.log" in source
    assert "timeout=600.0" in source
    assert "SERVER_PORT = _free_port(8765)" in source
    assert "HOST_SERVICE_PORT = _free_port(8767, used={SERVER_PORT})" in source
    assert "settings[\"OUROBOROS_HOST_SERVICE_PORT\"] = HOST_SERVICE_PORT" in source
    assert "server_command(REPO_DIR, port=SERVER_PORT)" in source
    assert "ensure_telegram_bridge_live(settings=settings, data_dir=DATA_DIR, port=SERVER_PORT" in source

def test_quickstart_uses_clone_or_update_repo_helper():
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath("notebooks", "colab_quickstart.py").read_text(encoding="utf-8")
    assert "clone_or_update_repo" in source
    assert 'os.chdir("/content")' in source
    assert 'cwd="/content"' in source
    assert source.index("_bootstrap_checkout(REPO_DIR, SOURCE_URL)") < source.index("from ouroboros.colab_bootstrap import")
    call = "clone_or_update_repo(REPO_DIR, source_url=SOURCE_URL)"
    assert call in source
    assert source.index(call) < source.index("pip", source.index(call))
    assert "dotenv" not in source

def test_quickstart_has_ephemeral_storage_fallback_when_drive_mount_fails():
    import pathlib
    source = pathlib.Path(__file__).resolve().parents[1].joinpath("notebooks", "colab_quickstart.py").read_text(encoding="utf-8")
    assert "DRIVE_MOUNTED = False" in source
    assert "Google Drive mount failed; continuing with ephemeral /content storage" in source
    assert 'pathlib.Path("/content/Ouroboros")' in source

def test_clone_or_update_repo_fast_forwards_existing_checkout(tmp_path):
    from ouroboros.colab_bootstrap import clone_or_update_repo
    upstream = tmp_path / "upstream"; upstream.mkdir()
    subprocess.run(["git", "init"], cwd=upstream, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=upstream, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=upstream, check=True)
    subprocess.run(["git", "checkout", "-b", "ouroboros"], cwd=upstream, check=True, capture_output=True)
    (upstream / "marker.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker.txt"], cwd=upstream, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=upstream, check=True, capture_output=True)
    checkout = tmp_path / "checkout"
    clone_or_update_repo(checkout, source_url=str(upstream), branch="ouroboros")
    (upstream / "marker.txt").write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker.txt"], cwd=upstream, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=upstream, check=True, capture_output=True)
    clone_or_update_repo(checkout, source_url=str(upstream), branch="ouroboros")
    assert (checkout / "marker.txt").read_text(encoding="utf-8") == "v2\n"

def test_get_colab_secret_optional_returns_empty_without_prompt(monkeypatch):
    from ouroboros.colab_bootstrap import get_colab_secret
    monkeypatch.delenv("OUROBOROS_TEST_ABSENT_KEY", raising=False)
    # required=False must never block on getpass when the secret is absent.
    assert get_colab_secret("OUROBOROS_TEST_ABSENT_KEY", required=False) == ""

def test_get_colab_secret_coerces_non_string_prompt_value(monkeypatch):
    import ouroboros.colab_bootstrap as bootstrap
    monkeypatch.delenv("OUROBOROS_TEST_PROMPT_KEY", raising=False)
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda prompt: {"value": "  abc123  "})
    assert bootstrap.get_colab_secret("OUROBOROS_TEST_PROMPT_KEY") == "abc123"

def test_collect_colab_secrets_prompts_for_groq_by_default(monkeypatch):
    import ouroboros.colab_bootstrap as bootstrap
    prompts = []

    def fake_secret(name, *, required=True):
        prompts.append((name, required))
        if required and name == "GROQ_API_KEY":
            return "gsk_test_key_1234567890"
        return ""

    monkeypatch.setattr(bootstrap, "get_colab_secret", fake_secret)
    secrets = bootstrap.collect_colab_secrets()

    assert secrets["GROQ_API_KEY"] == "gsk_test_key_1234567890"
    assert ("GROQ_API_KEY", True) in prompts
    assert ("OPENROUTER_API_KEY", True) not in prompts

def test_ensure_telegram_bridge_live_installs_enables_and_sets_full_access():
    from ouroboros.colab_bootstrap import ensure_telegram_bridge_live
    calls = []
    def fake_request(method, path, body=None, timeout=None):
        calls.append((method, path, body, timeout))
        if path == "/api/health":
            return 200, {"ok": True}
        if path.endswith("/toggle"):
            return 200, {"ok": True, "enabled": True}
        return 200, {"ok": True}
    status = ensure_telegram_bridge_live(settings={"TELEGRAM_BOT_TOKEN": "x"}, request=fake_request, timeout=5)
    assert status["ok"] is True and status["command_mode_ok"] is True
    assert status["steps"] == ["ready", "installed", "reviewed", "enabled", "command_mode:full_access"]
    triples = [(m, p, b) for (m, p, b, t) in calls]
    assert ("POST", "/api/skills/telegram-bridge/review", None) in triples
    assert ("POST", "/api/skills/telegram-bridge/toggle", {"enabled": True}) in triples
    assert ("POST", "/api/extensions/telegram-bridge/settings/save", {"TELEGRAM_COMMAND_MODE": "full_access"}) in triples
    # Auto-grant must NOT be force-POSTed; it is governed by the persisted setting.
    assert all(p != "/api/owner/auto-grant" for (m, p, b) in triples)
    # Install uses a review-scale timeout, not the default 60s (synchronous tri-model review).
    install_timeout = next(t for (m, p, b, t) in calls if p == "/api/marketplace/ouroboroshub/install")
    assert install_timeout is not None and install_timeout >= 600

def test_ensure_telegram_bridge_live_command_mode_failure_is_not_silent():
    from ouroboros.colab_bootstrap import ensure_telegram_bridge_live
    def fake_request(method, path, body=None, timeout=None):
        if path == "/api/health":
            return 200, {}
        if path.endswith("/toggle"):
            return 200, {"enabled": True}
        if path.endswith("/settings/save"):
            return 404, {"error": "route not found"}
        return 200, {}
    status = ensure_telegram_bridge_live(settings={"TELEGRAM_BOT_TOKEN": "x"}, request=fake_request, timeout=5)
    # Bridge installed+enabled, but command mode not applied — must not claim it silently.
    assert status["ok"] is True
    assert status.get("command_mode_ok") is False
    assert status.get("warning")
    assert "command_mode:full_access" not in status["steps"]

def test_ensure_telegram_bridge_live_retries_transient_review_quorum_failure():
    from ouroboros.colab_bootstrap import ensure_telegram_bridge_live
    review_attempts = 0
    sleeps = []

    def fake_request(method, path, body=None, timeout=None):
        nonlocal review_attempts
        if path == "/api/health":
            return 200, {}
        if path.endswith("/review"):
            review_attempts += 1
            if review_attempts == 1:
                return 200, {"error": "Skill review quorum failure: fewer than 2 reviewers returned parseable findings."}
        if path.endswith("/toggle"):
            return 200, {"enabled": True}
        return 200, {}

    status = ensure_telegram_bridge_live(
        settings={"TELEGRAM_BOT_TOKEN": "x"},
        request=fake_request,
        timeout=5,
        review_retry_delay=75,
        sleep=sleeps.append,
    )

    assert status["ok"] is True
    assert status["steps"] == ["ready", "installed", "review_retry:1", "reviewed", "enabled", "command_mode:full_access"]
    assert review_attempts == 2
    assert sleeps == [75]

def test_ensure_telegram_bridge_live_does_not_retry_non_transient_review_failure():
    from ouroboros.colab_bootstrap import ensure_telegram_bridge_live
    review_attempts = 0
    sleeps = []

    def fake_request(method, path, body=None, timeout=None):
        nonlocal review_attempts
        if path == "/api/health":
            return 200, {}
        if path.endswith("/review"):
            review_attempts += 1
            return 200, {"error": "review denied: missing required skill manifest field"}
        return 200, {}

    status = ensure_telegram_bridge_live(
        settings={"TELEGRAM_BOT_TOKEN": "x"},
        request=fake_request,
        timeout=5,
        sleep=sleeps.append,
    )

    assert status["ok"] is False
    assert "review failed" in status["error"]
    assert review_attempts == 1
    assert sleeps == []

def test_ensure_telegram_bridge_live_bootstrap_reviews_official_bridge_after_quorum_failure(monkeypatch, tmp_path):
    import ouroboros.colab_bootstrap as bootstrap
    review_attempts = 0
    calls = []

    def fake_request(method, path, body=None, timeout=None):
        nonlocal review_attempts
        calls.append((method, path, body))
        if path == "/api/health":
            return 200, {}
        if path.endswith("/review"):
            review_attempts += 1
            return 200, {"error": "Skill review quorum failure: fewer than 2 reviewers returned parseable findings."}
        if path.endswith("/toggle"):
            return 200, {"enabled": True}
        return 200, {}

    fallback_calls = []

    def fake_fallback(data_dir, slug):
        fallback_calls.append((data_dir, slug))
        return {"ok": True, "review_profile": "official_hub", "auto_granted_keys": ["TELEGRAM_BOT_TOKEN"]}

    monkeypatch.setattr(bootstrap, "_bootstrap_review_official_telegram_bridge", fake_fallback)
    status = bootstrap.ensure_telegram_bridge_live(
        settings={"TELEGRAM_BOT_TOKEN": "x"},
        data_dir=tmp_path,
        request=fake_request,
        timeout=5,
        review_retries=1,
        review_retry_delay=75,
        sleep=lambda _seconds: None,
    )

    assert status["ok"] is True
    assert status["steps"] == [
        "ready",
        "installed",
        "review_retry:1",
        "review_bootstrap_fallback",
        "enabled",
        "command_mode:full_access",
    ]
    assert fallback_calls == [(tmp_path, "telegram-bridge")]
    assert status["bootstrap_review"]["auto_granted_keys"] == ["TELEGRAM_BOT_TOKEN"]
    assert ("POST", "/api/skills/telegram-bridge/toggle", {"enabled": True}) in calls

def test_ensure_telegram_bridge_live_handles_already_installed_and_warns_missing_token():
    from ouroboros.colab_bootstrap import ensure_telegram_bridge_live
    def fake_request(method, path, body=None, timeout=None):
        if path == "/api/health":
            return 200, {}
        if path == "/api/marketplace/ouroboroshub/install":
            return 409, {"error": "telegram-bridge is already installed"}
        if path.endswith("/toggle"):
            return 200, {"enabled": True}
        return 200, {}
    status = ensure_telegram_bridge_live(request=fake_request, timeout=5)
    assert status["ok"] is True and "already_installed" in status["steps"]
    assert status.get("warning")  # empty TELEGRAM_BOT_TOKEN warning

def test_ensure_telegram_bridge_live_reports_server_not_ready():
    from ouroboros.colab_bootstrap import ensure_telegram_bridge_live
    status = ensure_telegram_bridge_live(request=lambda *a, **k: (503, {}), timeout=0.2)
    assert status["ok"] is False and "ready" in status["error"]

def test_ensure_telegram_bridge_live_stops_on_enable_error():
    from ouroboros.colab_bootstrap import ensure_telegram_bridge_live
    def fake_request(method, path, body=None, timeout=None):
        if path == "/api/health":
            return 200, {}
        if path.endswith("/toggle"):
            return 409, {"error": "cannot enable until requested key and permission grants are approved"}
        return 200, {}
    status = ensure_telegram_bridge_live(settings={"TELEGRAM_BOT_TOKEN": "x"}, request=fake_request, timeout=5)
    assert status["ok"] is False and "enable failed" in status["error"]
