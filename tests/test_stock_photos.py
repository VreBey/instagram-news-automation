"""
src.stock_photos birim testleri. Gerçek Pexels API'sine hiçbir istek atılmaz —
requests.get mock'lanır. Dosya indirmeleri tmp_path'e yönlendirilir (gerçek
assets/stock_cache dizinine yazılmaz).
"""

import src.stock_photos as stock_photos


def _fake_search_response(mocker, photos):
    resp = mocker.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"photos": photos}
    return resp


def _fake_download_response(mocker, content=b"fake-image-bytes"):
    resp = mocker.Mock()
    resp.raise_for_status.return_value = None
    resp.content = content
    return resp


def test_fetch_stock_photo_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(stock_photos, "PEXELS_API_KEY", "")
    assert stock_photos.fetch_stock_photo("ai", seed=1) is None


def test_fetch_stock_photo_returns_none_on_no_results(monkeypatch, mocker):
    monkeypatch.setattr(stock_photos, "PEXELS_API_KEY", "fake-key")
    mocker.patch("src.stock_photos.requests.get",
                 return_value=_fake_search_response(mocker, []))
    assert stock_photos.fetch_stock_photo("ai", seed=1) is None


def test_fetch_stock_photo_returns_none_on_request_exception(monkeypatch, mocker):
    monkeypatch.setattr(stock_photos, "PEXELS_API_KEY", "fake-key")
    mocker.patch("src.stock_photos.requests.get",
                 side_effect=stock_photos.requests.RequestException("boom"))
    assert stock_photos.fetch_stock_photo("ai", seed=1) is None


def test_fetch_stock_photo_downloads_and_caches(monkeypatch, mocker, tmp_path):
    monkeypatch.setattr(stock_photos, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(stock_photos, "STOCK_PHOTO_CACHE_DIR", tmp_path)

    photos = [{"id": 42, "src": {"large2x": "https://example.com/42.jpg"}}]
    search_resp = _fake_search_response(mocker, photos)
    download_resp = _fake_download_response(mocker)
    get_mock = mocker.patch("src.stock_photos.requests.get",
                             side_effect=[search_resp, download_resp])

    path = stock_photos.fetch_stock_photo("ai", seed=1)

    assert path is not None
    assert (tmp_path / "42.jpg").exists()
    assert get_mock.call_count == 2


def test_fetch_stock_photo_uses_cache_on_second_call(monkeypatch, mocker, tmp_path):
    monkeypatch.setattr(stock_photos, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(stock_photos, "STOCK_PHOTO_CACHE_DIR", tmp_path)

    photos = [{"id": 42, "src": {"large2x": "https://example.com/42.jpg"}}]

    # İlk çağrı: arama + indirme (2 istek)
    mocker.patch("src.stock_photos.requests.get",
                 side_effect=[_fake_search_response(mocker, photos), _fake_download_response(mocker)])
    first_path = stock_photos.fetch_stock_photo("ai", seed=1)
    assert first_path is not None

    # İkinci çağrı: sadece arama isteği yapılmalı, indirme YOK (önbellekten dönmeli)
    get_mock = mocker.patch("src.stock_photos.requests.get",
                             side_effect=[_fake_search_response(mocker, photos)])
    second_path = stock_photos.fetch_stock_photo("ai", seed=1)

    assert second_path == first_path
    assert get_mock.call_count == 1


def test_fetch_stock_photo_deterministic_query_by_seed(monkeypatch, mocker, tmp_path):
    monkeypatch.setattr(stock_photos, "PEXELS_API_KEY", "fake-key")
    monkeypatch.setattr(stock_photos, "STOCK_PHOTO_CACHE_DIR", tmp_path)

    photos = [{"id": 1, "src": {"large2x": "https://example.com/1.jpg"}}]
    get_mock = mocker.patch(
        "src.stock_photos.requests.get",
        side_effect=[_fake_search_response(mocker, photos), _fake_download_response(mocker)]
    )

    stock_photos.fetch_stock_photo("ai", seed=3)

    used_query = get_mock.call_args_list[0].kwargs["params"]["query"]
    expected_query = stock_photos.PEXELS_QUERY_TERMS["ai"][3 % len(stock_photos.PEXELS_QUERY_TERMS["ai"])]
    assert used_query == expected_query
