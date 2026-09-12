"""
src.instagram_client birim testleri. Gerçek Instagram Graph API'ye veya
Cloudinary'ye hiçbir istek atılmaz — tüm HTTP çağrıları ve Cloudinary
upload'ı mock'lanır. time.sleep de mock'lanarak _wait_for_container testleri
gerçek zaman geçirmeden çalışır.
"""

import src.instagram_client as instagram_client_module
from src.instagram_client import InstagramClient


def _client(**kwargs):
    kwargs.setdefault("access_token", "fake-token")
    kwargs.setdefault("user_id", "1234567890")
    return InstagramClient(**kwargs)


def _fake_response(mocker, json_data, raise_exc=None):
    resp = mocker.Mock()
    if raise_exc:
        resp.raise_for_status.side_effect = raise_exc
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


# =============================================
# is_configured / _resolve_token
# =============================================

def test_is_configured_true_with_token_and_user_id():
    assert _client().is_configured() is True


def test_is_configured_false_without_token(tmp_db, monkeypatch):
    # access_token="" is falsy, so the constructor falls back to _resolve_token()
    # which would otherwise read the *real* .env INSTAGRAM_ACCESS_TOKEN and a
    # real Database() — both must be neutralized so this stays a pure unit test.
    monkeypatch.setattr(instagram_client_module, "INSTAGRAM_ACCESS_TOKEN", "")
    client = InstagramClient(access_token="", user_id="1234567890", db=tmp_db)
    assert client.is_configured() is False


def test_resolve_token_prefers_db_setting(tmp_db, monkeypatch):
    monkeypatch.setattr(instagram_client_module, "INSTAGRAM_ACCESS_TOKEN", "env-token")
    tmp_db.set_setting("instagram_access_token", "db-token")

    client = InstagramClient(user_id="123", db=tmp_db)
    assert client.access_token == "db-token"


def test_resolve_token_falls_back_to_env(tmp_db, monkeypatch):
    monkeypatch.setattr(instagram_client_module, "INSTAGRAM_ACCESS_TOKEN", "env-token")

    client = InstagramClient(user_id="123", db=tmp_db)
    assert client.access_token == "env-token"


# =============================================
# publish_post
# =============================================

def test_publish_post_returns_error_when_not_configured(tmp_db, monkeypatch):
    monkeypatch.setattr(instagram_client_module, "INSTAGRAM_ACCESS_TOKEN", "")
    client = InstagramClient(access_token="", user_id="1234567890", db=tmp_db)
    result = client.publish_post("https://example.com/img.png")
    assert result == {"success": False, "error": "API yapılandırılmamış"}


def test_publish_post_success(mocker):
    client = _client()
    mocker.patch.object(client, "_create_media_container", return_value="container-1")
    mocker.patch.object(client, "_wait_for_container", return_value=True)
    mocker.patch.object(client, "_publish_media", return_value={
        "success": True, "media_id": "media-1", "permalink": "https://instagram.com/p/x"
    })

    result = client.publish_post("https://example.com/img.png", caption="Merhaba", hashtags=["#ai"])

    assert result["success"] is True
    assert result["media_id"] == "media-1"


def test_publish_post_fails_when_container_not_created(mocker):
    client = _client()
    mocker.patch.object(client, "_create_media_container", return_value=None)

    result = client.publish_post("https://example.com/img.png")
    assert result == {"success": False, "error": "Container oluşturulamadı"}


def test_publish_post_fails_when_container_never_ready(mocker):
    client = _client()
    mocker.patch.object(client, "_create_media_container", return_value="container-1")
    mocker.patch.object(client, "_wait_for_container", return_value=False)

    result = client.publish_post("https://example.com/img.png")
    assert result == {"success": False, "error": "Container hazır olmadı"}


# =============================================
# publish_carousel
# =============================================

def test_publish_carousel_requires_at_least_two_images():
    result = _client().publish_carousel(["https://example.com/1.png"])
    assert result == {"success": False, "error": "Carousel için en az 2 görsel gerekli"}


