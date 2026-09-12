"""Günlük derleme testleri.

Bağlam (3 Ağustos 2026): günde ~75 haber toplanıyor ama feed kotası 2
gönderi. Kapsamı tek tek yayınlayarak büyütmek spam sinyali — 2 Ağustos'ta
DAILY_POST_LIMIT=2 ayarlıyken 18 gönderi yayınlandı. Derleme kapsamı gönderi
SAYISINI artırmadan büyütür.
"""

import pytest

from src.roundup import (
    build_roundup_caption, build_roundup_slides, clean_source_name,
    is_translated, select_roundup_items,
)


def _item(i=1, summary="Bir haber özeti", category="ai", source="IGN", title=None):
    return {
        "id": i, "news_id": i, "summary_text": summary, "category": category,
        "source_name": source, "image_url": f"https://example.com/{i}.jpg",
        "news_title": title or f"News {i}",
    }


def _seed_draft(db, i, score=0.8, summary="Türkçe özet", content_type="post"):
    news_id = db.add_news(title=f"Haber {i}", url=f"https://example.com/n{i}",
                          category="ai", source_name=f"Kaynak{i}")
    db.mark_news_processed(news_id, relevance_score=score)
    return db.add_content(news_id=news_id, content_type=content_type,
                          caption="c", summary_text=summary)


# =============================================
# Slaytlar
# =============================================

def test_slides_have_cover_plus_one_per_item():
    slides = build_roundup_slides([_item(1), _item(2), _item(3)])
    assert len(slides) == 4
    assert "3 Haberi" in slides[0]["display_title"]


def test_cover_has_no_image():
    """
    Kapağa haberlerden birinin görselini koymak, derlemeyi o haberin
    gönderisi gibi gösterirdi.
    """
    slides = build_roundup_slides([_item(1)])
    assert slides[0]["image_url"] is None


def test_each_slide_keeps_its_own_source_and_image():
    """Paylaşılan tek görsel kullanmak alakasız eşleşmelere yol açardı."""
    slides = build_roundup_slides([_item(1, source="IGN"), _item(2, source="Kotaku")])
    assert slides[1]["source_name"] == "IGN"
    assert slides[2]["source_name"] == "Kotaku"
    assert slides[1]["image_url"] != slides[2]["image_url"]


def test_slides_use_turkish_summary_not_english_title():
    """Derlemenin ek Gemini maliyeti olmamasının sebebi: özet zaten Türkçe."""
    slides = build_roundup_slides([_item(1, summary="Türkçe özet", title="English Title")])
    # `display_title` sözleşmesi: slayt üreticisi çizilecek metni AÇIKÇA
    # verir; çizim tarafı kaynak alanlara düşmez.
    assert slides[1]["display_title"] == "Türkçe özet"


def test_empty_items_produce_no_slides():
    assert build_roundup_slides([]) == []


# =============================================
# Caption
# =============================================

def test_caption_hook_is_short_enough_for_instagram():
    """Instagram ~125 karakterde '… daha' ile kesiyor."""
    caption = build_roundup_caption([_item(i) for i in range(1, 7)])
    assert len(caption.split("\n")[0]) <= 120


def test_caption_lists_every_item():
    items = [_item(i, summary=f"Haber özeti {i}") for i in range(1, 6)]
    caption = build_roundup_caption(items)
    for i in range(1, 6):
        assert f"{i}." in caption


def test_caption_credits_sources():
    caption = build_roundup_caption([_item(1, source="Eurogamer")])
    assert "Eurogamer" in caption


def test_caption_hashtag_count_within_limit():
    """3-5 niş etiket; fazlası 2026'da spam sinyali."""
    caption = build_roundup_caption([_item(i) for i in range(1, 7)])
    assert 1 <= caption.count("#") <= 5


def test_caption_truncates_long_headlines_at_word_boundary():
    uzun = "Bu çok uzun bir haber başlığıdır ve kesinlikle sınırı aşacak kadar uzundur gerçekten"
    caption = build_roundup_caption([_item(1, summary=uzun)])
    satir = [s for s in caption.split("\n") if s.startswith("1.")][0]
    assert "…" in satir
    assert not satir.replace("…", "").rstrip().endswith(" ")


def test_caption_picks_gaming_hashtags_when_gaming_dominates():
    items = [_item(1, category="gaming"), _item(2, category="gaming"),
             _item(3, category="ai")]
    assert "#oyun" in build_roundup_caption(items)


# =============================================
# Aday seçimi
# =============================================

def test_selection_requires_minimum_items(tmp_db, monkeypatch):
    """2 haberlik bir 'günün haberleri' gönderisi zayıf durur."""
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 4)
    for i in range(2):
        _seed_draft(tmp_db, i)
    assert select_roundup_items(tmp_db) == []


def test_selection_returns_items_when_enough(tmp_db, monkeypatch):
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 4)
    for i in range(6):
        _seed_draft(tmp_db, i)
    assert len(select_roundup_items(tmp_db, limit=6)) == 6


