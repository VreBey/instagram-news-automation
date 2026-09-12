"""Yerel yazılan sütunlar yerel saatle karşılaştırılmalı.

Bu şemada iki zaman evreni var (bkz. src/database.py başındaki kural):

  YEREL (Python, TZ=Europe/Istanbul)   UTC (SQLite datetime('now'))
  publish_history.published_at         news_items.collected_at
  scheduled_posts.scheduled_time       processed_content.created_at

`published_at` bilerek yerel yazılıyor, ama ona karşı yapılan
karşılaştırmaların bir kısmı SQLite'ın UTC `now`'ını kullanıyordu.
Canlı ölçüm (7 Ağustos 2026, servis ortamı): Python 15:22, SQLite 12:22 —
3 saat fark. Sonuçları:

  * `/huni` raporundaki "bugün yayınlanan", yerel 00:00-03:00 arasında
    YANLIŞ GÜNÜ sayıyordu. Sayı gerçek veriden geliyordu ama etiketiyle
    uyuşmuyordu.
  * Insights penceresi 24 saat yerine fiilen 27 saat açık kalıyor ve son
    3 saatte garantili başarısız API çağrısı üretiyordu.

Bu testler saat dilimini taklit edemez (SQLite'ın `now`'ı süreç TZ'sinden
bağımsız), o yüzden DAVRANIŞI değil KURALI kilitliyorlar: `published_at`
ile karşılaştırmalarda SQLite'ın `now` fonksiyonu geçmemeli.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent

# `published_at` ile aynı ifadede SQLite'ın kendi "şimdi"si geçiyor mu?
_YASAK = re.compile(
    r"published_at\s*(?:>=|<=|>|<|=)\s*(?:DATE|datetime)\('now'",
    re.IGNORECASE,
)
_YASAK_TERS = re.compile(
    r"DATE\(published_at\)\s*=\s*DATE\('now'\)",
    re.IGNORECASE,
)


def _kaynaklar():
    for yol in sorted((KOK / "src").glob("*.py")):
        yield yol, yol.read_text(encoding="utf-8", errors="ignore")


def test_published_at_is_never_compared_to_sqlite_now():
    ihlaller = []
    for yol, metin in _kaynaklar():
        for kalip in (_YASAK, _YASAK_TERS):
            for m in kalip.finditer(metin):
                satir = metin[:m.start()].count("\n") + 1
                ihlaller.append(f"{yol.name}:{satir} — {m.group(0)}")
    assert not ihlaller, (
        "published_at YEREL yazılıyor, SQLite'ın now'ı UTC. "
        "Zamanı Python'dan parametre geçin:\n  " + "\n  ".join(ihlaller)
    )


def test_local_helpers_produce_local_time():
    from src.database import _local_now, _local_today

    simdi = datetime.now()
    assert _local_today() == simdi.strftime("%Y-%m-%d")
    uretilen = datetime.strptime(_local_now(), "%Y-%m-%d %H:%M:%S")
    assert abs((uretilen - simdi).total_seconds()) < 5


def test_local_helpers_apply_offsets():
    from src.database import _local_now, _local_today

    assert _local_today(offset_days=-3) == (
        datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    gecmis = datetime.strptime(_local_now(offset_hours=-24), "%Y-%m-%d %H:%M:%S")
    fark = datetime.now() - gecmis
    assert 23.9 < fark.total_seconds() / 3600 < 24.1


def test_insights_window_is_exactly_24_hours(tmp_db):
    """
    Hikayeler için pencere 24 saat olmalı. UTC/yerel karışıklığı yüzünden
    fiilen 27 saatti ve son 3 saatte Meta'dan garantili hata alınıyordu
    (hikaye insights'ı 24 saat sonra kayboluyor).
    """
    news_id = tmp_db.add_news(title="x", url="https://example.com/tz", category="ai")
    cid = tmp_db.add_content(news_id=news_id, content_type="story", summary_text="ö")
    ph = tmp_db.add_publish_record(content_id=cid, post_type="story",
                                   status="success", instagram_media_id="m1")
    # 25 saat öncesine çek: pencerenin DIŞINDA kalmalı.
    eski = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE publish_history SET published_at=? WHERE id=?", (eski, ph))

    kayitlar = tmp_db.get_publish_history_for_insights(days=14)

    assert not [k for k in kayitlar if k["post_type"] == "story"], \
        "24 saatten eski hikaye hâlâ sorgulanıyor"
