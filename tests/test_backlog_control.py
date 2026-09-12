"""Üretimi yayın kapasitesine bağlayan fren ve taslak raf ömrü.

Ölçüm (7 Ağustos 2026): 466 onay bekleyen taslak, en eskisi 1 Ağustos'tan.
Üretim günde ~30 içerik; Telegram'a bildirilen 8; yayınlanan 8. Kuyruk her
gün ~22 büyüyordu ve hiç erimiyordu — yani üretilen içeriğin çoğu daha
yayınlanma şansı doğmadan bayatlıyordu.

Günlük bütçe tek başına yetmiyordu: bütçe her gün sıfırlanıyor, kuyruk
sıfırlanmıyor. İki mekanizma birlikte çalışıyor — fren üretimi durduruyor,
raf ömrü kuyruğu eritip freni geri açıyor.
"""

from datetime import datetime, timedelta

import pytest


def _taslak(db, gun_once=0, status="draft"):
    news_id = db.add_news(title="English", category="gaming",
                          url=f"https://example.com/{db.get_draft_backlog_count()}-{gun_once}-{status}")
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text="Türkçe özet")
    if gun_once:
        eski = (datetime.now() - timedelta(days=gun_once)).strftime("%Y-%m-%d %H:%M:%S")
        with db._get_connection() as conn:
            conn.execute("UPDATE processed_content SET created_at=? WHERE id=?", (eski, cid))
    if status != "draft":
        db.update_content_status(cid, status)
    return cid


# =============================================
# Taslak raf ömrü
# =============================================

def test_stale_draft_is_expired(tmp_db):
    taze = _taslak(tmp_db, gun_once=0)
    bayat = _taslak(tmp_db, gun_once=5)

    assert tmp_db.expire_stale_drafts(days=3) == 1

    assert tmp_db.get_content_by_id(taze)["status"] == "draft"
    assert tmp_db.get_content_by_id(bayat)["status"] == "rejected"


def test_expiry_is_distinguishable_from_user_rejection(tmp_db):
    """
    İkisi de 'rejected' oluyor (status CHECK yeni değere izin vermiyor) ama
    çok farklı sinyaller: biri içerik kalitesi, diğeri yalnızca kapasite.
    Karışırlarsa puanlama ölçümünün yer gerçeği bozulur.
    """
    _taslak(tmp_db, gun_once=5)
    elle = _taslak(tmp_db, gun_once=0, status="rejected")

    tmp_db.expire_stale_drafts(days=3)

    with tmp_db._get_connection() as conn:
        bayatlayan = conn.execute(
            "SELECT COUNT(*) FROM processed_content WHERE expired_at IS NOT NULL"
        ).fetchone()[0]
        kullanici = conn.execute(
            "SELECT expired_at FROM processed_content WHERE id = ?", (elle,)
        ).fetchone()["expired_at"]

    assert bayatlayan == 1
    assert kullanici is None


def test_expiry_leaves_published_and_approved_alone(tmp_db):
    yayinlanmis = _taslak(tmp_db, gun_once=9, status="published")
    onayli = _taslak(tmp_db, gun_once=9, status="approved")

    assert tmp_db.expire_stale_drafts(days=3) == 0
    assert tmp_db.get_content_by_id(yayinlanmis)["status"] == "published"
    assert tmp_db.get_content_by_id(onayli)["status"] == "approved"


def test_expiry_is_idempotent(tmp_db):
    _taslak(tmp_db, gun_once=5)
    assert tmp_db.expire_stale_drafts(days=3) == 1
    assert tmp_db.expire_stale_drafts(days=3) == 0


# =============================================
# Üretim freni
# =============================================

@pytest.fixture
def islemci(tmp_db, monkeypatch):
    from src.content_processor import ContentProcessor
    p = ContentProcessor(db=tmp_db)
    p.client = object()
    # Haber işleme yolunu tamamen sahteleyelim: burada ölçtüğümüz şey
    # üretimin BAŞLAYIP başlamadığı.
    monkeypatch.setattr(p, "_calculate_relevance", lambda news: 0.9)
    monkeypatch.setattr(p, "_create_post_content",
                        lambda news: {"caption": "c", "hashtags": [], "summary": "ö"})
    monkeypatch.setattr(p, "_create_story_content", lambda news: "hikaye")
    return p


def _islenmemis_haber(db, n):
    for i in range(n):
        db.add_news(title=f"English {i}", url=f"https://example.com/haber{i}",
                    category="gaming")


def test_production_pauses_when_backlog_is_full(islemci, tmp_db, monkeypatch):
    monkeypatch.setattr("src.content_processor.DRAFT_BACKLOG_DAYS", 2)
    _islenmemis_haber(tmp_db, 5)
    # Kapasite 2+5+1=8, tavan 8*2=16. Taslaklar DÜNDEN: bugün üretilmiş
    # olsalardı günlük bütçe zaten dolardı ve fren sınanamazdı.
    for _ in range(16):
        _taslak(tmp_db, gun_once=1)

    stats = islemci.process_all_news()

    assert stats["processed"] == 0
    assert stats["paused_for_backlog"] == 16


def test_production_resumes_when_backlog_drains(islemci, tmp_db, monkeypatch):
    """
    Fren kalıcı değil: kuyruk eridikçe üretim kendiliğinden başlar. Aksi
    halde sistem bir kez durduğunda bir daha çalışmazdı.
    """
    monkeypatch.setattr("src.content_processor.DRAFT_BACKLOG_DAYS", 2)
    _islenmemis_haber(tmp_db, 5)
    taslaklar = [_taslak(tmp_db, gun_once=5) for _ in range(16)]

    assert islemci.process_all_news()["processed"] == 0

    tmp_db.expire_stale_drafts(days=3)  # kuyruk erisin

    assert islemci.process_all_news()["processed"] > 0
    assert len(taslaklar) == 16


def test_backlog_count_ignores_decided_content(tmp_db):
    _taslak(tmp_db)
    _taslak(tmp_db, status="rejected")
    _taslak(tmp_db, status="published")

    assert tmp_db.get_draft_backlog_count() == 1
