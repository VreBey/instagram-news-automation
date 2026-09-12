"""Arka plan kaynağının kaydedilmesi ve raporlanması.

Kullanıcı "gerçek görüntü kullanmıyor" diye bildirdi ama bunu ölçecek hiçbir
veri yoktu: görsel zinciri (manuel → haberin görseli → og:image → oyun kapağı
→ stok → gradyan) seçtiği kaynağı kaydetmiyor, yalnızca görseli döndürüyordu.

Sonuç: hem şikayeti doğrulamak hem de yapılan düzeltmelerin işe yarayıp
yaramadığını görmek anekdota kalıyordu. Artık kaynak kaydediliyor ve
`/huni` raporunda "gerçek görsel oranı" olarak görünüyor.
"""

import itertools

_sayac = itertools.count()


def _icerik(db, summary="Elden Ring Switch 2'ye geliyor"):
    # Her cagriya ayri URL: add_news ayni URL'de mukerrer sayip None doner.
    news_id = db.add_news(title="English Source", category="gaming",
                          url=f"https://example.com/x{next(_sayac)}")
    return db.add_content(news_id=news_id, content_type="post", caption="c",
                          summary_text=summary)


# =============================================
# Kayıt
# =============================================

def test_source_is_persisted(tmp_db):
    cid = _icerik(tmp_db)
    tmp_db.set_background_source(cid, "og_image")

    with tmp_db._get_connection() as conn:
        kaynak = conn.execute(
            "SELECT background_source FROM processed_content WHERE id = ?", (cid,)
        ).fetchone()["background_source"]
    assert kaynak == "og_image"


def test_generator_records_gradient_when_no_photo(tmp_db, monkeypatch):
    """
    Hiçbir görsel kaynağı bulunamazsa gradyana düşülür ve bu KAYDEDİLİR —
    "gerçek görüntü kullanmıyor" şikayetinin ölçülebilir hali budur.
    """
    from src.image_generator import ImageGenerator

    gen = ImageGenerator(db=tmp_db)
    cid = _icerik(tmp_db)
    # Tüm foto kaynaklarını kapat.
    monkeypatch.setattr("src.image_generator.fetch_article_photo", lambda *a, **k: None)
    monkeypatch.setattr("src.image_generator.fetch_article_photo_from_page", lambda *a, **k: None)
    monkeypatch.setattr("src.image_generator.fetch_game_cover_art", lambda *a, **k: None)
    monkeypatch.setattr("src.image_generator.fetch_stock_photo", lambda *a, **k: None)

    gen.generate_post_image(content_id=cid)

    with tmp_db._get_connection() as conn:
        kaynak = conn.execute(
            "SELECT background_source FROM processed_content WHERE id = ?", (cid,)
        ).fetchone()["background_source"]
    assert kaynak == "gradient"


def test_generator_records_article_image(tmp_db, monkeypatch, tmp_path):
    from PIL import Image
    from src.image_generator import ImageGenerator

    foto = tmp_path / "foto.jpg"
    Image.new("RGB", (1200, 800), (40, 60, 90)).save(foto)

    gen = ImageGenerator(db=tmp_db)
    news_id = tmp_db.add_news(title="English", url="https://example.com/y",
                              category="gaming", image_url="https://a.com/foto.jpg")
    cid = tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                             summary_text="Türkçe özet burada")
    monkeypatch.setattr("src.image_generator.fetch_article_photo", lambda *a, **k: str(foto))

    gen.generate_post_image(content_id=cid)

    with tmp_db._get_connection() as conn:
        kaynak = conn.execute(
            "SELECT background_source FROM processed_content WHERE id = ?", (cid,)
        ).fetchone()["background_source"]
    assert kaynak == "article_image"


# =============================================
# Carousel: tek içerik, çok slayt
# =============================================

