"""Regression tests for profile-scoped dashboard Channels endpoints.

Before the ``profile`` parameter existed, ``/api/messaging/platforms`` always
read/wrote the dashboard process's own (root) ``.env`` via ``load_env()`` /
``save_env_value()`` — so a dashboard switched to a freshly created profile
still displayed and persisted the ROOT install's messaging credentials.
These tests pin the new behavior: reads and writes land in the REQUESTED
profile's HERMES_HOME, and the dashboard's own profile stays untouched.
"""
import pytest
import yaml

_VALID_BODY_BOT_TOKEN = "987654321:ZYXWVUTSRQPONMLKJIHGFEDCBA_4321"

@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch, _isolate_hermes_home):
    """Isolated default home + one named profile, each with its own .env."""
    from hermes_constants import get_hermes_home
    from hermes_cli import profiles

    default_home = get_hermes_home()
    profiles_root = default_home / "profiles"
    worker_home = profiles_root / "worker_alpha"
    for home in (default_home, worker_home):
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("{}\n", encoding="utf-8")

    (default_home / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=root-token\n", encoding="utf-8"
    )
    (worker_home / ".env").write_text("", encoding="utf-8")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    return {"default": default_home, "worker_alpha": worker_home}

@pytest.fixture
def client(monkeypatch, isolated_profiles):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
    # The dashboard process's os.environ may carry root-install credentials;
    # make sure the scoped path never falls back to them.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c

def _env_field(platform, key):
    return next(f for f in platform["env_vars"] if f["key"] == key)

class TestProfileScopedMessagingReads:
    def test_unknown_profile_returns_404(self, client, isolated_profiles):
        resp = client.get(
            "/api/messaging/platforms", params={"profile": "no_such_profile"}
        )
        assert resp.status_code == 404