def test_publish_carousel_success(mocker):
    client = _client()
    mocker.patch.object(client, "_create_media_container", side_effect=["child-1", "child-2"])
    mocker.patch.object(client, "_wait_for_container", return_value=True)
    mocker.patch.object(client, "_create_carousel_container", return_value="carousel-1")
    mocker.patch.object(client, "_publish_media", return_value={"success": True, "media_id": "media-1"})

    result = client.publish_carousel(
        ["https://example.com/1.png", "https://example.com/2.png"]
    )
    assert result["success"] is True


def test_publish_carousel_fails_when_not_enough_children_created(mocker):
    client = _client()
    mocker.patch.object(client, "_create_media_container", side_effect=[None, None])
    mocker.patch.object(client, "_wait_for_container", return_value=True)

    result = client.publish_carousel(
        ["https://example.com/1.png", "https://example.com/2.png"]
    )
    assert result == {"success": False, "error": "Yeterli carousel öğesi oluşturulamadı"}


# =============================================
# publish_story / publish_reels
# =============================================

def test_publish_story_success(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "post",
        return_value=_fake_response(mocker, {"id": "container-1"}),
    )
    mocker.patch.object(client, "_wait_for_container", return_value=True)
    mocker.patch.object(client, "_publish_media", return_value={"success": True, "media_id": "media-1"})

    result = client.publish_story("https://example.com/story.png")
    assert result["success"] is True


def test_publish_story_fails_without_container_id(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "post",
        return_value=_fake_response(mocker, {}),
    )

    result = client.publish_story("https://example.com/story.png")
    assert result == {"success": False, "error": "Story container oluşturulamadı"}


def test_publish_reels_success(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "post",
        return_value=_fake_response(mocker, {"id": "container-1"}),
    )
    mocker.patch.object(client, "_wait_for_container", return_value=True)
    mocker.patch.object(client, "_publish_media", return_value={"success": True, "media_id": "media-1"})

    result = client.publish_reels("https://example.com/reel.mp4", caption="Selam")
    assert result["success"] is True


def test_publish_reels_fails_when_video_never_processed(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "post",
        return_value=_fake_response(mocker, {"id": "container-1"}),
    )
    mocker.patch.object(client, "_wait_for_container", return_value=False)

    result = client.publish_reels("https://example.com/reel.mp4")
    assert result == {"success": False, "error": "Reels video işlenemedi"}


# =============================================
# _wait_for_container
# =============================================

def test_wait_for_container_returns_true_when_finished(mocker):
    client = _client()
    mocker.patch("src.instagram_client.time.sleep")
    mocker.patch.object(
        client.session, "get",
        return_value=_fake_response(mocker, {"status_code": "FINISHED"}),
    )

    assert client._wait_for_container("container-1", max_attempts=3) is True


def test_wait_for_container_returns_false_on_error_status(mocker):
    client = _client()
    mocker.patch("src.instagram_client.time.sleep")
    mocker.patch.object(
        client.session, "get",
        return_value=_fake_response(mocker, {"status_code": "ERROR", "status": "boom"}),
    )

    assert client._wait_for_container("container-1", max_attempts=3) is False


def test_wait_for_container_times_out(mocker):
    client = _client()
    mocker.patch("src.instagram_client.time.sleep")
    mocker.patch.object(
        client.session, "get",
        return_value=_fake_response(mocker, {"status_code": "IN_PROGRESS"}),
    )

    assert client._wait_for_container("container-1", max_attempts=2) is False


# =============================================
# _publish_media
# =============================================

def test_publish_media_success_includes_permalink(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "post",
        return_value=_fake_response(mocker, {"id": "media-1"}),
    )
    mocker.patch.object(client, "_get_permalink", return_value="https://instagram.com/p/x")

    result = client._publish_media("container-1")
    assert result == {"success": True, "media_id": "media-1", "permalink": "https://instagram.com/p/x"}


def test_publish_media_extracts_api_error_message(mocker):
    import requests as real_requests

    client = _client()
    error_response = mocker.Mock()
    error_response.json.return_value = {"error": {"message": "Token geçersiz"}}
    http_error = real_requests.RequestException("400")
    http_error.response = error_response

    post_response = mocker.Mock()
    post_response.raise_for_status.side_effect = http_error
    mocker.patch.object(client.session, "post", return_value=post_response)

    result = client._publish_media("container-1")
    assert result == {"success": False, "error": "Token geçersiz"}


