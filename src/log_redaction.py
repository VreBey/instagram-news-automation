"""Log çıktısındaki sırları maskeleyen logging filtresi.

Neden var: 3 Ağustos 2026'daki denetimde `logs/app.log` içinde CANLI
`TELEGRAM_BOT_TOKEN` (15 satır) ve `MCP_SHARED_SECRET` (4 satır) düz metin
olarak bulundu. İkisi de kasten loglanmamıştı — iki dolaylı yoldan sızıyorlardı:

1. `requests` istisnalarının metni istek URL'sini içerir. Telegram API'de
   token URL'nin İÇİNDEDİR (`/bot<TOKEN>/getUpdates`), NewsAPI/Currents'ta ise
   `?apiKey=...` sorgu parametresi olarak geçer. Dolayısıyla `logger.warning(f"...: {e}")`
   gibi masum görünen HER satır, hata anında sırrı diske yazar.
2. MCP sunucusu açılışta tam endpoint'i logluyordu; `MCP_PATH` sırrı URL
   yolunda taşır (bu bilinçli bir tasarım — Gemini Spark konnektörü yalnızca
   URL kabul eder, özel başlık gönderemez).

Bu yüzden çözüm "şu satırı düzelt" değil, çıkış noktasında merkezî bir
maskeleme: hangi kod yolu loglarsa loglasın sır diske ulaşmaz.

İki katmanlı çalışır:
  - Bilinen değerler: config'teki gerçek sır dizeleri birebir aranıp değiştirilir.
  - Desen tabanlı: sır config'te olmasa bile (ör. üçüncü taraf bir kütüphanenin
    kendi ürettiği URL) token biçimindeki dizeler maskelenir.
"""

import logging
import re

MASK = "<REDACTED>"

# config'te sır TAŞIYAN değişkenler. Bilinçli olarak beyaz liste — otomatik
# "config'teki her string" taraması yapılmıyor, çünkü CONTENT_LANGUAGE="tr"
# gibi kısa/masum değerler logun tamamını mahvederdi ("tr" her yerde geçer).
_SECRET_CONFIG_NAMES = (
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_APP_SECRET",
    "NEWS_API_KEY",
    "CURRENTS_API_KEY",
    "GEMINI_API_KEY",
    "PEXELS_API_KEY",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
    "DASHBOARD_PASSWORD",
    "DASHBOARD_SECRET_KEY",
    "MCP_SHARED_SECRET",
    "TELEGRAM_BOT_TOKEN",
)

# Bu uzunluğun altındaki değerler maskelenmez. Koruma amaçlı: kısa ya da boş
# bir sır (ör. test ortamında DASHBOARD_PASSWORD="123") logdaki alakasız
# metinlerle eşleşip okunamaz hale getirirdi.
_MIN_SECRET_LEN = 12

# Sır config'te bulunamasa bile yakalayan desenler. Gerçek üretim loglarında
# gözlemlenen biçimlere göre yazıldı.
_PATTERNS = (
    # Telegram: https://api.telegram.org/bot<id>:<token>/getUpdates
    re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{30,}"),
    # MCP: https://<tunel>/mcp/<paylasilan-sir>
    re.compile(r"(?<=/mcp/)[A-Za-z0-9_-]{20,}"),
    # Sorgu parametresi olarak taşınan sırlar (NewsAPI, Currents, Meta OAuth).
    # Değeri maskeler, parametre adını bırakır — hata ayıklarken hangi çağrının
    # patladığını görmek gerekiyor.
    re.compile(
        r"(?<=[?&])(api_?key|access_token|client_secret|fb_exchange_token|"
        r"apiKey|token)=[^&\s\"']+",
        re.IGNORECASE,
    ),
)


def _redact(text: str) -> str:
    """Metindeki bilinen sırları ve token biçimli dizeleri maskele."""
    for secret in _load_secrets():
        if secret in text:
            text = text.replace(secret, MASK)

    for pattern in _PATTERNS:
        if pattern.pattern.startswith("(?<=[?&])"):
            # Parametre adını koru, yalnızca değeri maskele.
            text = pattern.sub(lambda m: m.group(0).split("=", 1)[0] + "=" + MASK, text)
        else:
            text = pattern.sub(MASK, text)
    return text


