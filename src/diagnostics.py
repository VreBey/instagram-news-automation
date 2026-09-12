"""Salt-okunur teşhis raporları — Telegram'dan sunucuya girmeden durum görmek için.

Neden var: sistemin sessiz arızaları vardı ve hepsi ancak loglara elle
bakılınca fark edildi — Telegram 2 saat koptu (1-2 Ağustos), insights aylarca
0/58 topladı (4 Ağustos), 290 haber işlenmeden birikti. Sağlık kontrolü bu
sınıfı 6 saatte bir kendiliğinden bildiriyor; burada eksik olan **sorulduğunda
cevap verme** yeteneğiydi: "şu an durum ne?"

Tasarım kararı: bu modüldeki hiçbir fonksiyon durum DEĞİŞTİRMEZ. Yalnızca
okur ve metin döndürür. Bu bilinçli — Telegram kanalını ele geçiren biri
sunucuda komut çalıştıramamalı. Aynı ihtiyacı karşılayan genel amaçlı bir AI
ajanı kurmak (ör. OpenClaw) bu garantiyi ortadan kaldırırdı.
"""

import logging
import re
import subprocess

from config import (
    DAILY_POST_LIMIT, DAILY_STORY_LIMIT, DAILY_REELS_LIMIT,
    DRAFT_EXPIRY_DAYS, LOG_FILE, UNPROCESSED_NEWS_EXPIRY_DAYS,
)
from src.content_processor import backlog_ceiling
# Zaman kuralı tek yerde tanımlı (bkz. src/database.py başı): yerel yazılan
# sütunlarla karşılaştırırken zamanı Python'dan geçir, SQLite'ın UTC
# `now`'ını kullanma.
from src.database import _local_now, _local_today

logger = logging.getLogger(__name__)

SERVICES = (
    "instagram-scheduler",
    "instagram-telegram",
    "instagram-dashboard",
    "instagram-mcp",
    "instagram-cloudflare-tunnel",
)


def _service_states() -> list[tuple[str, str]]:
    """Servis durumlarını (ad, durum) olarak döndür.

    `systemctl is-active` salt-okunur bir işlem ve root gerektirmiyor, bu
    yüzden bot `appuser` olarak çalışırken de sorgulanabiliyor.
    """
    states = []
    for name in SERVICES:
        try:
            out = subprocess.run(
                ["systemctl", "is-active", name],
                capture_output=True, text=True, timeout=10,
            )
            states.append((name, (out.stdout or out.stderr).strip() or "bilinmiyor"))
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning(f"Servis durumu okunamadı [{name}]: {e}")
            states.append((name, "okunamadı"))
    return states


def system_status(db) -> str:
    """Servisler, disk, yedek ve kuyruk özeti."""
    from src.maintenance import check_disk_space, get_disk_usage, get_latest_backup_age_hours

    satirlar = ["🩺 Sistem Durumu", ""]

    sorunlu = 0
    for name, state in _service_states():
        isaret = "✅" if state == "active" else "❌"
        if state != "active":
            sorunlu += 1
        satirlar.append(f"{isaret} {name.replace('instagram-', '')}: {state}")

    satirlar.append("")
    try:
        u = get_disk_usage()
        uyari = "⚠️ " if check_disk_space() else ""
        satirlar.append(f"{uyari}💾 Disk: %{u['percent_used']:.0f} dolu, "
                        f"{u['free_gb']:.1f} GB boş")
    except OSError as e:
        satirlar.append(f"💾 Disk okunamadı: {e}")

    yas = get_latest_backup_age_hours()
    if yas is None:
        satirlar.append("❌ Yedek: hiç yok")
    else:
        satirlar.append(f"{'⚠️ ' if yas > 48 else '✅ '}🗄️ Son yedek: {yas:.0f} saat önce")

    satirlar.append("")
    with db._get_connection() as conn:
        def say(sql):
            return conn.execute(sql).fetchone()[0]

        islenmemis = say("SELECT COUNT(*) FROM news_items WHERE is_processed = 0")
        taslak = say("SELECT COUNT(*) FROM processed_content WHERE status = 'draft'")
        zamanlanmis = say("SELECT COUNT(*) FROM scheduled_posts WHERE status = 'pending'")

        satirlar.append(f"📥 İşlenmemiş haber: {islenmemis}")
        satirlar.append(f"📝 Onay bekleyen: {taslak}")
        satirlar.append(f"⏰ Zamanlanmış: {zamanlanmis}")

    # Üretim freni açık mı? Kuyruk tavanı aşılmışsa yeni içerik üretilmiyor
    # ve bu SESSİZ bir durum — nedeni burada görünmezse "sistem durdu"
    # gibi okunur.
    # Tavan formülü BURADA TEKRAR YAZILMAZ: kural content_processor'da tek
    # yerde duruyor. Kopyalanmış hâli sessizce ayrışabilirdi — biri
    # değişirse `/durum` "fren açık" derken üretim çalışmaya devam ederdi.
    tavan = backlog_ceiling()
    if taslak >= tavan:
        satirlar.append(
            f"⏸️ Üretim duraklatıldı (kuyruk {taslak} ≥ tavan {tavan}). "
            f"Onayladıkça ya da {DRAFT_EXPIRY_DAYS} günde bayatladıkça açılır."
        )

    if sorunlu:
        satirlar.append("")
        satirlar.append(f"⚠️ {sorunlu} servis çalışmıyor.")

    return "\n".join(satirlar)


