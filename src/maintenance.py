"""Bakım işleri: veritabanı yedekleme, disk retention, disk doluluk kontrolü.

Neden var (2026-08-03 denetimi):

* **Yedek yoktu.** `data/news.db` tek kopyaydı. İçindeki onay geçmişi, yayın
  kaydı, planlanmış gönderiler ve ayarlar yeniden üretilemez — VDS diski
  bozulursa geri dönüş yoktu.
* **Hiçbir şey silinmiyordu.** `output/` 8 günde 4.3 GB'a ulaşmıştı
  (~540 MB/gün). Tek temizlik mekanizması olan `Database.cleanup_old_data`
  yalnızca `news_items` SATIRLARINI siliyor; diskteki üretilmiş medyaya
  dokunmuyor, `VACUUM` da çalıştırmıyordu. Disk dolduğunda beş systemd
  servisi birden düşer.

Sıralama önemlidir: `run_daily_maintenance` önce YEDEKLER, sonra siler.
Tersi olsaydı bozuk bir temizlik çalıştığında elde yedek kalmazdı.
"""

import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from config import (
    BACKUP_DIR,
    BACKUP_RETENTION_DAYS,
    CACHE_RETENTION_DAYS,
    DB_PATH,
    DISK_USAGE_WARN_PERCENT,
    DRAFT_EXPIRY_DAYS,
    MEDIA_RETENTION_DAYS,
    UNPROCESSED_NEWS_EXPIRY_DAYS,
    POSTS_OUTPUT_DIR,
    REELS_OUTPUT_DIR,
    STORIES_OUTPUT_DIR,
    STOCK_PHOTO_CACHE_DIR,
    ARTICLE_PHOTO_CACHE_DIR,
    MANUAL_IMAGE_CACHE_DIR,
    GAME_COVER_CACHE_DIR,
)

logger = logging.getLogger(__name__)

# Retention taramasında ASLA silinmeyecek dosyalar. `.gitkeep` olmadan
# klasörler git'ten düşer ve ilk çalıştırmada "dizin yok" hatası alınır.
_PROTECTED_NAMES = {".gitkeep", ".gitignore"}


# =============================================
# 1) VERİTABANI YEDEKLEME
# =============================================

