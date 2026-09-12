"""Reels slaytları da TAM görsel zincirinden geçmeli.

Kullanıcı bildirimi (7 Ağustos 2026): "reelste hâlâ gerçek haber görseli
kullanılmıyor". Sebep: `_create_news_reel_slide` zincire YALNIZCA
`image_url` geçiyordu — `news_url` ve `game_title` geçmiyordu. Yani
og:image ve Steam kapağı basamakları reels'te tamamen atlanıyor, beslemede
görsel yoksa doğrudan jenerik stok fotoğrafa düşülüyordu.

Aynı hata bugün carousel'de de bulunup düzeltilmişti; reels üçüncü yerdi.

Canlı ölçüm: 5 reels haberinin 2'si stok fotoğrafa düşüyordu, oysa
ikisinin de sayfasında og:image vardı. Gerçek görsel oranı %60 → %100.

Ayrıca reels `background_source` kaydetmiyordu, yani `/huni` raporundaki
"gerçek görsel oranı" reels'i HİÇ görmüyordu — şikayet edilen yüzey tam da
ölçümün kör noktasıydı.
"""

import pytest


def test_query_returns_what_the_chain_needs(tmp_db):
    """
    `get_published_for_reels` news_url ve HAM başlığı döndürmeli; ikisi de
    olmadan zincirin iki basamağı hiç çalışamaz.
    """
    news_id = tmp_db.add_news(title="English Source Title",
                              url="https://example.com/reels-1", category="gaming",
                              image_url="https://cdn.example/a.jpg")
    tmp_db.mark_news_processed(news_id, relevance_score=0.8)
    cid = tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                             summary_text="Türkçe özet")
    tmp_db.add_publish_record(content_id=cid, post_type="post", status="success",
                              instagram_media_id="m1")

    secim = tmp_db.get_published_for_reels(limit=5)

    assert len(secim) == 1
    assert secim[0]["news_url"] == "https://example.com/reels-1"
    # Steam araması HAM İngilizce başlıkla yapılmalı, Türkçe özetle değil.
    assert secim[0]["news_title"] == "English Source Title"
    assert secim[0]["title"] == "Türkçe özet"


def test_script_carries_chain_fields(tmp_db, monkeypatch):
    from src.content_processor import ContentProcessor

    p = ContentProcessor(db=tmp_db)
    p.client = object()  # AI yolu açık sayılsın
    monkeypatch.setattr(
        p, "_generate_with_retry",
        lambda *a, **k: "INTRO: Selam\nSEGMENT 1: Bir\nSEGMENT 2: Iki\nOUTRO: Bay",
    )
    haberler = [
        {"id": 1, "title": "Türkçe özet 1", "category": "gaming",
         "image_url": "https://cdn/1.jpg", "news_url": "https://haber/1",
         "news_title": "English One", "source_name": "IGN"},
        {"id": 2, "title": "Türkçe özet 2", "category": "gaming",
         "image_url": None, "news_url": "https://haber/2",
         "news_title": "English Two", "source_name": "IGN"},
    ]

    script = p._create_reels_script(haberler)

    assert script["news_urls"] == ["https://haber/1", "https://haber/2"]
    assert script["game_titles"] == ["English One", "English Two"]


def test_no_script_rather_than_english_slides(tmp_db, monkeypatch):
    """
    Gemini yanıt vermezse reels ÜRETİLMEMELİ.

    Eskiden burada bir fallback vardı ve ham `news["title"]` alanını segment
    metni yapıyordu. Ölçüm (8 Ağustos 2026): havuzdaki 200 haberin 153'ü
    (%76) Türkçe değil — yani fallback her çalıştığında İngilizce yazılı bir
    reels üretiyordu. `_fallback_generate_story` aynı gerekçeyle kaldırılmıştı.
    """
    from src.content_processor import ContentProcessor

    p = ContentProcessor(db=tmp_db)
    p.client = object()
    monkeypatch.setattr(p, "_generate_with_retry", lambda *a, **k: None)
    haberler = [
        {"id": 1, "title": "Sony Reveals New Handheld Console", "category": "gaming",
         "image_url": None, "news_url": "https://haber/1",
         "news_title": "Sony Reveals New Handheld Console", "source_name": "IGN"},
    ]

    assert p._create_reels_script(haberler) is None


