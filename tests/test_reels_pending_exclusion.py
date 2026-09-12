"""Onay bekleyen bir reels'in haberleri yeniden seçilmemeli.

`is_used` bayrağı tek başına yetmiyor: işaretleme bilinçli olarak ÜRETİM
anından TESLİM anına taşındı (yayınlanmayan taslaklar haber yakmasın diye).
Ama `get_published_for_reels`teki `is_used = 0` filtresi hâlâ üretimde
işaretlendiğini varsayıyordu. İki düzeltme çelişince, onay bekleyen bir
taslağın haberleri ertesi gün yeniden seçilebilir hale geldi.

Ölçüm (8 Ağustos 2026): bildirim penceresindeki 9 reels taslağı yalnızca
4 FARKLI haber kümesi anlatıyordu; iki grup birebir aynıydı (biri 4,
diğeri 3 kopya). Kullanıcı bunların hepsini Telegram'da görecekti.
"""

import pytest


def _yayinlanmis_haber(db, ek: str) -> int:
    """Reels havuzuna girecek, gerçekten yayınlanmış bir haber."""
    nid = db.add_news(title=f"English {ek}", url=f"https://kaynak/{ek}",
                      source_name="IGN", category="gaming",
                      description="aciklama", image_url=f"https://cdn/{ek}.jpg")
    db.mark_news_processed(nid, relevance_score=0.8)
    cid = db.add_content(news_id=nid, content_type="post", caption="c",
                         summary_text=f"Türkçe özet {ek}")
    db.add_publish_record(content_id=cid, post_type="post", status="success",
                          instagram_media_id=f"m{ek}")
    return nid


@pytest.fixture
def havuz(tmp_db):
    return [_yayinlanmis_haber(tmp_db, str(i)) for i in range(4)]


def test_pool_is_full_without_pending_reels(tmp_db, havuz):
    assert len(tmp_db.get_published_for_reels(limit=10)) == 4


def test_pending_reels_news_are_excluded(tmp_db, havuz):
    tmp_db.add_content(
        news_id=havuz[0], content_type="reels", caption="c",
        reels_script={"segments": ["a", "b"], "news_ids": [havuz[0], havuz[1]]},
    )

    kalan = {h["id"] for h in tmp_db.get_published_for_reels(limit=10)}

    assert kalan == {havuz[2], havuz[3]}, "bekleyen taslağın haberleri yine seçildi"


def test_rejected_reels_release_their_news(tmp_db, havuz):
    """
    Reddedilen taslak havuzu KİLİTLEMEMELİ — yoksa her ret kalıcı bir
    sızıntı olurdu.
    """
    cid = tmp_db.add_content(
        news_id=havuz[0], content_type="reels", caption="c",
        reels_script={"segments": ["a"], "news_ids": [havuz[0], havuz[1]]},
    )
    tmp_db.update_content_status(cid, "rejected")

    kalan = {h["id"] for h in tmp_db.get_published_for_reels(limit=10)}

    assert kalan == set(havuz)


def test_published_reels_do_not_lock_the_pool_twice(tmp_db, havuz):
    """
    Yayınlanmış reels için zaten `is_used` işareti var; bu filtre
    sadece BEKLEYENLERE bakmalı.
    """
    cid = tmp_db.add_content(
        news_id=havuz[0], content_type="reels", caption="c",
        reels_script={"segments": ["a"], "news_ids": [havuz[0]]},
    )
    tmp_db.update_content_status(cid, "published")

    kalan = {h["id"] for h in tmp_db.get_published_for_reels(limit=10)}

    assert havuz[1] in kalan and havuz[2] in kalan


def test_approved_without_media_does_not_lock_the_pool(tmp_db, havuz):
    """
    Onaylı ama MEDYASIZ bir reels yayınlanamaz, yani onaylı durumda
    süresiz kalır. Sayılsaydı anlattığı haberleri havuzdan KALICI olarak
    düşürürdü.

    Üretimde tam olarak böyle bir kayıt bulundu (id=467, 6 günlük,
    medyasız). Çözüm onun durumunu değiştirmek değil — kullanıcının
    onayını sistem geri almaz — bu sorguda teslim edilemeyeceğini görmek.
    """
    tmp_db.add_content(
        news_id=havuz[0], content_type="reels", caption="c",
        reels_script={"segments": ["a"], "news_ids": [havuz[0], havuz[1]]},
    )
    with tmp_db._get_connection() as c:
        c.execute("UPDATE processed_content SET status='approved', "
                  "media_path=NULL WHERE content_type='reels'")

    kalan = {h["id"] for h in tmp_db.get_published_for_reels(limit=10)}

    assert kalan == set(havuz), "teslim edilemeyecek reels havuzu kilitledi"


def test_approved_with_media_still_holds_its_news(tmp_db, havuz):
    """Teslim edilebilir onaylı bir reels haberlerini tutmalı."""
    cid = tmp_db.add_content(
        news_id=havuz[0], content_type="reels", caption="c",
        reels_script={"segments": ["a"], "news_ids": [havuz[0]]},
    )
    with tmp_db._get_connection() as c:
        c.execute("UPDATE processed_content SET status='approved', "
                  "media_path='/tmp/v.mp4' WHERE id=?", (cid,))

    kalan = {h["id"] for h in tmp_db.get_published_for_reels(limit=10)}

    assert havuz[0] not in kalan


def test_old_script_without_news_ids_does_not_crash(tmp_db, havuz):
    """
    Alanı taşımayan eski senaryolar katkı veremez ama sorguyu da
    düşürmemeli.
    """
    tmp_db.add_content(
        news_id=havuz[0], content_type="reels", caption="c",
        reels_script={"segments": ["a"], "news_titles": ["Türkçe özet 0"]},
    )

    assert len(tmp_db.get_published_for_reels(limit=10)) == 4


def test_corrupt_script_does_not_crash(tmp_db, havuz):
    cid = tmp_db.add_content(news_id=havuz[0], content_type="reels", caption="c",
                             reels_script={"segments": ["a"]})
    with tmp_db._get_connection() as c:
        c.execute("UPDATE processed_content SET reels_script='{bozuk' WHERE id=?",
                  (cid,))

    assert len(tmp_db.get_published_for_reels(limit=10)) == 4
