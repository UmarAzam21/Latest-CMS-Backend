from types import SimpleNamespace

import pytest

from app import auth, main


@pytest.mark.parametrize("value", [None, "", "your-secret-key-change-this"])
def test_app_refuses_to_run_without_a_real_jwt_secret(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET_KEY", value)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        auth._require_secret_key()


def test_configured_jwt_secret_is_used(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "a-long-random-value")

    assert auth._require_secret_key() == "a-long-random-value"


@pytest.mark.parametrize(
    "configured",
    [
        {},
        {"DEFAULT_SUPERADMIN_EMAIL": "owner@example.com"},
        {"DEFAULT_SUPERADMIN_PASSWORD": "a-strong-password"},
    ],
)
def test_no_superadmin_is_created_without_configured_credentials(monkeypatch, configured):
    monkeypatch.delenv("DEFAULT_SUPERADMIN_EMAIL", raising=False)
    monkeypatch.delenv("DEFAULT_SUPERADMIN_PASSWORD", raising=False)
    for name, value in configured.items():
        monkeypatch.setenv(name, value)

    added = []

    class FakeQuery:
        def filter(self, *_):
            return self

        def first(self):
            return None

    fake_db = SimpleNamespace(
        query=lambda _: FakeQuery(),
        add=added.append,
        commit=lambda: None,
        close=lambda: None,
    )
    monkeypatch.setattr(main, "get_db", lambda: iter([fake_db]))

    main.ensure_default_superadmin()

    assert added == []