def funnel_report(db, hours: int = 24) -> str:
    """Haber → içerik → medya → yayın hunisinin son N saati."""
    satirlar = [f"📊 Son {hours} Saat", ""]
    pencere = f"-{hours} hours"

    with db._get_connection() as conn:
        def say(sql, *p):
            return conn.execute(sql, p).fetchone()[0]

        toplanan = say("SELECT COUNT(*) FROM news_items WHERE collected_at >= datetime('now', ?)", pencere)
        islenen = say("SELECT COUNT(*) FROM news_items WHERE collected_at >= datetime('now', ?) "
                      "AND is_processed=1 AND expired_at IS NULL", pencere)
        emekli = say("SELECT COUNT(*) FROM news_items WHERE expired_at >= datetime('now', ?)", pencere)
        icerik = say("SELECT COUNT(*) FROM processed_content WHERE created_at >= datetime('now', ?)", pencere)
        medya = say("SELECT COUNT(*) FROM processed_content WHERE created_at >= datetime('now', ?) "
                    "AND media_path IS NOT NULL", pencere)
        bildirim = say("SELECT COUNT(*) FROM processed_content WHERE created_at >= datetime('now', ?) "
                       "AND telegram_message_id IS NOT NULL", pencere)

        satirlar.append(f"📰 Toplanan haber: {toplanan}")
        satirlar.append(f"⚙️ İşlenen: {islenen}")
        if emekli:
            satirlar.append(f"⌛ Emekliye ayrılan: {emekli} ({UNPROCESSED_NEWS_EXPIRY_DAYS} günden eski)")
        satirlar.append(f"✍️ Üretilen içerik: {icerik}")
        satirlar.append(f"🎨 Medyası hazır: {medya}")
        satirlar.append(f"📲 Telegram'a bildirilen: {bildirim}")

        satirlar.append("")
        satirlar.append("📤 Yayınlanan:")
        # `published_at` YEREL saatle yazılıyor, `DATE('now')` ise UTC verir
        # (bkz. src/database.py başındaki zaman kuralı). Eskiden ikisi
        # karşılaştırılıyordu ve servisler UTC+3'te çalıştığı için rapor,
        # yerel 00:00–03:00 arasında YANLIŞ GÜNÜ sayıyordu. Sayı gerçek
        # veriden geliyordu ama etiketiyle uyuşmuyordu — bu projede
        # kabul edilemez bir sınıf.
        rows = conn.execute(
            "SELECT post_type, COUNT(*) n FROM publish_history "
            "WHERE status='success' AND DATE(published_at)=? GROUP BY post_type",
            (_local_today(),)
        ).fetchall()
        limitler = {"post": DAILY_POST_LIMIT, "story": DAILY_STORY_LIMIT, "reels": DAILY_REELS_LIMIT}
        if not rows:
            satirlar.append("   (bugün henüz yok)")
        for r in rows:
            lim = limitler.get(r["post_type"], "?")
            asim = " ⚠️ limit aşıldı" if isinstance(lim, int) and r["n"] > lim else ""
            satirlar.append(f"   {r['post_type']}: {r['n']}/{lim}{asim}")

        basarisiz = say("SELECT COUNT(*) FROM publish_history WHERE status='failed' "
                        "AND published_at >= ?", _local_now(offset_hours=-hours))
        if basarisiz:
            satirlar.append(f"❌ Başarısız paylaşım: {basarisiz}")

    # Arka plan kaynakları: gerçek görsel mi, yedek mi?
    kaynaklar = db.get_background_source_stats(days=7)
    if kaynaklar:
        ETIKET = {
            "manual": "elle gönderilen",
            "article_image": "haberin görseli",
            "og_image": "sayfadan (og:image)",
            "game_cover": "oyun kapağı",
            "stock_photo": "stok fotoğraf",
            "gradient": "gradyan (görsel yok)",
            "kayitsiz": "kayıtsız (eski)",
        }
        YEDEK = {"stock_photo", "gradient"}
        gercek = sum(n for k, n in kaynaklar.items()
                     if k not in YEDEK and k != "kayitsiz")
        olculen = sum(n for k, n in kaynaklar.items() if k != "kayitsiz")

        satirlar.append("")
        satirlar.append("🖼️ Görsel kaynağı (7 gün):")
        for kaynak, n in kaynaklar.items():
            satirlar.append(f"   {ETIKET.get(kaynak, kaynak)}: {n}")
        if olculen:
            satirlar.append(f"   → gerçek görsel oranı: %{100 * gercek / olculen:.0f}")

    return "\n".join(satirlar)