_secrets_cache: tuple[str, ...] | None = None


def _load_secrets() -> tuple[str, ...]:
    """config'ten gerçek sır değerlerini bir kez okuyup önbellekle.

    İçe aktarma fonksiyonun içinde: `config` bu modülü import ettiğinde
    dairesel bağımlılık oluşmasın diye (ve config yüklenemezse loglama
    tamamen çökmesin diye).
    """
    global _secrets_cache
    if _secrets_cache is not None:
        return _secrets_cache

    values = []
    try:
        import config

        for name in _SECRET_CONFIG_NAMES:
            value = getattr(config, name, "") or ""
            value = str(value).strip()
            if len(value) >= _MIN_SECRET_LEN:
                values.append(value)
    except Exception:
        # config okunamazsa desen tabanlı katman yine de çalışır.
        pass

    # Uzun olanı önce değiştir: bir sır diğerinin alt dizesiyse (ör. app secret
    # access token'ın içinde geçiyorsa) kısa olanı önce maskelemek uzun olanı
    # parçalayıp yarısını açıkta bırakırdı.
    _secrets_cache = tuple(sorted(set(values), key=len, reverse=True))
    return _secrets_cache


class RedactingFilter(logging.Filter):
    """Log kaydının mesajını ve argümanlarını diske/konsola gitmeden maskeler.

    Handler'a eklenir (logger'a değil): logger'a eklenen filtreler alt
    logger'ların kayıtlarına uygulanmaz, handler'a eklenenler ise o handler'dan
    geçen HER kaydı görür — üçüncü taraf kütüphanelerin (urllib3, uvicorn,
    werkzeug) kayıtları dahil.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str) and record.msg:
                record.msg = _redact(record.msg)
            elif record.msg is not None and not record.args:
                # `logger.warning(e)` — mesaj bir istisna nesnesi. str
                # olmadığı için eskiden hiç maskelenmiyordu.
                record.msg = _redact(str(record.msg))

            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: _redact(str(v)) if isinstance(v, (str, BaseException)) else v
                        for k, v in record.args.items()
                    }
                else:
                    record.args = tuple(
                        _redact(str(a)) if isinstance(a, (str, BaseException)) else a
                        for a in record.args
                    )

            # İstisna metni de sırrı taşıyabilir (asıl sızıntı yolu buydu):
            # `logger.warning(f"...: {e}")` yerine `exc_info=True` kullanılan
            # yerlerde traceback'in son satırı requests'in URL'li mesajıdır.
            #
            # KRİTİK SIRA: `exc_text`i BURADA üretiyoruz. Eskiden yalnızca
            # "zaten üretilmişse maskele" deniyordu, ama `exc_text` handler'ın
            # FORMATTER'ı tarafından, yani bu filtreden SONRA üretiliyor.
            # Filtre çalıştığında değer hâlâ None'dı; ilk handler traceback'i
            # maskesiz yazıyor, ikinci handler ise birincinin önbelleklediği
            # (artık maskeli) metni alıyordu.
            #
            # main.py'de konsol handler'ı ÖNCE ekleniyor: yani sır journald'a
            # düz metin gidiyor, `logs/app.log` temiz kalıyordu. "app.log'da
            # sır yok" ölçümü bu yüzden yanıltıcıydı — sızıntı kapanmamış,
            # yalnızca yer değiştirmişti. `/loglar` da app.log'u okuduğu için
            # Telegram'dan da görünmüyordu.
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = _redact(record.exc_text)
            if record.stack_info:
                record.stack_info = _redact(record.stack_info)
        except Exception:
            # Maskeleme hiçbir koşulda loglamayı düşürmemeli — sessizce geç.
            pass
        return True


def install(logger: logging.Logger | None = None) -> None:
    """Filtreyi verilen logger'ın (varsayılan: root) tüm handler'larına ekle.

    setup_logging() içinde handler'lar kurulduktan SONRA çağrılmalıdır.
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