# =============================================
# _build_caption
# =============================================

def test_build_caption_appends_hashtags():
    caption = InstagramClient._build_caption("Merhaba", ["#ai", "#gaming"])
    assert caption == "Merhaba\n\n#ai #gaming"


def test_build_caption_truncates_over_2200_chars():
    long_caption = "x" * 3000
    result = InstagramClient._build_caption(long_caption)
    assert len(result) == 2200
    assert result.endswith("...")


# =============================================
# upload_media_to_host
# =============================================

def test_upload_media_to_host_uses_cloudinary_when_configured(monkeypatch, mocker, tmp_path):
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_AVAILABLE", True)
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_CLOUD_NAME", "cloud")
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_API_KEY", "key")
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_API_SECRET", "secret")
    mocker.patch.object(instagram_client_module.cloudinary, "config")
    mocker.patch.object(
        instagram_client_module.cloudinary.uploader, "upload",
        return_value={"secure_url": "https://cdn.example.com/img.png"},
    )
    mocker.patch.object(instagram_client_module.requests, "head", return_value=mocker.Mock(status_code=200))

    local_file = tmp_path / "img.png"
    local_file.write_bytes(b"fake")

    result = _client().upload_media_to_host(str(local_file))
    assert result == "https://cdn.example.com/img.png"


def test_wait_for_url_ready_returns_true_immediately_on_200(mocker):
    client = _client()
    mocker.patch.object(instagram_client_module.requests, "head", return_value=mocker.Mock(status_code=200))
    sleep_mock = mocker.patch("src.instagram_client.time.sleep")

    assert client._wait_for_url_ready("https://cdn.example.com/img.png") is True
    sleep_mock.assert_not_called()


def test_wait_for_url_ready_retries_then_succeeds(mocker):
    """
    Gerçek olay: Cloudinary'nin secure_url'i başarıyla dönmesine rağmen, o
    URL Instagram'ın (Meta sunucularının) erişebileceği şekilde CDN'de hemen
    tam yayılmamış olabiliyordu — "Medya URI'sinden alınamadı" hatasıyla
    paylaşım düşüyordu. Birkaç deneme sonrası hazır olursa True dönmeli.
    """
    client = _client()
    mocker.patch.object(
        instagram_client_module.requests, "head",
        side_effect=[mocker.Mock(status_code=404), mocker.Mock(status_code=200)]
    )
    mocker.patch("src.instagram_client.time.sleep")

    assert client._wait_for_url_ready("https://cdn.example.com/img.png", max_retries=5) is True


def test_wait_for_url_ready_gives_up_after_max_retries(mocker):
    client = _client()
    mocker.patch.object(instagram_client_module.requests, "head", return_value=mocker.Mock(status_code=404))
    mocker.patch("src.instagram_client.time.sleep")

    assert client._wait_for_url_ready("https://cdn.example.com/img.png", max_retries=3) is False


def test_upload_media_to_host_still_returns_url_when_verification_fails(monkeypatch, mocker, tmp_path):
    """CDN doğrulaması başarısız olsa bile URL yine de döndürülmeli (best-effort) —
    doğrulama sadece erken uyarı amaçlı, paylaşımı tamamen engellememeli."""
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_AVAILABLE", True)
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_CLOUD_NAME", "cloud")
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_API_KEY", "key")
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_API_SECRET", "secret")
    mocker.patch.object(instagram_client_module.cloudinary, "config")
    mocker.patch.object(
        instagram_client_module.cloudinary.uploader, "upload",
        return_value={"secure_url": "https://cdn.example.com/img.png"},
    )
    mocker.patch.object(instagram_client_module.requests, "head", return_value=mocker.Mock(status_code=404))
    mocker.patch("src.instagram_client.time.sleep")

    local_file = tmp_path / "img.png"
    local_file.write_bytes(b"fake")

    result = _client().upload_media_to_host(str(local_file))
    assert result == "https://cdn.example.com/img.png"


