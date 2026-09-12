"""Yetkilendirme kapısının KABLOLAMASI — saf yüklem değil.

`_is_authorized_chat` tek başına test ediliyordu, ama o yüklem yalnızca
`run_bot_polling` içinde çağrılıyor (`_handle_callback` kendisi kontrol
etmiyor). Yani o iki `if` bloğu silinse tüm test paketi yeşil kalırdı ve
botu bulan herkes içerik onaylayabilir, reddedebilir, görsel değiştirebilirdi.

Blast radius açısından test edilmemiş en pahalı yol buydu.

Her test bir POZİTİF KONTROL içeriyor: "hiçbir şey çağrılmadı" iddiası tek
başına, kapının çalıştığını değil döngünün hiç dönmediğini de gösterebilir.
"""

import pytest

import src.telegram_bot as telegram_bot


class _TekPartiSonraDur:
    """İlk çağrıda güncellemeleri verir, ikincide döngüyü kırar."""

    def __init__(self, updates):
        self._updates = updates
        self.cagri = 0

    def __call__(self, *args, **kwargs):
        self.cagri += 1
        if self.cagri > 1:
            raise KeyboardInterrupt
        yanit = type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"result": self_updates},
        })
        self_updates = self._updates
        return yanit()


@pytest.fixture
def bot(monkeypatch, tmp_db):
    """Polling döngüsünü izole et: ağ yok, gerçek üreteç yok."""
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(telegram_bot, "Database", lambda *a, **k: tmp_db)
    for ad in ("ImageGenerator", "StoryGenerator", "VideoGenerator"):
        monkeypatch.setattr(telegram_bot, ad, lambda *a, **k: object())
    monkeypatch.setattr(telegram_bot, "_answer_callback_query", lambda *a, **k: None)

    cagrilanlar = []
    for ad in ("_handle_callback", "_handle_photo_reply", "_handle_video_reply",
               "_handle_command", "_handle_manual_research_text"):
        monkeypatch.setattr(telegram_bot, ad,
                            (lambda isim: lambda *a, **k: cagrilanlar.append(isim))(ad))
    return cagrilanlar


def _callback(chat_id):
    return {"update_id": 1, "callback_query": {
        "id": "cb1", "data": "approve:5",
        "message": {"chat": {"id": chat_id}, "message_id": 9}}}


def _mesaj(chat_id, **alanlar):
    return {"update_id": 2, "message": {"chat": {"id": chat_id}, **alanlar}}


def _calistir(monkeypatch, updates):
    monkeypatch.setattr(telegram_bot.requests, "get", _TekPartiSonraDur(updates))
    telegram_bot.run_bot_polling()


# =============================================
# Yetkisiz sohbet hiçbir şeyi tetikleyemez
# =============================================

def test_unauthorized_callback_is_ignored(bot, monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "111")
    _calistir(monkeypatch, [_callback(999)])
    assert bot == [], "yetkisiz sohbet onay butonunu tetikleyebildi"


def test_unauthorized_message_paths_are_ignored(bot, monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "111")
    _calistir(monkeypatch, [
        _mesaj(999, photo=[{"file_id": "f"}]),
        _mesaj(999, video={"file_id": "v"}),
        _mesaj(999, text="/durum"),
        _mesaj(999, text="serbest metin"),
    ])
    assert bot == [], "yetkisiz sohbet mesaj işleyicilerini tetikleyebildi"


# =============================================
# POZİTİF KONTROL — kapı her şeyi kapatmıyor
#
# Bu olmadan yukarıdaki iki test, "kapı çalıştı" ile "döngü hiç dönmedi"yi
# ayırt edemez.
# =============================================

def test_authorized_callback_is_dispatched(bot, monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "111")
    _calistir(monkeypatch, [_callback(111)])
    assert bot == ["_handle_callback"]


def test_authorized_message_paths_are_dispatched(bot, monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "111")
    _calistir(monkeypatch, [
        _mesaj(111, photo=[{"file_id": "f"}]),
        _mesaj(111, video={"file_id": "v"}),
        _mesaj(111, text="/durum"),
        _mesaj(111, text="serbest metin"),
    ])
    assert bot == ["_handle_photo_reply", "_handle_video_reply",
                   "_handle_command", "_handle_manual_research_text"]


def test_string_and_int_chat_ids_both_authorize(bot, monkeypatch):
    """Telegram chat_id'yi sayı verir, .env metin tutar — ikisi eşleşmeli."""
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "111")
    assert telegram_bot._is_authorized_chat(111) is True
    assert telegram_bot._is_authorized_chat("111") is True
    assert telegram_bot._is_authorized_chat(112) is False
