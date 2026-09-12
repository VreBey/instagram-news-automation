"""Retention çöpü toplar, yayın kaydını silmez.

`PRAGMA foreign_keys` bu projede AÇIK, yani `news_items`'tan silmek cascade
ile yayılıyor: processed_content → publish_history → media_insights.

`is_used = 1` bayrağını koyan tek yer `create_reels_content` ve o da yalnızca
GERÇEKTEN YAYINLANMIŞ haberleri seçiyor. Yani eski temizlik sorgusu, tam da
yayın geçmişi taşıyan satırları hedefliyordu.

Canlı ölçüm (7 Ağustos 2026): 76 publish_history kaydının 56'sı (%74) bu
koşula giriyordu ve 30 günü doldurduklarında 184 media_insights kaydıyla
birlikte silineceklerdi. Hesap 8 günlük olduğu için henüz kaybolan olmamıştı.

Bu tablolar "önce ölç, sonra düzelt" yaklaşımının dayandığı tek veri kaynağı.
"""

from datetime import datetime, timedelta


def _eski_haber(db, url, gun=60, is_used=1):
    news_id = db.add_news(title="English", url=url, category="gaming")
    eski = (datetime.now() - timedelta(days=gun)).strftime("%Y-%m-%d %H:%M:%S")
    with db._get_connection() as conn:
        conn.execute("UPDATE news_items SET collected_at=?, is_used=? WHERE id=?",
                     (eski, is_used, news_id))
    return news_id


def _yayinla(db, news_id):
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text="özet")
    db.add_publish_record(content_id=cid, post_type="post", status="success",
                          instagram_media_id=f"m-{news_id}")
    return cid


def test_published_news_survives_cleanup(tmp_db):
    yayinlanmis = _eski_haber(tmp_db, "https://example.com/y")
    _yayinla(tmp_db, yayinlanmis)

    tmp_db.cleanup_old_data(days=30)

    with tmp_db._get_connection() as conn:
        kalan = conn.execute("SELECT COUNT(*) FROM news_items WHERE id=?",
                             (yayinlanmis,)).fetchone()[0]
        gecmis = conn.execute("SELECT COUNT(*) FROM publish_history").fetchone()[0]
    assert kalan == 1, "yayınlanmış haber silinmiş"
    assert gecmis == 1, "yayın geçmişi cascade ile silinmiş"


def test_unpublished_used_news_is_still_cleaned(tmp_db):
    """Yayınlanmamış eski satırlar temizlenmeye devam etmeli — retention işini yapsın."""
    cop = _eski_haber(tmp_db, "https://example.com/c")
    tmp_db.add_content(news_id=cop, content_type="post", caption="c")

    assert tmp_db.cleanup_old_data(days=30) == 1

    with tmp_db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_items WHERE id=?",
                            (cop,)).fetchone()[0] == 0


def test_insights_survive_with_their_publish_record(tmp_db):
    """Performans ölçümü yayın kaydıyla birlikte korunmalı."""
    news_id = _eski_haber(tmp_db, "https://example.com/i")
    cid = _yayinla(tmp_db, news_id)
    with tmp_db._get_connection() as conn:
        ph_id = conn.execute("SELECT id FROM publish_history WHERE content_id=?",
                             (cid,)).fetchone()[0]
    tmp_db.add_media_insight(publish_history_id=ph_id, reach=4, likes=0)

    tmp_db.cleanup_old_data(days=30)

    with tmp_db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_insights").fetchone()[0] == 1


def test_recent_news_is_untouched(tmp_db):
    taze = _eski_haber(tmp_db, "https://example.com/t", gun=1)
    assert tmp_db.cleanup_old_data(days=30) == 0
    with tmp_db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_items WHERE id=?",
                            (taze,)).fetchone()[0] == 1