def test_selection_prefers_high_relevance(tmp_db):
    _seed_draft(tmp_db, 1, score=0.4, summary="dusuk")
    _seed_draft(tmp_db, 2, score=0.95, summary="yuksek")
    adaylar = tmp_db.get_roundup_candidates(limit=1)
    assert adaylar[0]["summary_text"] == "yuksek"


def test_selection_skips_stories(tmp_db):
    """Hikaye metinleri feed gönderisi için fazla kısa ve farklı tonda."""
    _seed_draft(tmp_db, 1, content_type="story")
    assert tmp_db.get_roundup_candidates(limit=5) == []


def test_selection_skips_items_without_summary(tmp_db):
    news_id = tmp_db.add_news(title="H", url="https://example.com/x", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                       summary_text=None)
    assert tmp_db.get_roundup_candidates(limit=5) == []


# =============================================
# Çift yayın koruması
# =============================================

def test_used_items_are_excluded_from_next_roundup(tmp_db):
    ids = [_seed_draft(tmp_db, i) for i in range(5)]
    tmp_db.mark_used_in_roundup(ids[:3])
    kalan = tmp_db.get_roundup_candidates(limit=10)
    assert len(kalan) == 2


def test_used_items_are_not_notified_individually(tmp_db):
    """Aynı haber hem derlemede hem tekil gönderi olarak çıkmamalı."""
    cid = _seed_draft(tmp_db, 1)
    tmp_db.update_content_media(cid, "/tmp/a.png")
    assert len(tmp_db.get_content_pending_telegram_notification(limit=10)) == 1

    tmp_db.mark_used_in_roundup([cid])
    assert tmp_db.get_content_pending_telegram_notification(limit=10) == []


def test_used_items_are_not_auto_scheduled(tmp_db):
    cid = _seed_draft(tmp_db, 1)
    assert len(tmp_db.get_draft_content(limit=10)) == 1
    tmp_db.mark_used_in_roundup([cid])
    assert tmp_db.get_draft_content(limit=10) == []


def test_marking_empty_list_is_safe(tmp_db):
    assert tmp_db.mark_used_in_roundup([]) == 0


# =============================================
# Çeviri kontrolü ve kaynak temizliği
#
# İlk canlı derlemede 6 haberin 5'inde özet, İngilizce başlığın birebir
# kopyasıydı — çeviri adımı atlanmıştı. "summary_text zaten Türkçe"
# varsayımı yanlıştı ve onaylansaydı İngilizce manşetler yayınlanacaktı.
# =============================================

def test_untranslated_summary_is_detected():
    baslik = "Final Fantasy XIV Launches on Nintendo Switch 2 August 4, First Time"
    assert is_translated({"summary_text": baslik, "news_title": baslik}) is False


def test_translated_summary_passes():
    assert is_translated({
        "summary_text": "Yapay zeka patlaması psikoloji öğrencilerini kodlamaya yöneltti",
        "news_title": "AI boom pushes psychology students toward coding",
    }) is True


def test_empty_summary_is_not_translated():
    assert is_translated({"summary_text": "", "news_title": "X"}) is False


def test_comparison_ignores_case():
    assert is_translated({"summary_text": "ABC haber", "news_title": "abc haber"}) is False


def test_untranslated_items_are_excluded_from_selection(tmp_db, monkeypatch):
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 2)
    for i in range(3):
        news_id = tmp_db.add_news(title=f"English Headline {i}",
                                  url=f"https://example.com/e{i}",
                                  category="ai", source_name=f"K{i}")
        tmp_db.mark_news_processed(news_id, relevance_score=0.9)
        # Özet = başlık → çevrilmemiş
        tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                           summary_text=f"English Headline {i}")
    for i in range(2):
        news_id = tmp_db.add_news(title=f"English Title {i}",
                                  url=f"https://example.com/t{i}",
                                  category="ai", source_name=f"T{i}")
        tmp_db.mark_news_processed(news_id, relevance_score=0.8)
        tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                           summary_text=f"Türkçe çevrilmiş özet {i}")

    secim = select_roundup_items(tmp_db, limit=6)

    assert len(secim) == 2
    assert all("Türkçe" in i["summary_text"] for i in secim)


def test_selection_applies_source_diversity(tmp_db, monkeypatch):
    """
    Çeşitlilik tavanı get_unprocessed_news'e eklenmişti ama derleme adayları
    oradan geçmiyor — ilk canlı derlemede 6 haberin 3'ü tek kaynaktandı.
    """
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 2)
    for i in range(10):
        news_id = tmp_db.add_news(title=f"EN {i}", url=f"https://example.com/m{i}",
                                  category="ai", source_name="Tekel")
        tmp_db.mark_news_processed(news_id, relevance_score=0.95)
        tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                           summary_text=f"Türkçe özet {i}")
    for i in range(6):
        news_id = tmp_db.add_news(title=f"EN d{i}", url=f"https://example.com/d{i}",
                                  category="ai", source_name=f"Kaynak{i}")
        tmp_db.mark_news_processed(news_id, relevance_score=0.7)
        tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                           summary_text=f"Türkçe diğer {i}")

    secim = select_roundup_items(tmp_db, limit=6)
    tekel = sum(1 for i in secim if i["source_name"] == "Tekel")
    assert tekel <= 2
    assert len({i["source_name"] for i in secim}) >= 4


