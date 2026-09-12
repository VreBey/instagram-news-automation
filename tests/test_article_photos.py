"""
src.article_photos birim testleri. Gerçek ağ isteği atılmaz — requests.get
mock'lanır. PIL decode/boyut kontrolü gerçek görsel byte'ları ile test edilir
(tests/test_stock_photos.py ile aynı mock yaklaşımı).
"""

import io
import pytest

from PIL import Image

import src.article_photos as article_photos


def _fake_image_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (100, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


def _fake_response(mocker, content: bytes, content_type: str = "image/jpeg"):
    resp = mocker.Mock()
    resp.raise_for_status.return_value = None
    resp.headers = {"content-type": content_type}
    resp.content = content
    return resp


def test_fetch_article_photo_returns_none_without_url():
    assert article_photos.fetch_article_photo(None) is None
    assert article_photos.fetch_article_photo("") is None


def test_fetch_article_photo_returns_none_on_request_exception(mocker):
    mocker.patch(
        "src.article_photos.requests.get",
        side_effect=article_photos.requests.RequestException("boom"),
    )
    assert article_photos.fetch_article_photo("https://example.com/a.jpg") is None


def test_fetch_article_photo_returns_none_on_non_image_content_type(mocker):
    resp = _fake_response(mocker, b"<html>not an image</html>", content_type="text/html")
    mocker.patch("src.article_photos.requests.get", return_value=resp)
    assert article_photos.fetch_article_photo("https://example.com/a.jpg") is None


def test_fetch_article_photo_returns_none_on_corrupt_image(mocker):
    resp = _fake_response(mocker, b"this-is-not-a-real-image")
    mocker.patch("src.article_photos.requests.get", return_value=resp)
    assert article_photos.fetch_article_photo("https://example.com/a.jpg") is None


def test_fetch_article_photo_returns_none_when_too_small(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(article_photos, "ARTICLE_PHOTO_CACHE_DIR", tmp_path)
    resp = _fake_response(mocker, _fake_image_bytes(100, 100))
    mocker.patch("src.article_photos.requests.get", return_value=resp)
    assert article_photos.fetch_article_photo("https://example.com/logo.jpg") is None


def test_fetch_article_photo_downloads_and_caches(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(article_photos, "ARTICLE_PHOTO_CACHE_DIR", tmp_path)
    resp = _fake_response(mocker, _fake_image_bytes(800, 1200))
    get_mock = mocker.patch("src.article_photos.requests.get", return_value=resp)

    path = article_photos.fetch_article_photo("https://example.com/photo.jpg")

    assert path is not None
    assert path.endswith(".jpg")
    assert get_mock.call_count == 1


def test_fetch_article_photo_uses_cache_on_second_call(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(article_photos, "ARTICLE_PHOTO_CACHE_DIR", tmp_path)
    resp = _fake_response(mocker, _fake_image_bytes(800, 1200))
    mocker.patch("src.article_photos.requests.get", return_value=resp)

    first_path = article_photos.fetch_article_photo("https://example.com/photo.jpg")

    get_mock = mocker.patch("src.article_photos.requests.get")
    second_path = article_photos.fetch_article_photo("https://example.com/photo.jpg")

    assert second_path == first_path
    get_mock.assert_not_called()


# ---- kabul eşiği: neyin "logo" sayıldığı ----

def test_wide_article_photo_is_accepted(mocker, tmp_path, monkeypatch):
    """
    Geniş ama kısa haber fotoğrafı kabul edilmeli. Eski kural "her iki kenar
    ≥ 400" idi ve canlı ölçümde (4 Ağustos 2026) buna takılanların tamamı
    gerçek haber fotoğrafıydı: 690x388, 660x330, 600x315, 550x315. Hepsi
    stok fotoğrafa düşüyordu — kullanıcının "gerçek görüntü kullanmıyor"
    şikayetinin en büyük tek sebebi buydu.
    """
    monkeypatch.setattr(article_photos, "ARTICLE_PHOTO_CACHE_DIR", tmp_path)
    for w, h in ((690, 388), (660, 330), (600, 315), (550, 315)):
        resp = _fake_response(mocker, _fake_image_bytes(w, h))
        mocker.patch("src.article_photos.requests.get", return_value=resp)
        assert article_photos.fetch_article_photo(
            f"https://example.com/foto_{w}x{h}.jpg") is not None, f"{w}x{h} reddedildi"


def test_thumbnail_and_icon_are_still_rejected(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(article_photos, "ARTICLE_PHOTO_CACHE_DIR", tmp_path)
    for w, h in ((300, 168), (64, 64), (1200, 90)):
        resp = _fake_response(mocker, _fake_image_bytes(w, h))
        mocker.patch("src.article_photos.requests.get", return_value=resp)
        assert article_photos.fetch_article_photo(
            f"https://example.com/kucuk_{w}x{h}.jpg") is None, f"{w}x{h} kabul edildi"


# ---- küçültülmüş adresi tam boya yükseltme ----

def test_full_size_variant_strips_downscale_params():
    assert article_photos._full_size_variant(
        "https://www.gamespot.com/uploads/a.jpg?w=300"
    ) == "https://www.gamespot.com/uploads/a.jpg"
    assert article_photos._full_size_variant(
        "https://cdn.x/a.png?width=690&quality=85&format=jpg"
    ) == "https://cdn.x/a.png?quality=85&format=jpg"


def test_full_size_variant_returns_none_when_nothing_to_strip():
    assert article_photos._full_size_variant("https://cdn.x/a.jpg") is None
    assert article_photos._full_size_variant("https://cdn.x/a.jpg?v=2") is None


def test_downscaled_url_is_upgraded_before_original(mocker, tmp_path, monkeypatch):
    """
    Besleme küçük varyantı veriyor (`?w=300`) ama aynı adres parametresiz
    tam boy görseli döndürüyor. Önce tam boy denenmeli.
    """
    monkeypatch.setattr(article_photos, "ARTICLE_PHOTO_CACHE_DIR", tmp_path)
    istenen = []

    def _get(url, **kwargs):
        istenen.append(url)
        boyut = (1600, 900) if "?" not in url else (300, 168)
        return _fake_response(mocker, _fake_image_bytes(*boyut))

    mocker.patch("src.article_photos.requests.get", side_effect=_get)

    yol = article_photos.fetch_article_photo("https://www.gamespot.com/a.jpg?w=300")

    assert yol is not None
    assert istenen[0] == "https://www.gamespot.com/a.jpg"
    assert Image.open(yol).size == (1600, 900)


def test_falls_back_to_original_url_when_full_size_fails(mocker, tmp_path, monkeypatch):
    """Bazı CDN'ler parametresiz isteği reddediyor — özgün adres denenmeli."""
    monkeypatch.setattr(article_photos, "ARTICLE_PHOTO_CACHE_DIR", tmp_path)

    def _get(url, **kwargs):
        if "?" not in url:
            raise article_photos.requests.RequestException("403")
        return _fake_response(mocker, _fake_image_bytes(690, 388))

    mocker.patch("src.article_photos.requests.get", side_effect=_get)

    assert article_photos.fetch_article_photo("https://cdn.x/a.jpg?width=690") is not None


# ---- og:image geri çekilmesi (beslemede image_url yokken) ----

def test_extract_og_image_handles_attribute_order_variants():
    """Siteler meta özniteliklerini farklı sırada yazıyor — ikisi de çalışmalı."""
    assert article_photos.extract_og_image(
        '<head><meta property="og:image" content="https://cdn.x/a.jpg"></head>'
    ) == "https://cdn.x/a.jpg"
    assert article_photos.extract_og_image(
        '<head><meta content="https://cdn.x/b.jpg" property="og:image"/></head>'
    ) == "https://cdn.x/b.jpg"


def test_extract_og_image_falls_back_to_twitter_image():
    assert article_photos.extract_og_image(
        '<head><meta name="twitter:image" content="https://cdn.x/t.jpg"></head>'
    ) == "https://cdn.x/t.jpg"


def test_extract_og_image_normalizes_protocol_relative_url():
    assert article_photos.extract_og_image(
        '<head><meta property="og:image" content="//cdn.x/c.jpg"></head>'
    ) == "https://cdn.x/c.jpg"


def test_extract_og_image_resolves_relative_url():
    """
    Bazı siteler og:image'ı site köküne göreli yazıyor. Canlı ölçümde
    konami.com `content="/games_cms/promo/na/uploads/vol2.jpg"` veriyordu:
    etiket bulunuyor, ama "http" ile başlamadığı için atılıyor ve gönderi
    jenerik stok fotoğrafa düşüyordu.
    """
    assert article_photos.extract_og_image(
        '<head><meta property="og:image" content="/uploads/kapak.jpg"></head>',
        base_url="https://www.konami.com/games/us/en/topics/3095/",
    ) == "https://www.konami.com/uploads/kapak.jpg"


def test_extract_og_image_keeps_dropping_relative_url_without_base():
    """Taban adres yoksa göreli adres çözülemez — tahmin edilmemeli."""
    assert article_photos.extract_og_image(
        '<head><meta property="og:image" content="/uploads/kapak.jpg"></head>'
    ) is None


def test_extract_og_image_returns_none_when_absent():
    assert article_photos.extract_og_image("<head><title>yok</title></head>") is None


def test_fetch_article_photo_from_page_rejects_non_html(mocker):
    resp = mocker.Mock()
    resp.raise_for_status.return_value = None
    resp.headers = {"content-type": "application/pdf"}
    mocker.patch("src.article_photos.requests.get", return_value=resp)
    assert article_photos.fetch_article_photo_from_page("https://example.com/a") is None


def test_fetch_article_photo_from_page_returns_none_for_bad_url():
    assert article_photos.fetch_article_photo_from_page(None) is None
    assert article_photos.fetch_article_photo_from_page("not-a-url") is None


# =============================================
# og:image öznitelik varyasyonları
# =============================================

@pytest.mark.parametrize("html,beklenen", [
    # Open Graph spesifikasyonu `property` diyor...
    ('<meta property="og:image" content="https://a.com/1.png"/>', "https://a.com/1.png"),
    # ...ama pek çok CMS `name` üretiyor. Canlı ölçümde rpgsite.net tam
    # olarak bunu yapıyordu ve yalnızca `property` arandığı için görsel hiç
    # bulunamıyor, gönderi stok fotoğrafa/gradyana düşüyordu (5 Ağustos 2026).
    ('<meta name="og:image" content="https://a.com/2.png"/>', "https://a.com/2.png"),
    ('<meta content="https://a.com/3.png" name="og:image"/>', "https://a.com/3.png"),
    ('<meta property="og:image:secure_url" content="https://a.com/4.png"/>', "https://a.com/4.png"),
    ('<meta property="twitter:image" content="https://a.com/5.png"/>', "https://a.com/5.png"),
    ('<meta name="twitter:image" content="https://a.com/6.png"/>', "https://a.com/6.png"),
    ('<link rel="image_src" href="https://a.com/7.png"/>', "https://a.com/7.png"),
])
def test_og_image_accepts_both_property_and_name(html, beklenen):
    assert article_photos.extract_og_image(html) == beklenen


def test_og_image_absent_returns_none():
    assert article_photos.extract_og_image('<meta name="description" content="x"/>') is None
