"""Günlük işleme bütçesi: üretim, yayın kapasitesiyle orantılı kalmalı.

Ölçüm (3-5 Ağustos 2026): 251 içerik üretildi, 58'ine karar verildi,
onay kuyruğunda +193 birikme oluştu. En eski bekleyen içerik 4 günlüktü.

Sebep formüldeydi: `max(POST*MULT, STORY*MULT)` "günlük" diye tasarlanmıştı
ama HER TURDA yeniden uygulanıyordu. Boru hattı günde birkaç kez çalıştığı
için (zamanlanmış tur + yeniden başlatma telafisi + elle çalıştırmalar)
gerçek üretim, günlük yayın kapasitesinin (8 içerik) 8-11 katına çıkıyordu.

Fazla üretim bedava değil: Gemini kotası, görsel üretimi, disk ve
kullanıcının karar verme dikkati.
"""

import pytest

from src.content_processor import ContentProcessor


@pytest.fixture
def processor(tmp_db, monkeypatch):
    monkeypatch.setattr("src.content_processor.DAILY_POST_LIMIT", 2)
    monkeypatch.setattr("src.content_processor.DAILY_STORY_LIMIT", 5)
    monkeypatch.setattr("src.content_processor.MEDIA_GENERATION_MULTIPLIER", 3)
    monkeypatch.setattr("src.content_processor.MIN_PROCESSING_SCORE", 0.0)
    p = ContentProcessor(db=tmp_db)
    p.client = object()
    monkeypatch.setattr(p, "_calculate_relevance", lambda news: 0.9)
    monkeypatch.setattr(
        p, "_create_post_content",
        lambda news: {"caption": "c", "hashtags": ["#a"], "summary": "Türkçe özet"},
    )
    monkeypatch.setattr(p, "_create_story_content", lambda news: "Türkçe hikaye")
    return p


def _haber_ekle(db, n):
    for i in range(n):
        db.add_news(title=f"Haber {i}", url=f"https://example.com/{i}", category="ai")


# =============================================
# Günlük bütçe
# =============================================

def test_first_run_uses_full_budget(processor, tmp_db):
    _haber_ekle(tmp_db, 40)

    sonuc = processor.process_all_news()

    # max(2*3, 5*3) = 15
    assert sonuc["processed"] == 15


def test_second_run_same_day_is_capped(processor, tmp_db):
    """
    Asıl regresyon: boru hattı günde birkaç kez çalışıyor ve her turda
    bütçe sıfırdan uygulanıyordu.
    """
    _haber_ekle(tmp_db, 40)

    ilk = processor.process_all_news()
    ikinci = processor.process_all_news()

    assert ilk["processed"] == 15
    assert ikinci["processed"] == 0, "gunluk butce dolduysa ikinci tur uretmemeli"


def test_partial_budget_remaining(processor, tmp_db, monkeypatch):
    """Bütçenin bir kısmı kullanılmışsa yalnızca kalanı işlenmeli."""
    _haber_ekle(tmp_db, 40)
    monkeypatch.setattr(tmp_db, "get_news_processed_today_count", lambda: 12)

    sonuc = processor.process_all_news()

    assert sonuc["processed"] == 3  # 15 - 12


def test_budget_resets_next_day(processor, tmp_db):
    """Dünkü üretim bugünkü bütçeyi tüketmemeli.

    Dünkü taslaklar 'published' yapılıyor: bütçe ile ÜRETİM FRENİ ayrı iki
    mekanizma (bkz. tests/test_backlog_control.py) ve burada ölçülen yalnızca
    bütçenin sıfırlanması. Taslaklar kuyrukta bırakılsaydı fren devreye
    girerdi — ki üretimin durması o senaryoda zaten doğru davranış olurdu.
    """
    _haber_ekle(tmp_db, 40)
    processor.process_all_news()

    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE processed_content "
                     "SET created_at = datetime('now','-1 day'), status = 'published'")

    sonuc = processor.process_all_news()
    assert sonuc["processed"] == 15


def test_expired_news_does_not_consume_budget(tmp_db):
    """
    Emekliye ayrılan haberler için içerik üretilmedi; bütçeyi tüketmemeli.
    """
    _haber_ekle(tmp_db, 5)
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE news_items SET collected_at = datetime('now','-5 days')")
    tmp_db.expire_stale_unprocessed_news(days=2)

    assert tmp_db.get_news_processed_today_count() == 0


def test_counts_distinct_news_not_content_rows(tmp_db):
    """
    Bir haber hem post hem story üretiyor. Bütçe HABER sayar, içerik satırı
    değil — yoksa gerçek üretimin yarısında dolardı.
    """
    news_id = tmp_db.add_news(title="H", url="https://example.com/1", category="ai")
    tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.add_content(news_id=news_id, content_type="story", summary_text="s")

    assert tmp_db.get_news_processed_today_count() == 1
