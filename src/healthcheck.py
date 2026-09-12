"""
Sistem Sağlık Kontrolü

Sessiz arızaları yakalar. Gerçek olay (1-2 Ağustos 2026): Telegram
bağlantısı 2+ saat koptu, 30 hata logu birikti ve bu ancak ertesi gün
loglara elle bakılınca fark edildi — o süre boyunca onay butonları
çalışmıyordu.

Kontroller kasıtlı olarak "gerçekten önemli olan" şeylere odaklanır:
sistemin çalışıyor GÖRÜNMESİ değil, işini YAPIYOR olması.

Sorun bulunursa Telegram'dan tek bir özet mesajı gönderilir. Aynı sorun
için tekrar tekrar mesaj atmamak adına, son gönderilen uyarının imzası
veritabanında tutulur ve değişmediyse susulur.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import HEALTHCHECK_STALE_HOURS, HEALTHCHECK_NO_PUBLISH_DAYS
from src.database import Database

logger = logging.getLogger(__name__)

_ALERT_STATE_KEY = "healthcheck_last_alert"


def run_health_checks(db: Database) -> list[str]:
    """Sorunların insan-okunur listesini döner. Boş liste = her şey yolunda."""
    problems: list[str] = []
    now = datetime.now()

    # 1. Haber akışı duruyor mu? Toplama günde bir çalışıyor; belirtilen
    #    süredir hiç yeni haber yoksa besleme/ağ tarafında bir sorun var.
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(collected_at) AS son FROM news_items"
        ).fetchone()
    son = row["son"] if row else None
    if not son:
        problems.append("Veritabanında hiç haber yok.")
    else:
        # collected_at SQLite datetime('now') ile yazılıyor → UTC
        yas = (datetime.now(timezone.utc).replace(tzinfo=None)
               - datetime.fromisoformat(son)).total_seconds() / 3600
        if yas > HEALTHCHECK_STALE_HOURS:
            problems.append(f"{yas:.0f} saattir yeni haber toplanmadı.")

    # 1b. TEK TEK kaynaklar sağ mı? Yukarıdaki kontrol `MAX(collected_at)`
    #     bakıyor — yani TÜM kaynaklar arasındaki en yeniye. Tek bir feed
    #     çalıştığı sürece yeşil kalır; kaynakların yarısı aylarca ölü
    #     olabilir ve hiçbir şey söylemez. Ölü kaynak sessizce kapsamı
    #     daraltır: seçim havuzu küçüldükçe puanlama daha kötü adaylar
    #     arasından seçmek zorunda kalır.
    hatali = db.get_setting("last_failed_feeds")
    if hatali:
        adlar = [a for a in hatali.split("|") if a]
        if adlar:
            problems.append(
                f"{len(adlar)} RSS kaynağı son toplamada hata verdi: "
                f"{', '.join(adlar[:5])}"
                + (f" (+{len(adlar) - 5})" if len(adlar) > 5 else "")
            )

    # 2. Onay kuyruğu tıkandı mı? Medyası hazır ama Telegram'a hiç
    #    bildirilmemiş içerik birikiyorsa bildirim işi çalışmıyor demektir.
    with db._get_connection() as conn:
        bekleyen = conn.execute(
            """SELECT COUNT(*) AS n FROM processed_content
               WHERE status = 'draft' AND media_path IS NOT NULL
                 AND telegram_message_id IS NULL"""
        ).fetchone()["n"]
    if bekleyen > 40:
        problems.append(f"{bekleyen} içerik Telegram'a bildirilmeyi bekliyor (kuyruk tıkanmış olabilir).")

    # 3. Yayınlama başarısız mı? Son 24 saatte başarısız kayıt varsa
    #    Instagram tarafında (token/kota/medya) bir sorun var demektir.
    esik = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    with db._get_connection() as conn:
        basarisiz = conn.execute(
            "SELECT COUNT(*) AS n FROM publish_history WHERE status = 'failed' AND published_at >= ?",
            (esik,),
        ).fetchone()["n"]
    if basarisiz:
        problems.append(f"Son 24 saatte {basarisiz} paylaşım başarısız oldu.")

    # 3b. HİÇ YAYIN ÇIKMIYOR MU? — sistemin tek gerçek çıktısı.
    #
    # Buradaki tüm diğer kontroller boru hattının PARÇALARINA bakıyor: haber
    # geliyor mu, kuyruk tıkalı mı, token geçerli mi, disk dolu mu. Hiçbiri
    # "peki bu hesapta bir şey yayınlanıyor mu?" diye sormuyordu. Yayın
    # tamamen dursa — onay verilmese, `publish_scheduled` sessizce boş dönse,
    # zamanlama hiç oluşmasa — bütün kontroller yeşil kalıyordu.
    #
    # Bu, dosyanın kendi felsefesinin ("çalışıyor GÖRÜNMESİ değil, işini
    # YAPIYOR olması") kör noktasıydı: her parça sağlıklı, ürün yok.
    esik_yayin = (now - timedelta(days=HEALTHCHECK_NO_PUBLISH_DAYS)
                  ).strftime("%Y-%m-%d %H:%M:%S")
    with db._get_connection() as conn:
        basarili = conn.execute(
            "SELECT COUNT(*) AS n FROM publish_history "
            "WHERE status = 'success' AND published_at >= ?",
            (esik_yayin,),
        ).fetchone()["n"]
        # Yayınlanacak içerik VAR MI? Yoksa sorun yayın yolunda değil,
        # üretimde ya da onayda — farklı bir arıza, farklı mesaj.
        hazir = conn.execute(
            "SELECT COUNT(*) AS n FROM processed_content "
            "WHERE status = 'draft' AND media_path IS NOT NULL"
        ).fetchone()["n"]
    if not basarili:
        if hazir:
            problems.append(
                f"{HEALTHCHECK_NO_PUBLISH_DAYS} gündür hiç paylaşım yapılmadı "
                f"({hazir} içerik hazır ve onay bekliyor)."
            )
        else:
            problems.append(
                f"{HEALTHCHECK_NO_PUBLISH_DAYS} gündür hiç paylaşım yapılmadı "
                f"ve yayına hazır içerik de yok."
            )

    # 4. Instagram token'ı yakında doluyor mu?
    from src.token_manager import TokenManager
    durum = TokenManager(db=db).get_expiry_status()
    if durum.get("known") and durum.get("warning"):
        kalan = durum.get("days_remaining")
        problems.append(f"Instagram token'ının süresi {kalan} gün içinde doluyor.")

    # 5. Disk doluyor mu? Bu, sessiz arızaların en sertidir: disk dolduğunda
    #    beş systemd servisi birden durur ve SQLite yazma sırasında bozulabilir.
    #    2026-08-03 denetiminde output/ 8 günde 4.3 GB'a ulaşmıştı ve hiçbir
    #    şey silinmiyordu. Retention artık var (src/maintenance.py) ama yine de
    #    izlenmeli — retention yetmezse bunu ÖNCEDEN bilmek gerekiyor.
    from src.maintenance import check_disk_space
    disk_uyarisi = check_disk_space()
    if disk_uyarisi:
        problems.append(disk_uyarisi)

    # 6. Yedekleme çalışıyor mu? Yedeğin var sanılıp olmaması, hiç olmamasından
    #    daha tehlikeli — bu yüzden yedeğin YAŞI kontrol ediliyor, varlığı değil.
    from src.maintenance import get_latest_backup_age_hours
    yedek_yasi = get_latest_backup_age_hours()
    if yedek_yasi is None:
        problems.append("Veritabanının hiç yedeği yok.")
    elif yedek_yasi > 48:
        problems.append(f"En son veritabanı yedeği {yedek_yasi:.0f} saatlik (günlük olmalı).")

    # 7. Performans verisi toplanıyor mu? Bu, sistemin çalışıyor GÖRÜNÜP işini
    #    yapmadığı en sinsi durumdu: 58 yayının 58'inde insights isteği
    #    başarısızdı (token'da `instagram_manage_insights` izni yok) ama
    #    hiçbir uyarı üretilmiyordu. Sonuç: hangi içeriğin gerçekten erişim
    #    aldığı bilinmiyordu, yani içerik kalitesini kalibre edecek tek
    #    nesnel sinyal aylarca kayıptı.
    if db.get_setting("insights_permission_status") == "missing":
        problems.append(
            "Instagram performans verisi toplanamıyor: token'da "
            "'instagram_manage_insights' izni yok."
        )

    return problems


def check_and_alert(db: Database = None) -> dict:
    """Kontrolleri çalıştırır, yeni bir sorun varsa Telegram'dan uyarır."""
    db = db or Database()
    problems = run_health_checks(db)

    # Aynı sorun tablosu için tekrar tekrar mesaj atma — yalnızca durum
    # DEĞİŞTİĞİNDE haber ver (sorun çıktı ya da düzeldi).
    imza = " | ".join(problems) if problems else "ok"
    onceki = db.get_setting(_ALERT_STATE_KEY)

    if imza == onceki:
        logger.info("🩺 Sağlık durumu değişmedi, uyarı gönderilmedi.")
        return {"problems": problems, "notified": False}

    import src.telegram_bot as telegram_bot
    from config import TELEGRAM_CHAT_ID

    if problems:
        mesaj = "🩺 Sistem uyarısı\n\n" + "\n".join(f"• {p}" for p in problems)
    else:
        mesaj = "✅ Sistem normale döndü — tüm kontroller başarılı."

    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID yok, sağlık uyarısı gönderilemedi.")
        return {"problems": problems, "notified": False}

    gonderildi = bool(telegram_bot._send_text(TELEGRAM_CHAT_ID, mesaj))
    if gonderildi:
        db.set_setting(_ALERT_STATE_KEY, imza)
    logger.info(f"🩺 Sağlık kontrolü: {len(problems)} sorun, bildirim={gonderildi}")
    return {"problems": problems, "notified": gonderildi}