def test_carousel_records_dominant_source(tmp_db, monkeypatch):
    """
    Carousel'de her slayt ayrı kaynaktan gelebilir ama içeriğe tek kaynak
    yazılır. Çoğunluk seçilmeli: aksi halde "1 slayt gerçek, 4 slayt
    gradyan" olan bir gönderi rapora "gerçek görsel" diye girerdi.
    """
    from src.image_generator import ImageGenerator

    gen = ImageGenerator(db=tmp_db)
    gen._slide_background_sources = ["article_image", "gradient", "gradient",
                                     "gradient", "stock_photo"]
    assert gen.last_background_source() == "gradient"


def test_dominant_source_breaks_ties_toward_better_source(tmp_db):
    from src.image_generator import ImageGenerator

    gen = ImageGenerator(db=tmp_db)
    gen._slide_background_sources = ["gradient", "game_cover"]
    assert gen.last_background_source() == "game_cover"


def test_carousel_slides_try_page_image(tmp_db, monkeypatch):
    """
    Carousel slaytlarına news_url hiç geçilmiyordu: og:image basamağı
    atlanıyor, beslemede görsel yoksa doğrudan stok fotoğrafa düşülüyordu.
    """
    from src.image_generator import ImageGenerator

    istenen = []
    monkeypatch.setattr("src.image_generator.fetch_article_photo_from_page",
                        lambda url, *a, **k: istenen.append(url))

    gen = ImageGenerator(db=tmp_db)
    news_id = tmp_db.add_news(title="En iyi 3 oyun", url="https://haber.com/liste",
                              category="gaming")
    cid = tmp_db.add_content(
        news_id=news_id, content_type="post", caption="c",
        summary_text="Listede 3 oyun var",
        list_items={"cover": "En iyi 3 oyun",
                    "items": [{"name": "Oyun A", "detail": "detay"},
                              {"name": "Oyun B", "detail": "detay"}]},
    )
    icerik = tmp_db.get_content_by_id(cid)
    gen._generate_post_carousel(cid, icerik)

    assert istenen and all(u == "https://haber.com/liste" for u in istenen)


def test_roundup_slides_carry_news_url(tmp_db):
    """Derleme slaytları da og:image basamağına erişebilmeli."""
    from src.roundup import build_roundup_slides

    slaytlar = build_roundup_slides([
        {"summary_text": "Türkçe özet", "category": "gaming",
         "source_name": "IGN", "image_url": None,
         "news_url": "https://haber.com/a"},
    ])
    assert slaytlar[1]["news_url"] == "https://haber.com/a"


# =============================================
# Raporlama
# =============================================

def test_stats_group_by_source(tmp_db):
    for kaynak in ("og_image", "og_image", "stock_photo", "gradient"):
        cid = _icerik(tmp_db)
        tmp_db.update_content_media(cid, "/tmp/x.png")
        tmp_db.set_background_source(cid, kaynak)

    stats = tmp_db.get_background_source_stats(days=7)
    assert stats["og_image"] == 2
    assert stats["stock_photo"] == 1
    assert stats["gradient"] == 1


def test_stats_ignore_content_without_media(tmp_db):
    """Medyası üretilmemiş içerik istatistiğe girmemeli."""
    cid = _icerik(tmp_db)
    tmp_db.set_background_source(cid, "og_image")
    assert tmp_db.get_background_source_stats(days=7) == {}


def test_funnel_reports_real_image_ratio(tmp_db):
    """
    Rapor "gerçek görsel oranı" vermeli: stok fotoğraf ve gradyan yedektir,
    diğerleri habere ait gerçek görsel.
    """
    from src import diagnostics

    for kaynak in ("og_image", "article_image", "game_cover", "stock_photo"):
        cid = _icerik(tmp_db)
        tmp_db.update_content_media(cid, "/tmp/x.png")
        tmp_db.set_background_source(cid, kaynak)

    rapor = diagnostics.funnel_report(tmp_db)

    assert "Görsel kaynağı" in rapor
    assert "gerçek görsel oranı: %75" in rapor  # 3 gercek / 4 olculen


def test_funnel_omits_section_when_no_data(tmp_db):
    from src import diagnostics
    assert "Görsel kaynağı" not in diagnostics.funnel_report(tmp_db)
