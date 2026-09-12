"""
config._env() birim testleri. .env.example'dan kopyalanip degeri
degistirilmemis "your_xxx_here" placeholder'larinin gercek bir anahtarmis
gibi API'lere gonderilmesini (ve 401/403 log gurultusunu) onlemek icin
eklendi -- gercek vaka: NEWS_API_KEY/CURRENTS_API_KEY.
"""

import config


def test_env_treats_placeholder_as_empty(monkeypatch):
    monkeypatch.setenv("TEST_PLACEHOLDER_KEY", "your_newsapi_key_here")
    assert config._env("TEST_PLACEHOLDER_KEY") == ""


def test_env_is_case_insensitive_for_placeholder_prefix(monkeypatch):
    monkeypatch.setenv("TEST_PLACEHOLDER_KEY", "YOUR_api_key_here")
    assert config._env("TEST_PLACEHOLDER_KEY") == ""


def test_env_returns_real_value_unchanged(monkeypatch):
    monkeypatch.setenv("TEST_REAL_KEY", "sk-real-abc123")
    assert config._env("TEST_REAL_KEY") == "sk-real-abc123"


def test_env_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("TEST_UNSET_KEY", raising=False)
    assert config._env("TEST_UNSET_KEY", "fallback") == "fallback"


def test_env_returns_empty_when_unset_and_no_default(monkeypatch):
    monkeypatch.delenv("TEST_UNSET_KEY", raising=False)
    assert config._env("TEST_UNSET_KEY") == ""