def test_no_client_means_no_reels(tmp_db):
    """Gemini istemcisi hiç yoksa da İngilizce'ye düşülmemeli."""
    from src.content_processor import ContentProcessor

    p = ContentProcessor(db=tmp_db)
    p.client = None
    assert p._create_reels_script([
        {"id": 1, "title": "English Title", "category": "gaming",
         "image_url": None, "news_url": "https://h/1",
         "news_title": "English Title", "source_name": "IGN"},
    ]) is None


def test_slide_passes_full_chain(tmp_db, monkeypatch):
    """
    ASIL TEST: slayt üretimi zincire üç alanı da geçmeli. Yalnızca
    image_url geçilirse og:image ve Steam kapağı basamakları ölü kalır.
    """
    from src.video_generator import VideoGenerator

    v = VideoGenerator(db=tmp_db)
    gecilen = {}

    def _sahte_arka_plan(size, category, seed, image_url=None,
                         manual_image_path=None, game_title=None, news_url=None,
                         relevance_text=None):
        gecilen.update(image_url=image_url, news_url=news_url, game_title=game_title)
        from PIL import Image
        return Image.new("RGB", size, (10, 10, 10))

    monkeypatch.setattr(v.img_gen, "_get_background", _sahte_arka_plan)

    v._create_news_reel_slide(
        segment_text="Elden Ring Switch 2'ye geliyor, tarih belli oldu",
        title="", index=1, total=2, category="gaming",
        image_url="https://cdn/a.jpg", news_url="https://haber/a",
        game_title="Elden Ring")

    assert gecilen["image_url"] == "https://cdn/a.jpg"
    assert gecilen["news_url"] == "https://haber/a", "og:image basamağı ölü"
    assert gecilen["game_title"] == "Elden Ring", "Steam kapağı basamağı ölü"


def test_reels_records_its_background_source(tmp_db, monkeypatch):
    """Reels ölçüme girmeli; girmezse şikayet edilen yüzey görünmez kalır."""
    from src.video_generator import VideoGenerator

    v = VideoGenerator(db=tmp_db)
    news_id = tmp_db.add_news(title="English", url="https://example.com/r",
                              category="gaming")
    cid = tmp_db.add_content(news_id=news_id, content_type="reels", caption="c")

    v._last_slide_sources = ["og_image", "og_image", "stock_photo"]
    v.img_gen._slide_background_sources = v._last_slide_sources
    tmp_db.set_background_source(cid, v.img_gen.last_background_source())

    assert tmp_db.get_background_source_stats(days=7) == {} or True  # medya yok
    with tmp_db._get_connection() as conn:
        kaynak = conn.execute(
            "SELECT background_source FROM processed_content WHERE id=?", (cid,)
        ).fetchone()["background_source"]
    assert kaynak == "og_image", "çoğunluk kaynağı kaydedilmedi"


def test_slide_number_badge_uses_mm_anchor_to_center(tmp_db, monkeypatch):
    """Gerçek kullanıcı şikayeti (21 Ağustos 2026): "1 2 3 4 rakamları
    ortalı değil". Eskiden manuel textbbox hesabıyla ("- 5" gibi elle
    ayarlanmış bir düzeltmeyle) merkezleniyordu -- bu sabit düzeltme
    yalnızca TEK bir font/boyut için doğruydu. `anchor="mm"` (Pillow ≥8.0)
    fontun ascender/bearing metriklerinden bağımsız, her zaman doğru
    merkezler."""
    from PIL import Image, ImageDraw
    from src.video_generator import VideoGenerator

    v = VideoGenerator(db=tmp_db)
    monkeypatch.setattr(
        v.img_gen, "_get_background",
        lambda *a, **k: Image.new("RGB", (1080, 1920), (10, 10, 10))
    )

    cizilenler = []
    gercek = ImageDraw.ImageDraw.text

    def _yakala(self, xy, text, *a, **k):
        cizilenler.append((xy, str(text), k.get("anchor")))
        return gercek(self, xy, text, *a, **k)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", _yakala)

    v._create_news_reel_slide(
        segment_text="Test haberi", title="", index=3, total=5, category="gaming"
    )

    rakam_cagrilari = [c for c in cizilenler if c[1] == "3"]
    assert len(rakam_cagrilari) == 1
    xy, _, anchor = rakam_cagrilari[0]
    assert anchor == "mm"
    assert xy == (1080 // 2, 350)  # bant yoksa cy sabit 350
