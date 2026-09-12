"""Süre dolumu eşikleri, karşılaştırdıkları sütunla AYNI zaman evreninde olmalı.

`news_items.collected_at` ve `processed_content.created_at` SQLite'ın
`datetime('now')` değeriyle yazılıyor — yani UTC. Eşik ise Python'ın
`datetime.now()`'ı ile, yani YEREL (TZ=Europe/Istanbul, UTC+3) hesaplanıyordu.

Sonuç: eşik 3 saat İLERİDE kalıyor, `created_at < esik` koşuluna olması
gerekenden fazla satır giriyor ve taslaklar 3 saat ERKEN düşüyordu.
Bkz. src/database.py başındaki ZAMAN KURALI.

Bu testler saat farkını doğrudan kurar; makinenin gerçek saat dilimine
bağlı değildir.
"""

import re

from src.database import Database


def _ekle_taslak(db, saat_once):
    """Şu andan `saat_once` saat önce (UTC) oluşturulmuş bir taslak."""
    nid = db.add_news(
        title="Haber", url=f"https://ornek/{saat_once}", source_name="IGN",
        category="gaming", description="aciklama",
    )
    cid = db.add_content(news_id=nid, content_type="post", caption="c",
                         summary_text="Türkçe özet")
    with db._get_connection() as c:
        c.execute(
            "UPDATE processed_content SET created_at = datetime('now', ?) "
            "WHERE id = ?", (f"-{saat_once} hours", cid))
    return cid


def _durum(db, cid):
    with db._get_connection() as c:
        return c.execute(
            "SELECT status FROM processed_content WHERE id = ?", (cid,)
        ).fetchone()[0]


def test_draft_just_inside_the_window_survives(tmp_db):
    """
    3 gün eksi 2 saat yaşındaki taslak DURMALI. Yerel/UTC karışımıyla
    eşik 3 saat ileri kayıyordu ve bu taslak düşüyordu.
    """
    cid = _ekle_taslak(tmp_db, saat_once=3 * 24 - 2)
    tmp_db.expire_stale_drafts(days=3)
    assert _durum(tmp_db, cid) == "draft"


def test_draft_past_the_window_is_dropped(tmp_db):
    cid = _ekle_taslak(tmp_db, saat_once=3 * 24 + 2)
    tmp_db.expire_stale_drafts(days=3)
    assert _durum(tmp_db, cid) == "rejected"


def test_expired_at_is_stamped(tmp_db):
    """
    Damga şart: bunlar KULLANICININ reddettikleriyle karışmamalı —
    biri içerik kalitesi, diğeri yalnızca kapasite sinyali.
    """
    cid = _ekle_taslak(tmp_db, saat_once=3 * 24 + 2)
    tmp_db.expire_stale_drafts(days=3)
    with tmp_db._get_connection() as c:
        damga = c.execute(
            "SELECT expired_at FROM processed_content WHERE id = ?", (cid,)
        ).fetchone()[0]
    assert damga is not None


def test_expired_at_is_utc_like_its_siblings(tmp_db):
    """
    `expired_at` kardeş sütunlarla aynı evrende olmalı, yoksa ileride
    ona karşı yapılacak her karşılaştırma aynı hatayı tekrarlar.
    """
    cid = _ekle_taslak(tmp_db, saat_once=3 * 24 + 2)
    tmp_db.expire_stale_drafts(days=3)
    with tmp_db._get_connection() as c:
        fark = c.execute(
            "SELECT ABS(strftime('%s', expired_at) - strftime('%s','now')) "
            "FROM processed_content WHERE id = ?", (cid,)
        ).fetchone()[0]
    assert fark < 120, f"expired_at UTC 'now'dan {fark} sn sapıyor"


def test_approved_content_is_never_expired(tmp_db):
    """
    Kullanıcı onayladıysa karar KULLANICININDIR — medyası olmasa bile
    sistem bunu geri almaz.

    Bu kuralı bir ara medyasız onaylılar için gevşetmeye kalktım; gerekçem
    takılı bir kaydın haber havuzunu kilitlemesiydi. Mevcut test yakaladı
    ve haklıydı: o sorunun yeri burası değil, `_pending_reels_news_ids`
    (bkz. tests/test_reels_pending_exclusion.py).
    """
    cid = _ekle_taslak(tmp_db, saat_once=9 * 24)
    tmp_db.update_content_status(cid, "approved")

    tmp_db.expire_stale_drafts(days=3)

    assert _durum(tmp_db, cid) == "approved"


def test_published_content_is_never_touched(tmp_db):
    cid = _ekle_taslak(tmp_db, saat_once=30 * 24)
    tmp_db.update_content_status(cid, "published")

    tmp_db.expire_stale_drafts(days=3)

    assert _durum(tmp_db, cid) == "published"


def test_news_just_inside_the_window_survives(tmp_db):
    nid = tmp_db.add_news(title="H", url="https://ornek/taze", source_name="IGN",
                          category="gaming", description="a")
    with tmp_db._get_connection() as c:
        c.execute("UPDATE news_items SET collected_at = datetime('now','-46 hours') "
                  "WHERE id = ?", (nid,))
    tmp_db.expire_stale_unprocessed_news(days=2)
    with tmp_db._get_connection() as c:
        islendi = c.execute("SELECT is_processed FROM news_items WHERE id = ?",
                            (nid,)).fetchone()[0]
    assert islendi == 0, "haber 2 saat erken emekliye ayrıldı"


def test_news_past_the_window_is_retired(tmp_db):
    nid = tmp_db.add_news(title="H", url="https://ornek/bayat", source_name="IGN",
                          category="gaming", description="a")
    with tmp_db._get_connection() as c:
        c.execute("UPDATE news_items SET collected_at = datetime('now','-50 hours') "
                  "WHERE id = ?", (nid,))
    tmp_db.expire_stale_unprocessed_news(days=2)
    with tmp_db._get_connection() as c:
        satir = c.execute("SELECT is_processed, expired_at FROM news_items "
                          "WHERE id = ?", (nid,)).fetchone()
    assert satir[0] == 1 and satir[1] is not None


def test_no_local_clock_in_the_expiry_queries():
    """
    Yapısal koruma: bu iki metot bir daha Python'ın YEREL saatini eşik
    olarak kullanmasın. Aynı hata iki kez yazıldı, üçüncüsü olmasın.
    """
    import inspect

    for metot in (Database.expire_stale_drafts,
                  Database.expire_stale_unprocessed_news):
        kaynak = inspect.getsource(metot)
        gövde = "\n".join(
            s for s in kaynak.splitlines() if not s.strip().startswith("#")
        )
        assert not re.search(r"datetime\.now\(\)", gövde), (
            f"{metot.__name__} yerel saati eşik olarak kullanıyor"
        )