def backup_database(db_path: str | Path | None = None,
                    backup_dir: Path | None = None) -> Path | None:
    """Veritabanının tutarlı bir kopyasını al ve yolunu döndür.

    `shutil.copy` yerine SQLite'ın kendi `Connection.backup()` API'si
    kullanılıyor. Sebep: bu proje WAL modunda çalışıyor ve dört süreç aynı
    dosyaya yazıyor. Canlı bir WAL veritabanını dosya olarak kopyalamak,
    `-wal` dosyası kopyalanmadığı için henüz ana dosyaya işlenmemiş
    işlemleri KAÇIRIR — sessizce eksik bir yedek üretir. `backup()` ise
    okuma kilidi altında tutarlı bir anlık görüntü yazar ve eşzamanlı
    yazarları bloklamaz.

    Yedek alındıktan sonra `PRAGMA integrity_check` ile doğrulanır; bozuk
    çıkarsa dosya silinir ve None döner — bozuk bir yedeği "yedek var"
    diye saklamak, yedeğin hiç olmamasından daha tehlikelidir.
    """
    source = Path(db_path or DB_PATH)
    target_dir = Path(backup_dir or BACKUP_DIR)

    if not source.exists():
        logger.warning(f"Yedeklenecek veritabanı bulunamadı: {source}")
        return None

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"news_{stamp}.db"

    src_conn = dst_conn = None
    try:
        # DİKKAT: `with sqlite3.connect(...)` bağlantıyı commit eder ama
        # KAPATMAZ. Açık kalan tanıtıcı hem her yedekte bir handle sızdırır
        # hem de Windows'ta dosyanın silinmesini imkânsız kılar (WinError 32) —
        # yani bozuk bir yedeği temizleme yolu tıkanırdı. Bu yüzden bağlantılar
        # elle, finally içinde kapatılıyor.
        src_conn = sqlite3.connect(str(source), timeout=30)
        dst_conn = sqlite3.connect(str(target))
        src_conn.backup(dst_conn)
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None

        if not _verify_backup(target):
            logger.error(f"Yedek bütünlük kontrolünden geçemedi, siliniyor: {target.name}")
            _safe_unlink(target)
            return None

        size_mb = target.stat().st_size / (1024 * 1024)
        logger.info(f"💾 Veritabanı yedeklendi: {target.name} ({size_mb:.1f} MB)")
        return target

    except Exception as e:
        logger.error(f"Veritabanı yedeklenemedi: {e}")
        _safe_unlink(target)
        return None

    finally:
        for conn in (dst_conn, src_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def _safe_unlink(path: Path) -> None:
    """Dosyayı sil; silinemezse logla ama çağıranı çökertme."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"Geçici yedek dosyası silinemedi {path.name}: {e}")


def _verify_backup(path: Path) -> bool:
    """Yedeğin okunabilir ve bütün olduğunu doğrula."""
    conn = None
    try:
        conn = sqlite3.connect(str(path))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            return False
        # Boş/yarım bir dosya integrity_check'i geçebilir; asıl tabloların
        # varlığını da doğrula.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        return "news_items" in tables
    except Exception as e:
        logger.error(f"Yedek doğrulanamadı: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def backup_env_file(backup_dir: Path | None = None) -> Path | None:
    """`.env`'in bir kopyasını al — veritabanı yedeği tek başına yetmiyor.

    Neden gerekli: `.env` git'te değil (doğru) ve `post-receive` onu
    `git clean`'den koruyor, ama HİÇBİR yerde kopyası yoktu. İçinde
    Instagram uzun ömürlü token'ı, Gemini anahtarı, Cloudinary kimlik
    bilgileri, Telegram token'ı ve panel parolası var. Veritabanı yedeğini
    geri yükleseniz bile bu dosya olmadan sistem çalışmaz — yani gerçek
    kurtarma süresini belirleyen şey veritabanı değil, bu dosyaydı.

    DİKKAT — bu yedek AYNI DİSKTE. Kazara silme/bozma için koruma sağlar,
    sunucunun tamamen kaybı için SAĞLAMAZ. Tek gerçek çözüm dışarıda bir
    kopya (parola yöneticisi yeterli); bu fonksiyon onun yerine geçmez.

    Kopya 0600 izniyle yazılır: kaynağın izni neyse o korunmalı, yedek
    dizini daha gevşek olduğu için açıkça kısıtlanıyor.
    """
    kaynak = Path(__file__).resolve().parent.parent / ".env"
    if not kaynak.exists():
        logger.debug(".env bulunamadı, yedeklenmedi.")
        return None

    hedef_dizin = Path(backup_dir or BACKUP_DIR)
    hedef_dizin.mkdir(parents=True, exist_ok=True)
    hedef = hedef_dizin / f"env_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    try:
        shutil.copy2(kaynak, hedef)
        os.chmod(hedef, 0o600)
    except OSError as e:
        logger.error(f".env yedeklenemedi: {e}")
        return None

    logger.info(f"🔐 .env yedeklendi: {hedef.name}")
    return hedef


def prune_old_env_backups(backup_dir: Path | None = None,
                          keep_days: int | None = None) -> int:
    """Eski `.env` kopyalarını sil (en yenisi her zaman korunur)."""
    target_dir = Path(backup_dir or BACKUP_DIR)
    days = keep_days if keep_days is not None else BACKUP_RETENTION_DAYS
    if not target_dir.exists():
        return 0

    kopyalar = sorted(target_dir.glob("env_*.bak"), key=lambda p: p.stat().st_mtime)
    if len(kopyalar) <= 1:
        return 0

    cutoff = time.time() - days * 86400
    removed = 0
    for path in kopyalar[:-1]:
        if path.stat().st_mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                logger.warning(f"Eski .env yedeği silinemedi {path.name}: {e}")
    return removed


def prune_old_backups(backup_dir: Path | None = None,
                      keep_days: int | None = None) -> int:
    """Saklama süresini aşan yedekleri sil, silinen sayısını döndür.

    En yeni yedek, yaşı ne olursa olsun korunur: sistem bir süre durmuşsa
    tüm yedekler "eski" sayılıp hepsi silinirdi.
    """
    target_dir = Path(backup_dir or BACKUP_DIR)
    days = keep_days if keep_days is not None else BACKUP_RETENTION_DAYS
    if not target_dir.exists():
        return 0

    backups = sorted(target_dir.glob("news_*.db"), key=lambda p: p.stat().st_mtime)
    if len(backups) <= 1:
        return 0

    cutoff = time.time() - days * 86400
    removed = 0
    for path in backups[:-1]:  # en yenisini her zaman koru
        if path.stat().st_mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                logger.warning(f"Eski yedek silinemedi {path.name}: {e}")

    if removed:
        logger.info(f"🧹 {removed} eski veritabanı yedeği silindi ({days} günden eski).")
    return removed


# =============================================
# 2) DİSK RETENTION (üretilmiş medya + önbellekler)
# =============================================

def _prune_directory(directory: Path, keep_days: int) -> tuple[int, int]:
    """Bir dizindeki eski dosyaları sil. (silinen_adet, kazanılan_bayt) döner."""
    if not directory.exists():
        return 0, 0

    cutoff = time.time() - keep_days * 86400
    removed = 0
    freed = 0

    for path in directory.iterdir():
        if not path.is_file() or path.name in _PROTECTED_NAMES:
            continue
        try:
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            size = stat.st_size
            path.unlink()
            removed += 1
            freed += size
        except OSError as e:
            logger.warning(f"Dosya silinemedi {path.name}: {e}")

    return removed, freed


def prune_generated_media(keep_days: int | None = None) -> tuple[int, int]:
    """output/ altındaki eski gönderi, hikaye ve reels dosyalarını sil.

    Yayımlanan medya Instagram'a Cloudinary üzerinden gittiği için yerel
    kopya yalnızca hata ayıklama ve yeniden deneme içindir; kalıcı tutulması
    gerekmiyor.
    """
    days = keep_days if keep_days is not None else MEDIA_RETENTION_DAYS
    total_removed = 0
    total_freed = 0

    for directory in (POSTS_OUTPUT_DIR, STORIES_OUTPUT_DIR, REELS_OUTPUT_DIR):
        removed, freed = _prune_directory(Path(directory), days)
        total_removed += removed
        total_freed += freed

    if total_removed:
        logger.info(
            f"🧹 {total_removed} eski medya dosyası silindi "
            f"({total_freed / (1024 * 1024):.0f} MB kazanıldı, >{days} gün)."
        )
    return total_removed, total_freed


def prune_image_caches(keep_days: int | None = None) -> tuple[int, int]:
    """İndirilen görsel önbelleklerini buda (gerekirse yeniden indirilir)."""
    days = keep_days if keep_days is not None else CACHE_RETENTION_DAYS
    total_removed = 0
    total_freed = 0

    for directory in (STOCK_PHOTO_CACHE_DIR, ARTICLE_PHOTO_CACHE_DIR,
                      MANUAL_IMAGE_CACHE_DIR, GAME_COVER_CACHE_DIR):
        removed, freed = _prune_directory(Path(directory), days)
        total_removed += removed
        total_freed += freed

    if total_removed:
        logger.info(
            f"🧹 {total_removed} önbellek görseli silindi "
            f"({total_freed / (1024 * 1024):.0f} MB kazanıldı, >{days} gün)."
        )
    return total_removed, total_freed


# =============================================
# 3) DİSK DOLULUK KONTROLÜ
# =============================================

def get_disk_usage(path: Path | None = None) -> dict:
    """Verilen yolun bulunduğu diskin doluluk bilgisini döndür."""
    target = Path(path or DB_PATH).parent
    usage = shutil.disk_usage(target)
    percent = (usage.used / usage.total * 100) if usage.total else 0.0
    return {
        "total_gb": usage.total / (1024 ** 3),
        "used_gb": usage.used / (1024 ** 3),
        "free_gb": usage.free / (1024 ** 3),
        "percent_used": percent,
    }


def check_disk_space(path: Path | None = None) -> str | None:
    """Disk doluluğu eşiği aşıyorsa uyarı metni, aksi halde None döndür.

    Sağlık kontrolünün (`src.healthcheck`) çağırdığı biçimde: sorun varsa
    insan tarafından okunabilir tek satır, yoksa None.
    """
    try:
        usage = get_disk_usage(path)
    except OSError as e:
        logger.warning(f"Disk kullanımı okunamadı: {e}")
        return None

    if usage["percent_used"] >= DISK_USAGE_WARN_PERCENT:
        return (
            f"Disk %{usage['percent_used']:.0f} dolu "
            f"({usage['free_gb']:.1f} GB boş kaldı). Doluma ulaşırsa tüm "
            f"servisler durur ve veritabanı bozulabilir."
        )
    return None


def get_latest_backup_age_hours(backup_dir: Path | None = None) -> float | None:
    """En son yedeğin kaç saatlik olduğunu döndür; hiç yedek yoksa None.

    Sağlık kontrolü yedeğin VARLIĞINI değil YAŞINI sorar: bir kez alınıp
    sonra sessizce durmuş bir yedekleme, hiç yedek almamaktan daha
    tehlikelidir — çünkü korunuyor sanılır.
    """
    target_dir = Path(backup_dir or BACKUP_DIR)
    if not target_dir.exists():
        return None

    backups = list(target_dir.glob("news_*.db"))
    if not backups:
        return None

    newest = max(backups, key=lambda p: p.stat().st_mtime)
    return (time.time() - newest.stat().st_mtime) / 3600


def get_directory_size_mb(directory: Path) -> float:
    """Bir dizinin toplam boyutunu MB olarak döndür (dashboard/teşhis için)."""
    directory = Path(directory)
    if not directory.exists():
        return 0.0
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


# =============================================
# 4) GÜNLÜK BAKIM (scheduler tarafından çağrılır)
# =============================================

def run_daily_maintenance(db) -> dict:
    """Günlük bakım zinciri: önce yedekle, sonra temizle.

    Sıra kritik: veri silen her adımdan ÖNCE yedek alınır. Tersi olsaydı
    hatalı bir temizlik çalıştığında geri dönülecek kopya kalmazdı.
    """
    summary: dict = {}

    # 1. Önce yedek — bundan sonraki her adım veri siliyor.
    backup_path = backup_database()
    summary["backup"] = str(backup_path) if backup_path else None
    # `.env` de yedeklenir: veritabanı yedeği tek başına sistemi ayağa
    # kaldırmıyor, anahtarlar bu dosyada. Bkz. backup_env_file.
    env_path = backup_env_file()
    summary["env_backup"] = str(env_path) if env_path else None
    summary["backup_pruned"] = prune_old_backups() + prune_old_env_backups()

    # 2. Bayat haberi kuyruktan düşür. Temizlikten ÖNCE: bu adım satırları
    #    is_processed=1 yapıyor, cleanup_old_data ise is_used=1 olanlara
    #    bakıyor — bağımsızlar, ama kuyruğun gerçeği yansıtması sonraki
    #    adımların ve sağlık kontrolünün doğru çalışması için önce gelmeli.
    try:
        summary["expired_news"] = db.expire_stale_unprocessed_news(
            UNPROCESSED_NEWS_EXPIRY_DAYS
        )
    except Exception as e:
        logger.error(f"Bayat haber temizliği başarısız: {e}")
        summary["expired_news"] = 0

    # 2b. Bayat TASLAĞI da düşür. Haber emekliye ayrılıyordu ama ondan
    #     üretilmiş taslak kuyrukta kalıyordu; 7 Ağustos 2026'da 466 taslak
    #     birikmişti. Üretim frenini besleyen ölçü bu kuyruk olduğu için
    #     (bkz. content_processor.process_all_news) taslak emekliliği
    #     olmadan fren bir kez kapanınca bir daha açılmazdı.
    try:
        summary["expired_drafts"] = db.expire_stale_drafts(DRAFT_EXPIRY_DAYS)
    except Exception as e:
        logger.error(f"Bayat taslak temizliği başarısız: {e}")
        summary["expired_drafts"] = 0

    # 3. Veritabanı satır temizliği (mevcut davranış korunuyor).
    try:
        summary["rows_cleaned"] = db.cleanup_old_data(30)
        summary["db_cleaned"] = True
    except Exception as e:
        logger.error(f"Veritabanı temizliği başarısız: {e}")
        summary["db_cleaned"] = False
        summary["rows_cleaned"] = 0

    # 4. Silinen satırların yerini geri kazan. VACUUM olmadan SQLite dosyası
    #    satırlar silinse bile küçülmez — 30 gündür silinen kayıtların yeri
    #    dosyada boşluk olarak duruyordu.
    try:
        db.vacuum()
        summary["vacuumed"] = True
    except Exception as e:
        logger.warning(f"VACUUM başarısız: {e}")
        summary["vacuumed"] = False

    # 5. Disk retention — asıl şişmenin kaynağı burası.
    media_removed, media_freed = prune_generated_media()
    cache_removed, cache_freed = prune_image_caches()
    summary["media_removed"] = media_removed
    summary["cache_removed"] = cache_removed
    summary["freed_mb"] = (media_freed + cache_freed) / (1024 * 1024)

    # 6. Temizlikten SONRA disk durumu — hâlâ doluysa retention yetmiyor
    #    demektir ve bu bilinmesi gereken bir şey.
    warning = check_disk_space()
    summary["disk_warning"] = warning
    if warning:
        logger.warning(f"⚠️ {warning}")

    return summary
