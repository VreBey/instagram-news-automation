"""
src.token_manager birim testleri. Gerçek Meta Graph API'sine hiçbir istek
atılmaz — requests.get mock'lanır. Token durumu tmp_db (izole SQLite) üzerinden
okunup yazılır.
"""

from datetime import datetime, timedelta

import src.token_manager as token_manager_module
from src.token_manager import TokenManager


def _fake_response(mocker, json_data, raise_exc=None):
    resp = mocker.Mock()
    if raise_exc:
        resp.raise_for_status.side_effect = raise_exc
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


# =============================================
# get_current_token
# =============================================

def test_get_current_token_prefers_db_over_env(tmp_db, monkeypatch):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_ACCESS_TOKEN", "env-token")
    tmp_db.set_setting("instagram_access_token", "db-token")

    tm = TokenManager(db=tmp_db)
    assert tm.get_current_token() == "db-token"


def test_get_current_token_falls_back_to_env(tmp_db, monkeypatch):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_ACCESS_TOKEN", "env-token")

    tm = TokenManager(db=tmp_db)
    assert tm.get_current_token() == "env-token"


# =============================================
# refresh_long_lived_token
# =============================================

def test_refresh_long_lived_token_returns_none_without_app_credentials(tmp_db, monkeypatch):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_ID", "")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_SECRET", "")

    tm = TokenManager(db=tmp_db)
    assert tm.refresh_long_lived_token("current-token") is None


def test_refresh_long_lived_token_returns_json_on_success(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_ID", "app-id")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_SECRET", "app-secret")
    mocker.patch(
        "src.token_manager.requests.get",
        return_value=_fake_response(mocker, {"access_token": "new-token", "expires_in": 5184000}),
    )

    tm = TokenManager(db=tmp_db)
    result = tm.refresh_long_lived_token("current-token")

    assert result == {"access_token": "new-token", "expires_in": 5184000}


def test_refresh_long_lived_token_returns_none_on_request_exception(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_ID", "app-id")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_SECRET", "app-secret")
    mocker.patch(
        "src.token_manager.requests.get",
        side_effect=token_manager_module.requests.RequestException("boom"),
    )

    tm = TokenManager(db=tmp_db)
    assert tm.refresh_long_lived_token("current-token") is None


# =============================================
# check_and_refresh_if_needed
# =============================================

def test_check_and_refresh_returns_false_without_current_token(tmp_db, monkeypatch):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_ACCESS_TOKEN", "")

    tm = TokenManager(db=tmp_db)
    assert tm.check_and_refresh_if_needed() is False


def test_check_and_refresh_skips_when_not_close_to_expiry(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_ACCESS_TOKEN", "current-token")
    monkeypatch.setattr(token_manager_module, "TOKEN_REFRESH_WARNING_DAYS", 10)
    far_future = datetime.now() + timedelta(days=30)
    tmp_db.set_setting("instagram_token_expires_at", far_future.strftime("%Y-%m-%d %H:%M:%S"))
    get_mock = mocker.patch("src.token_manager.requests.get")

    tm = TokenManager(db=tmp_db)
    assert tm.check_and_refresh_if_needed() is False
    get_mock.assert_not_called()


def test_check_and_refresh_refreshes_when_close_to_expiry(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_ACCESS_TOKEN", "current-token")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_ID", "app-id")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_SECRET", "app-secret")
    monkeypatch.setattr(token_manager_module, "TOKEN_REFRESH_WARNING_DAYS", 10)
    soon = datetime.now() + timedelta(days=3)
    tmp_db.set_setting("instagram_token_expires_at", soon.strftime("%Y-%m-%d %H:%M:%S"))
    mocker.patch(
        "src.token_manager.requests.get",
        return_value=_fake_response(mocker, {"access_token": "refreshed-token", "expires_in": 5184000}),
    )

    tm = TokenManager(db=tmp_db)
    assert tm.check_and_refresh_if_needed() is True
    assert tmp_db.get_setting("instagram_access_token") == "refreshed-token"
    assert tmp_db.get_setting("instagram_token_expires_at") is not None


def test_check_and_refresh_attempts_when_expiry_unknown(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_ACCESS_TOKEN", "current-token")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_ID", "app-id")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_SECRET", "app-secret")
    mocker.patch(
        "src.token_manager.requests.get",
        return_value=_fake_response(mocker, {"access_token": "refreshed-token", "expires_in": 5184000}),
    )

    tm = TokenManager(db=tmp_db)
    assert tm.check_and_refresh_if_needed() is True


def test_check_and_refresh_returns_false_when_refresh_fails(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_ACCESS_TOKEN", "current-token")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_ID", "app-id")
    monkeypatch.setattr(token_manager_module, "INSTAGRAM_APP_SECRET", "app-secret")
    mocker.patch(
        "src.token_manager.requests.get",
        side_effect=token_manager_module.requests.RequestException("boom"),
    )

    tm = TokenManager(db=tmp_db)
    assert tm.check_and_refresh_if_needed() is False


# =============================================
# get_expiry_status
# =============================================

def test_get_expiry_status_unknown_when_not_set(tmp_db):
    tm = TokenManager(db=tmp_db)
    status = tm.get_expiry_status()
    assert status == {"known": False, "expires_at": None, "days_remaining": None, "warning": False}


def test_get_expiry_status_warns_when_close_to_expiry(tmp_db, monkeypatch):
    monkeypatch.setattr(token_manager_module, "TOKEN_REFRESH_WARNING_DAYS", 10)
    soon = datetime.now() + timedelta(days=3)
    tmp_db.set_setting("instagram_token_expires_at", soon.strftime("%Y-%m-%d %H:%M:%S"))

    tm = TokenManager(db=tmp_db)
    status = tm.get_expiry_status()
    assert status["known"] is True
    assert status["warning"] is True
    assert status["days_remaining"] in (2, 3)


def test_get_expiry_status_no_warning_when_far_from_expiry(tmp_db, monkeypatch):
    monkeypatch.setattr(token_manager_module, "TOKEN_REFRESH_WARNING_DAYS", 10)
    far_future = datetime.now() + timedelta(days=30)
    tmp_db.set_setting("instagram_token_expires_at", far_future.strftime("%Y-%m-%d %H:%M:%S"))

    tm = TokenManager(db=tmp_db)
    status = tm.get_expiry_status()
    assert status["known"] is True
    assert status["warning"] is False
