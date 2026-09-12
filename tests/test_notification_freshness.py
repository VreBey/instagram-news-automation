"""Bildirim tazelik penceresi İÇERİĞİN yaşını ölçmeli, haberin değil.

Kural şunun için var: kotalar dolduğu için bildirilememiş taslaklar
kuyrukta birikip günler sonra, değeri kalmamışken gönderiliyordu.

Ama ölçü `ni.collected_at` idi — HABERİN toplanma tarihi. Gönderi ve
hikâye için ikisi neredeyse aynı; REELS için değil. Reels bir DERLEME:
`news_id` yalnızca ilk habere işaret ediyor ve o haber günler önce
toplanmış oluyor.

Ölçüm (8 Ağustos 2026, üretim): bildirim kuyruğunda bekleyen 4 reels'in
2'si elenmişti — içerikler 1 günlük, ilk haberleri 4 ve 5 günlük. Yani
DÜN üretilmiş bir reels "bayat" sayılıyordu ve Telegram'a hiç ulaşmıyordu.
"""

import pytest


def _icerik(db, haber_yasi_gun, icerik_yasi_gun, tur="reels"):
    """Haberi ve içeriği AYRI yaşlarda olan bir taslak."""
    nid = db.add_news(title=f"English {haber_yasi_gun}-{icerik_yasi_gun}-{tur}",
                      url=f"https://k/{haber_yasi_gun}-{icerik_yasi_gun}-{tur}",
                      source_name="IGN", category="gaming", description="a")
    cid = db.add_content(news_id=nid, content_type=tur, caption="c",
                         summary_text="Türkçe özet")
    with db._get_connection() as c:
        c.execute("UPDATE news_items SET collected_at = datetime('now', ?) "
                  "WHERE id = ?", (f"-{haber_yasi_gun} days", nid))
        c.execute("UPDATE processed_content SET created_at = datetime('now', ?), "
                  "media_path = '/tmp/m.mp4' WHERE id = ?",
                  (f"-{icerik_yasi_gun} days", cid))
    return cid


def _kuyruk(db, gun=3):
    return {r["id"] for r in
            db.get_content_pending_telegram_notification(limit=50, max_age_days=gun)}


def test_fresh_content_from_old_news_is_sent(tmp_db):
    """
    ASIL DURUM: reels dün üretildi ama anlattığı ilk haber 5 günlük.
    İçerik taze — gönderilmeli.
    """
    cid = _icerik(tmp_db, haber_yasi_gun=5, icerik_yasi_gun=1)
    assert cid in _kuyruk(tmp_db), "yeni üretilmiş reels bayat sayıldı"


def test_stale_content_is_still_filtered(tmp_db):
    """Kural gevşetilmiyor: kuyrukta bayatlamış içerik hâlâ elenmeli."""
    cid = _icerik(tmp_db, haber_yasi_gun=1, icerik_yasi_gun=9)
    assert cid not in _kuyruk(tmp_db)


def test_boundary_is_the_content_age(tmp_db):
    icinde = _icerik(tmp_db, haber_yasi_gun=30, icerik_yasi_gun=2)
    disinda = _icerik(tmp_db, haber_yasi_gun=0, icerik_yasi_gun=4)

    kuyruk = _kuyruk(tmp_db, gun=3)

    assert icinde in kuyruk
    assert disinda not in kuyruk


@pytest.mark.parametrize("tur", ["post", "story", "reels"])
def test_rule_is_the_same_for_every_type(tmp_db, tur):
    cid = _icerik(tmp_db, haber_yasi_gun=10, icerik_yasi_gun=1, tur=tur)
    assert cid in _kuyruk(tmp_db)


def test_no_limit_means_no_filtering(tmp_db):
    cid = _icerik(tmp_db, haber_yasi_gun=40, icerik_yasi_gun=40)
    kuyruk = {r["id"] for r in
              tmp_db.get_content_pending_telegram_notification(limit=50,
                                                               max_age_days=None)}
    assert cid in kuyruk
