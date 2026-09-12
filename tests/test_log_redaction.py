"""src.log_redaction birim testleri.

Bu testler 2026-08-03 denetiminde gerçek `logs/app.log` içinde bulunan
sızıntı biçimlerini birebir yeniden üretir — regresyon koruması olarak.
"""

import logging

import pytest

from src import log_redaction
from src.log_redaction import MASK, RedactingFilter, install


@pytest.fixture(autouse=True)
def _clear_secret_cache():
    """Her test kendi sır listesiyle başlasın (modül düzeyinde önbellekleniyor)."""
    log_redaction._secrets_cache = None
    yield
    log_redaction._secrets_cache = None


def _record(msg, *args):
    return logging.LogRecord("t", logging.WARNING, __file__, 1, msg, args, None)


def _filtered(msg, *args):
    rec = _record(msg, *args)
    RedactingFilter().filter(rec)
    return rec.getMessage()


# --- Desen tabanlı katman (sır config'te olmasa bile çalışır) ---

def test_redacts_telegram_token_from_exception_url():
    """Gerçek sızıntı yolu: requests istisnası tam URL'yi metne gömüyor."""
    # NOT: buradaki token/sır değerleri UYDURMA. Gerçek bir sırrı teste
    # yazmak onu git geçmişine kalıcı olarak gömer — sır sonradan
    # döndürülse bile depoyu klonlayan herkes eski değeri görür.
    msg = (
        "Telegram polling hatası: 409 Client Error: Conflict for url: "
        "https://api.telegram.org/bot1234567890:AAsahte0TESTtokenDEGERi1234567890abc"
        "/getUpdates?offset=1"
    )
    out = _filtered(msg)
    assert "AAsahte0TESTtokenDEGERi1234567890abc" not in out
    assert MASK in out
    # Hata ayıklanabilirlik korunmalı: hangi hata olduğu hâlâ görünmeli.
    assert "409" in out and "Conflict" in out


def test_redacts_mcp_path_secret():
    out = _filtered("MCP başlıyor: http://127.0.0.1:8765/mcp/sahteTESTsirri1234567890abcdef")
    assert "sahteTESTsirri1234567890abcdef" not in out
    assert "/mcp/" + MASK in out


@pytest.mark.parametrize("param", ["apiKey", "api_key", "access_token", "client_secret"])
def test_redacts_query_string_secrets(param):
    out = _filtered(f"istek hatası: https://api.example.com/v1?q=ai&{param}=s3cret_value_123&lang=en")
    assert "s3cret_value_123" not in out
    # Parametre ADI korunur — hangi çağrının patladığını görmek gerekiyor.
    assert f"{param}={MASK}" in out
    assert "lang=en" in out


def test_leaves_ordinary_messages_untouched():
    msg = "2 gönderi ve 5 hikaye yayınlandı, kaynak: PC Gamer"
    assert _filtered(msg) == msg


# --- Bilinen değer katmanı (config'teki gerçek sırlar) ---

def test_redacts_configured_secret_value(monkeypatch):
    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "sup3r-gizli-parola", raising=False)
    out = _filtered("Giriş denemesi başarısız: sup3r-gizli-parola")
    assert "sup3r-gizli-parola" not in out
    assert MASK in out


def test_short_secrets_are_not_redacted(monkeypatch):
    """
    Kısa bir sır (ör. test ortamında DASHBOARD_PASSWORD='123') logdaki alakasız
    metinlerle eşleşip her şeyi maskelerdi. _MIN_SECRET_LEN bunu engelliyor.
    """
    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "123", raising=False)
    out = _filtered("123 haber toplandı, 123 tanesi işlendi")
    assert out == "123 haber toplandı, 123 tanesi işlendi"


def test_longer_secret_masked_before_its_substring(monkeypatch):
    """
    Bir sır diğerinin alt dizesiyse, kısa olan önce maskelenirse uzun olanın
    kalanı açıkta kalırdı. Uzunluğa göre azalan sıralama bunu önlüyor.
    """
    monkeypatch.setattr("config.INSTAGRAM_APP_SECRET", "abcdefghijklmnop", raising=False)
    monkeypatch.setattr(
        "config.INSTAGRAM_ACCESS_TOKEN", "abcdefghijklmnopQRSTUVWXYZ", raising=False
    )
    out = _filtered("token: abcdefghijklmnopQRSTUVWXYZ")
    assert "QRSTUVWXYZ" not in out


# --- Kayıt yapısı: args, exc_text, kurulum ---

def test_redacts_lazy_format_args():
    out = _filtered("polling hatası: %s", "https://api.telegram.org/bot1234567:" + "A" * 35)
    assert "A" * 35 not in out


def test_redacts_exception_text():
    rec = _record("çöktü")
    rec.exc_text = "requests.HTTPError: url: https://x/mcp/" + "B" * 25
    RedactingFilter().filter(rec)
    assert "B" * 25 not in rec.exc_text


def test_filter_never_drops_records():
    """Maskeleme bir kaydı asla yutmamalı — filter() daima True dönmeli."""
    assert RedactingFilter().filter(_record("herhangi bir mesaj")) is True


def test_install_is_idempotent():
    logger = logging.getLogger("test_install_idempotent")
    logger.handlers = [logging.NullHandler()]
    install(logger)
    install(logger)
    count = sum(isinstance(f, RedactingFilter) for f in logger.handlers[0].filters)
    assert count == 1


# =============================================
# Traceback: BİRDEN FAZLA handler olduğunda
#
# `exc_text` handler'ın FORMATTER'ı tarafından üretiliyor, yani filtreden
# SONRA. Filtre yalnızca "zaten üretilmişse maskele" dediği sürece İLK
# handler traceback'i maskesiz yazıyor, ikinci handler ise birincinin
# önbelleklediği (artık maskeli) metni alıyordu.
#
# main.py'de konsol handler'ı ÖNCE ekleniyor → sır journald'a düz metin
# gidiyor, logs/app.log temiz kalıyordu. "app.log'da sır yok" ölçümü bu
# yüzden yanıltıcıydı; sızıntı kapanmamış, yalnızca görünmez olmuştu.
# =============================================

def test_traceback_masked_in_every_handler(monkeypatch):
    import io

    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "SUPERSECRETVALUE123", raising=False)

    logger = logging.getLogger("test_coklu_handler")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    akislar = [io.StringIO(), io.StringIO()]
    for akis in akislar:
        h = logging.StreamHandler(akis)
        h.setFormatter(logging.Formatter("%(message)s"))
        h.addFilter(RedactingFilter())
        logger.addHandler(h)

    try:
        raise ValueError("istek basarisiz: SUPERSECRETVALUE123")
    except ValueError:
        logger.exception("patladi")

    for sira, akis in enumerate(akislar, start=1):
        metin = akis.getvalue()
        assert "SUPERSECRETVALUE123" not in metin, f"{sira}. handler sızdırdı"
        assert "<REDACTED>" in metin, f"{sira}. handler maskelemedi"


def test_exception_object_as_message_is_masked(monkeypatch):
    """`logger.warning(e)` — mesaj str değil, istisna nesnesi."""
    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "SUPERSECRETVALUE123", raising=False)

    kayit = logging.LogRecord(
        name="t", level=logging.WARNING, pathname=__file__, lineno=1,
        msg=ValueError("token=SUPERSECRETVALUE123"), args=(), exc_info=None,
    )
    RedactingFilter().filter(kayit)
    assert "SUPERSECRETVALUE123" not in kayit.getMessage()