def test_upload_media_to_host_falls_back_to_media_host_url(monkeypatch, tmp_path):
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_AVAILABLE", False)
    monkeypatch.setattr(instagram_client_module, "MEDIA_HOST_URL", "https://static.example.com")

    local_file = tmp_path / "img.png"
    local_file.write_bytes(b"fake")

    result = _client().upload_media_to_host(str(local_file))
    assert result == "https://static.example.com/img.png"


def test_upload_media_to_host_returns_none_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(instagram_client_module, "CLOUDINARY_AVAILABLE", False)
    monkeypatch.setattr(instagram_client_module, "MEDIA_HOST_URL", "")

    local_file = tmp_path / "img.png"
    local_file.write_bytes(b"fake")

    result = _client().upload_media_to_host(str(local_file))
    assert result is None


# =============================================
# get_account_info / get_media_insights
# =============================================

def test_get_account_info_returns_none_when_not_configured(tmp_db, monkeypatch):
    monkeypatch.setattr(instagram_client_module, "INSTAGRAM_ACCESS_TOKEN", "")
    client = InstagramClient(access_token="", user_id="1234567890", db=tmp_db)
    assert client.get_account_info() is None


def test_get_account_info_returns_json(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "get",
        return_value=_fake_response(mocker, {"username": "test"}),
    )
    assert client.get_account_info() == {"username": "test"}


def test_get_media_insights_maps_metric_values(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "get",
        return_value=_fake_response(mocker, {
            "data": [
                {"name": "impressions", "values": [{"value": 100}]},
                {"name": "reach", "values": [{"value": 80}]},
            ]
        }),
    )

    result = client.get_media_insights("media-1", post_type="post")
    assert result == {"impressions": 100, "reach": 80}


def test_get_media_insights_returns_none_on_request_exception(mocker):
    import requests as real_requests

    client = _client()
    mocker.patch.object(client.session, "get", side_effect=real_requests.RequestException("boom"))

    assert client.get_media_insights("media-1") is None


# =============================================
# get_rate_limit_status
# =============================================

def test_get_rate_limit_status_parses_quota_into_simple_dict(mocker):
    client = _client()
    mocker.patch.object(
        client.session, "get",
        return_value=_fake_response(mocker, {
            "data": [{"config": {"quota_total": 25, "quota_duration": 86400}, "quota_usage": 4}]
        }),
    )

    result = client.get_rate_limit_status()

    assert result == {"used": 4, "total": 25, "remaining": 21}


def test_get_rate_limit_status_returns_none_when_unconfigured(mocker):
    """
    ÖNEMLİ: access_token/user_id="" ile _client() çağırmak İSTENMEZ —
    InstagramClient.__init__ boş string'i falsy sayıp GERÇEK .env
    yapılandırmasına (varsa) düşer, bu da testin yanlışlıkla gerçek
    Instagram API'sine istek atmasına yol açar (bkz. gerçek olay: bu test
    ilk yazıldığında tam olarak bunu yaptı). is_configured() doğrudan
    mock'lanarak bu risk tamamen ortadan kaldırılıyor.
    """
    client = _client()
    mocker.patch.object(client, "is_configured", return_value=False)
    get_mock = mocker.patch.object(client.session, "get")

    assert client.get_rate_limit_status() is None
    get_mock.assert_not_called()


def test_get_rate_limit_status_returns_none_on_request_exception(mocker):
    import requests as real_requests

    client = _client()
    mocker.patch.object(client.session, "get", side_effect=real_requests.RequestException("boom"))

    assert client.get_rate_limit_status() is None


def test_get_rate_limit_status_returns_none_on_empty_data(mocker):
    client = _client()
    mocker.patch.object(client.session, "get", return_value=_fake_response(mocker, {"data": []}))

    assert client.get_rate_limit_status() is None


def test_get_rate_limit_status_remaining_never_negative(mocker):
    """Kota aşılmışsa (used > total) remaining 0'ın altına düşmemeli."""
    client = _client()
    mocker.patch.object(
        client.session, "get",
        return_value=_fake_response(mocker, {
            "data": [{"config": {"quota_total": 25}, "quota_usage": 30}]
        }),
    )

    result = client.get_rate_limit_status()

    assert result == {"used": 30, "total": 25, "remaining": 0}
