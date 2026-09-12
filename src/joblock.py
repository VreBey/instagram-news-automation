"""Süreçler arası iş kilidi — aynı işin iki kez birden çalışmasını engeller.

Neden var (1 Ağustos 2026 olayı, 7 Ağustos denetiminde mekanizması bulundu):
dokuz haber 2-27 dakika arayla İKİŞER kez işlendi ve her biri için mükerrer
post+story çifti üretildi. Sebep klasik bir oku-değiştir-yaz yarışı:

    SELECT işlenmemiş haberler        (content_processor)
    ... Gemini çağrıları — HABER BAŞINA DAKİKALAR ...
    UPDATE is_processed = 1

Bu pencere açıkken başlayan ikinci bir çalışma aynı satırları görüyor.
`processed_content` üzerinde tekillik kısıtı da yok, dolayısıyla ikisi de
yazabiliyor. Aralığın 2'den 27 dakikaya BÜYÜMESİ eşzamanlılığın imzasıydı:
iki çalışma aynı Gemini kotasını tüketip birbirini geri çekilmeye zorluyor.

Aynı işi başlatabilen üç bağımsız kapı var ve üçü de açık:
  1. `instagram-scheduler` servisi (zamanlanmış tur + yeniden başlatma telafisi)
  2. Panelin `/api/run/*` uçları — Scheduler'ı DASHBOARD sürecinde kuruyor,
     scheduler servisinden tamamen habersiz (waitress 4 iş parçacığıyla
     çalıştığı için panelin kendi içinde bile çakışabiliyor)
  3. Elle `python main.py --process` / `--pipeline`

Günlük bütçe ve üretim freni bu yarışı KAPATMIYOR: ikisi de çalışmanın
başında okunuyor, eşzamanlı iki çalışma da aynı "henüz 0" değerini görüyor.

Neden dosya kilidi: süreçler arası çalışır, ek bağımlılık istemez ve
—en önemlisi— süreç ölürse kilit işletim sistemi tarafından bırakılır.
PID dosyası ya da veritabanı bayrağı çökme sonrası kalıcı olarak takılı
kalırdı; onu kurtarmak için ayrı bir bayatlık mantığı yazmak gerekirdi.
"""

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

# Testler kilidi geçici bir dizine yönlendirebilsin diye modül seviyesinde.
LOCK_DIR = Path(__file__).resolve().parent.parent / "data" / "locks"


def _try_lock(fd) -> bool:
    """Bloklamadan kilit almayı dene."""
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd) -> None:
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # Süreç kapanırken işletim sistemi zaten bırakır.


@contextmanager
def job_lock(name: str, lock_dir: Path | None = None):
    """`name` işi için kilit almayı dener; alındıysa True, alınamadıysa False verir.

    Kullanım:

        with job_lock("icerik_isleme") as alindi:
            if not alindi:
                return stats          # başka bir süreç zaten çalıştırıyor
            ...

    Kilit ASLA beklemez. Bu bilinçli: bu işler dakikalar sürüyor ve
    beklemek, zamanlayıcı döngüsünü ya da bir HTTP isteğini o kadar süre
    bloke ederdi. İkinci çalışmanın atlanması doğru davranış — iş zaten
    yapılıyor.
    """
    hedef = Path(lock_dir) if lock_dir else LOCK_DIR
    hedef.mkdir(parents=True, exist_ok=True)
    yol = hedef / f"{name}.lock"

    fd = os.open(str(yol), os.O_RDWR | os.O_CREAT, 0o644)
    alindi = _try_lock(fd)
    if not alindi:
        os.close(fd)
        logger.warning(
            f"⏭️ '{name}' zaten çalışıyor (başka bir süreç kilidi tutuyor), "
            f"bu çağrı atlandı."
        )
        yield False
        return

    try:
        yield True
    finally:
        _unlock(fd)
        os.close(fd)