_LOG_LEVEL_RE = re.compile(r"\|\s*(ERROR|CRITICAL|WARNING)\s*\|")


def recent_errors(limit: int = 12) -> str:
    """Uygulama logundaki son hata satırları.

    journald yerine uygulamanın kendi log dosyası okunuyor: bot `appuser`
    olarak çalışıyor ve journal erişimi olmayabilir, ama bu dosya zaten
    onun. Dosya maskeleme filtresinden geçerek yazıldığı için sır içermez
    (bkz. src/log_redaction.py).
    """
    try:
        with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
            satirlar = f.readlines()
    except OSError as e:
        return f"Log okunamadı: {e}"

    hatalar = [s.strip() for s in satirlar if _LOG_LEVEL_RE.search(s)]
    if not hatalar:
        return "✅ Logda hata/uyarı yok."

    secilen = hatalar[-limit:]
    out = [f"📋 Son {len(secilen)} hata/uyarı", ""]
    for s in secilen:
        # Telegram mesaj sınırına takılmamak için satır başına kırp.
        out.append(s[:180])
    return "\n".join(out)


def quota_report(db) -> str:
    """Gemini kota durumu ve Instagram token ömrü."""
    from src.token_manager import TokenManager

    satirlar = ["🔑 Kota ve Token", ""]

    durum = db.get_setting("insights_permission_status")
    if durum == "missing":
        satirlar.append("❌ Insights izni yok (performans verisi toplanmıyor)")
    elif durum == "ok":
        satirlar.append("✅ Insights verisi toplanıyor")
    else:
        satirlar.append("➖ Insights durumu henüz bilinmiyor")

    try:
        t = TokenManager(db=db).get_expiry_status()
        if t.get("known"):
            kalan = t.get("days_remaining")
            satirlar.append(f"{'⚠️ ' if t.get('warning') else '✅ '}Instagram token: {kalan} gün kaldı")
        else:
            satirlar.append("➖ Instagram token ömrü bilinmiyor")
    except Exception as e:
        satirlar.append(f"➖ Token durumu okunamadı: {e}")

    with db._get_connection() as conn:
        bugun = conn.execute(
            "SELECT COUNT(*) FROM processed_content WHERE DATE(created_at)=DATE('now')"
        ).fetchone()[0]
    satirlar.append(f"🤖 Bugün üretilen içerik: {bugun}")

    satirlar.append("")
    satirlar.append(f"Günlük limitler — gönderi {DAILY_POST_LIMIT}, "
                    f"hikaye {DAILY_STORY_LIMIT}, reels {DAILY_REELS_LIMIT}")
    satirlar.append("Not: limitler yalnızca OTOMATİK yolu bağlar; "
                    "manuel onay bunları aşabilir (uyarı verilir).")

    return "\n".join(satirlar)


def help_text() -> str:
    return (
        "🤖 Teşhis Komutları\n\n"
        "/durum — servisler, disk, yedek, kuyruk\n"
        "/huni — son 24 saatin içerik hunisi\n"
        "/loglar — son hata ve uyarılar\n"
        "/kota — token ömrü, insights durumu, limitler\n"
        "/yardim — bu liste\n\n"
        "Hepsi salt-okunur: hiçbiri sistemde değişiklik yapmaz."
    )