@pytest.mark.parametrize("ham,beklenen", [
    ("IGN", "IGN"),
    ("Heather Hollingsworth, The Associated Press; Heather Hollingsworth", "Heather Hollingsworth"),
    ("Wang; Bo; Tang; Sijin; Tan; Pingjuan", "Wang"),
    ("", ""),
    (None, ""),
])
def test_source_name_is_cleaned(ham, beklenen):
    assert clean_source_name(ham) == beklenen


def test_very_long_single_source_is_truncated():
    out = clean_source_name("A" * 60)
    assert len(out) <= 25 and out.endswith("…")


def test_caption_stays_readable_with_messy_sources():
    items = [_item(1, source="Wang; Bo; Tang; Sijin; Tan; Pingjuan; Mi; Baohong")]
    caption = build_roundup_caption(items)
    assert "Bo;" not in caption


# =============================================
# Zamanlayıcı orkestrasyonu
#
# Bu testler ilk sürümde YOKTU ve `self.image_gen` (doğrusu `img_gen`) yazım
# hatası ancak üretimde çalıştırıldığında ortaya çıktı. Modül fonksiyonlarını
# test edip orkestrasyonu test etmemek, tam da bu tür hataları kaçırıyor.
# =============================================

@pytest.fixture
def scheduler(tmp_db, monkeypatch, tmp_path):
    from src.scheduler import Scheduler

    s = Scheduler.__new__(Scheduler)          # __init__ ağır bağımlılıkları kurar
    s.db = tmp_db
    uretilen = []

    class _FakeImageGen:
        def generate_carousel_images(self, slides, size=None):
            uretilen.append(slides)
            return [str(tmp_path / f"slayt_{i}.png") for i in range(len(slides))]

        def last_background_source(self):
            return "og_image"

    s.img_gen = _FakeImageGen()
    s._uretilen_slaytlar = uretilen
    monkeypatch.setattr("config.ROUNDUP_ENABLED", True, raising=False)
    monkeypatch.setattr("src.scheduler.ROUNDUP_ENABLED", True, raising=False)
    return s


def test_scheduler_creates_roundup_end_to_end(scheduler, tmp_db, monkeypatch):
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 4)
    for i in range(6):
        _seed_draft(tmp_db, i)

    stats = scheduler.create_daily_roundup()

    assert stats["created"] is True
    assert stats["item_count"] == 6
    # Kapak + 6 haber
    assert len(scheduler._uretilen_slaytlar[0]) == 7

    with tmp_db._get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM processed_content WHERE carousel_paths IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["content_type"] == "post"
    assert row["status"] == "draft"          # otomatik yayınlanmaz
    assert "Günün" in row["summary_text"]


def test_scheduler_marks_sources_used(scheduler, tmp_db, monkeypatch):
    """Aynı haber hem derlemede hem tekil gönderi olarak çıkmamalı."""
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 4)
    for i in range(5):
        _seed_draft(tmp_db, i)

    scheduler.create_daily_roundup()

    with tmp_db._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM processed_content WHERE used_in_roundup = 1"
        ).fetchone()[0]
    assert n == 5


def test_scheduler_skips_when_not_enough_drafts(scheduler, tmp_db, monkeypatch):
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 4)
    _seed_draft(tmp_db, 1)

    stats = scheduler.create_daily_roundup()

    assert stats["created"] is False
    assert scheduler._uretilen_slaytlar == []


def test_scheduler_respects_disabled_flag(scheduler, tmp_db, monkeypatch):
    monkeypatch.setattr("src.scheduler.ROUNDUP_ENABLED", False)
    for i in range(6):
        _seed_draft(tmp_db, i)

    assert scheduler.create_daily_roundup()["created"] is False


def test_scheduler_aborts_if_too_few_slides(scheduler, tmp_db, monkeypatch):
    """Görsel üretimi patlarsa yarım bir carousel kaydedilmemeli."""
    monkeypatch.setattr("src.roundup.ROUNDUP_MIN_ITEMS", 4)
    for i in range(5):
        _seed_draft(tmp_db, i)
    scheduler.img_gen.generate_carousel_images = lambda slides, size=None: ["tek.png"]

    stats = scheduler.create_daily_roundup()

    assert stats["created"] is False
    with tmp_db._get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM processed_content WHERE used_in_roundup = 1"
        ).fetchone()[0]
    assert n == 0  # taslaklar boşa harcanmamalı


def test_used_in_roundup_does_not_pollute_ground_truth(tmp_db):
    """
    Derlemeye giren taslak 'rejected' İŞARETLENMEMELİ: kullanıcı onu
    reddetmedi ve puanlama ölçümünün yer gerçeği bozulurdu.
    """
    cid = _seed_draft(tmp_db, 1)
    tmp_db.mark_used_in_roundup([cid])
    with tmp_db._get_connection() as conn:
        row = conn.execute(
            "SELECT status, used_in_roundup FROM processed_content WHERE id=?", (cid,)
        ).fetchone()
    assert row["status"] == "draft"
    assert row["used_in_roundup"] == 1
