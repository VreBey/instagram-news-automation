"""Yayın anındaki geçici hatalar kalıcı kayba dönüşmemeli.

Canlı olay (7 Ağustos 2026): kullanıcı Telegram'dan 4 içerik onayladı.
İlk ikisi yayınlandı, son ikisi Meta'nın "medyayı URI'den çekemedim"
hatasıyla düştü. Reddedilen üç Cloudinary adresini sonradan test ettim:
üçü de HTTP 200 ve geçerli PNG. Yani dosyalar sağlamdı — tek fark
zamanlamaydı: başarısızlarda yükleme ile container isteği arasında
1 saniye vardı, başarılılarda 14-17.

Meta bu hatayı `is_transient: false` diye işaretliyor ama davranışı
geçici: dosya Meta'nın fetcher'ının bastığı CDN kenarına henüz
yayılmamış oluyor. Yeniden deneme olmadığı için içerik kalıcı olarak
'approved' durumunda takılıyordu. Aynı hata 1 Ağustos'ta da olmuş ve
fark edilmesi altı gün almıştı.
"""

import pytest
import requests

from src.instagram_client import InstagramClient


def _yanit(mocker, status, govde):
    r = mocker.Mock()
    r.json.return_value = govde
    r.text = str(govde)
    r.status_code = status
    if status >= 400:
        hata = requests.HTTPError(f"{status} Client Error")
        hata.response = r
        r.raise_for_status.side_effect = hata
    else:
        r.raise_for_status.return_value = None
    return r


_CDN_HATASI = {"error": {
    "message": "Only photo or video can be accepted as media type.",
    "code": 9004, "error_subcode": 2207052, "is_transient": False,
}}


@pytest.fixture
def istemci(monkeypatch):
    c = InstagramClient.__new__(InstagramClient)
    c.graph_url = "https://graph.facebook.com/v21.0"
    c.user_id = "17841400000000000"
    c.access_token = "tok"
    monkeypatch.setattr("src.instagram_client.time.sleep", lambda s: None)
    return c


# =============================================
# Geçici CDN hatası
# =============================================

def test_media_fetch_error_is_retried(istemci, mocker):
    yanitlar = [
        _yanit(mocker, 400, _CDN_HATASI),
        _yanit(mocker, 400, _CDN_HATASI),
        _yanit(mocker, 200, {"id": "17999"}),
    ]
    istemci.session = mocker.Mock()
    istemci.session.post.side_effect = yanitlar

    sonuc = istemci._create_media_container(
        media_type="IMAGE", media_url="https://cdn/x.png", caption="c")

    assert sonuc == "17999"
    assert istemci.session.post.call_count == 3


def test_retry_gives_up_and_reports(istemci, mocker):
    istemci.session = mocker.Mock()
    istemci.session.post.side_effect = [_yanit(mocker, 400, _CDN_HATASI)] * 3

    assert istemci._create_media_container(
        media_type="IMAGE", media_url="https://cdn/x.png") is None
    assert istemci.session.post.call_count == 3


def test_permanent_errors_are_not_retried(istemci, mocker):
    """
    Yalnızca medya-çekme hatası yeniden denenir. Geçersiz token ya da izin
    hatasını 3 kez denemek hem anlamsız hem de asıl sebebi gizler.
    """
    kalici = {"error": {"message": "Invalid OAuth access token",
                        "code": 190, "error_subcode": 460}}
    istemci.session = mocker.Mock()
    istemci.session.post.side_effect = [_yanit(mocker, 400, kalici)]

    assert istemci._create_media_container(
        media_type="IMAGE", media_url="https://cdn/x.png") is None
    assert istemci.session.post.call_count == 1


def test_story_uses_the_same_retry_path(istemci, mocker):
    """Story eskiden ham session.post kullanıyordu ve korumasızdı."""
    istemci.session = mocker.Mock()
    istemci.session.post.side_effect = [
        _yanit(mocker, 400, _CDN_HATASI),
        _yanit(mocker, 200, {"id": "18888"}),
    ]
    mocker.patch.object(istemci, "_wait_for_container", return_value=True)
    mocker.patch.object(istemci, "_publish_media",
                        return_value={"success": True, "media_id": "18888"})

    sonuc = istemci.publish_story(media_url="https://cdn/s.png")

    assert sonuc["success"] is True
    assert istemci.session.post.call_count == 2


# =============================================
# Başarısızlık sessiz kalmamalı
# =============================================

def test_publish_failure_notifies_user(tmp_db, mocker):
    from src.scheduler import Scheduler

    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    gonderilen = []
    mocker.patch("src.telegram_bot._send_text",
                 side_effect=lambda chat, metin, **k: gonderilen.append(metin))
    mocker.patch("config.TELEGRAM_CHAT_ID", "123", create=True)

    s._notify_publish_failure(
        {"summary_text": "Elden Ring Switch 2'ye geliyor"},
        "post", "Container oluşturulamadı")

    assert gonderilen, "başarısızlık bildirilmedi"
    assert "Paylaşım başarısız" in gonderilen[0]
    assert "Elden Ring" in gonderilen[0]


def test_notification_failure_does_not_break_publishing(tmp_db, mocker):
    """Bildirim gönderilemezse yayın döngüsü çökmemeli."""
    from src.scheduler import Scheduler

    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    mocker.patch("src.telegram_bot._send_text", side_effect=RuntimeError("kopuk"))
    mocker.patch("config.TELEGRAM_CHAT_ID", "123", create=True)

    s._notify_publish_failure({"summary_text": "x"}, "post", "hata")  # patlamamalı
