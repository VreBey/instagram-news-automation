"""Reels tutarlılığı ve dil denetimi.

Kullanıcı bildirimi (5 Ağustos 2026): "reelslerde tutarsızlık var,
paylaşmadığımız bir bilgi reelste kullanılıyor, ayrıca reelsin içinde
İngilizce içerikler var."

İkisi de aynı kökten geliyordu:

  create_reels_content -> db.get_unused_news()      # HİÇ paylaşılmamış haber
  _ai_generate_reels   -> news_titles = [n["title"]] # HAM İngilizce başlık
  video_generator      -> bunları slayta basıyordu

Yani reels, hesapta olmayan haberleri İngilizce başlıklarla anlatıyordu.
Çözüm: reels artık GERÇEKTEN YAYINLANMIŞ içeriklerin Türkçe özetlerinden
kuruluyor ve video tarafında da dil kapısı var.
"""

import pytest

from src.content_language import is_probably_turkish


# =============================================
# Dil tespiti — izin verici olmalı
# =============================================

@pytest.mark.parametrize("metin", [
    "Final Fantasy XIV Online Switch 2'ye geliyor",
    "Elden Ring, Switch 2'ye 28 Ağustos'ta geliyor!",
    "Super Mario Sunshine Switch 2ye geliyor",
    "Path of Exile 2 çökmeleri sürücü güncellemesiyle çözüldü",
    "Escape from Tarkov, ilk sezon döngüsünü başlattı",
    "Fire Emblem serisinde bir ilk: silah eklendi",
])
def test_turkish_is_never_blocked(metin):
    """
    Kritik: daha önce aceleyle yazılmış katı bir tespitçi düpedüz Türkçe
    cümleleri İngilizce sanmıştı (Türkçe harf içermiyor diye). Kural bu
    yüzden tersine kuruldu — şüphede kalırsa Türkçe sayar.
    """
    assert is_probably_turkish(metin) is True


@pytest.mark.parametrize("metin", [
    "Final Fantasy XIV Launches on Nintendo Switch 2 August 4, First Time on Nintendo",
    "Call of Duty: Modern Warfare 4 Beta Kicks Off August 21 After COD NEXT Event",
    "Girls' Frontline: Fire Control Shuts Down August 26, Nine Months After Launch",
    "Elden Ring Comes to Nintendo Switch 2 on August 28 with Full Game Content",
])
def test_english_is_detected(metin):
    assert is_probably_turkish(metin) is False


@pytest.mark.parametrize("metin", [
    "Europe's AI labeling and transparency rules are now in effect",
    "China's Alibaba takes another swipe at America's AI supremacy",
    "Fire Emblem: Fortune's Weave adds surprise new weapon: guns!",
    "Path of Exile 2's Full 1.0 Launch Targeted for Late 2026",
])
def test_english_possessive_is_not_mistaken_for_turkish(metin):
    """
    İngilizce iyelik eki `'s`, Türkçe kesme-işareti desenine uyuyordu
    ("Europe's" → 's). Canlı veride 5 başlığın 3'ü bu yüzden Türkçe sanıldı.
    Türkçede kesmeden sonra 'ye/'ta/'nin gibi ekler gelir, tek harflik `s`
    gelmez.
    """
    assert is_probably_turkish(metin) is False


def test_turkish_apostrophe_suffixes_still_pass():
    """Ayrım Türkçe ekleri bozmamalı."""
    for metin in ["Switch 2'ye 28 Ağustos'ta geliyor",
                  "PS5'in yeni sürümü çıktı",
                  "Xbox'tan yeni duyuru geldi"]:
        assert is_probably_turkish(metin) is True


@pytest.mark.parametrize("metin", [None, "", "   ", "GTA 6", "PS5"])
def test_short_or_empty_is_permitted(metin):
    """Karar verecek kadar bilgi yok; asıl korumayı safe_display_title yapıyor."""
    assert is_probably_turkish(metin) is True


def test_turkish_chars_short_circuit():
    """Türkçe harf varsa İngilizce kelime sayısı hiç önemli değil."""
    assert is_probably_turkish("The game is coming with full content çünkü") is True


# =============================================
# Reels yalnızca YAYINLANMIŞ içerikten kurulur
# =============================================

def _yayinla(db, baslik_tr, kategori="gaming", tip="post"):
    news_id = db.add_news(title=f"English source {baslik_tr}",
                          url=f"https://example.com/{baslik_tr}", category=kategori)
    db.mark_news_processed(news_id, relevance_score=0.8)
    cid = db.add_content(news_id=news_id, content_type=tip, caption="c",
                         summary_text=baslik_tr)
    db.add_publish_record(content_id=cid, post_type=tip, status="success",
                          instagram_media_id=f"m{news_id}")
    return news_id


def test_only_published_content_is_used(tmp_db):
    """Asıl regresyon: paylaşılmamış haber reelste kullanılmamalı."""
    _yayinla(tmp_db, "Elden Ring Switch 2'ye geliyor")
    _yayinla(tmp_db, "Path of Exile 2 çökmeleri çözüldü")
    # Yayınlanmamış bir haber — reels'e GİRMEMELİ
    nid = tmp_db.add_news(title="Never Published English Title",
                          url="https://example.com/gizli", category="gaming")
    tmp_db.mark_news_processed(nid, relevance_score=0.99)
    tmp_db.add_content(news_id=nid, content_type="post", caption="c",
                       summary_text="Hiç paylaşılmayan içerik")

    secilen = tmp_db.get_published_for_reels(category="gaming", limit=10)

    basliklar = [s["title"] for s in secilen]
    assert "Hiç paylaşılmayan içerik" not in basliklar
    assert len(secilen) == 2


def test_title_field_carries_turkish_summary(tmp_db):
    """
    `title` alanı Türkçe özetle dolduruluyor: çağıran kod (senaryo, slayt,
    caption) zaten `title` bekliyor, tek yerde düzeltmek yetiyor.
    """
    _yayinla(tmp_db, "Elden Ring Switch 2'ye geliyor")
    secilen = tmp_db.get_published_for_reels(limit=5)

    assert secilen[0]["title"] == "Elden Ring Switch 2'ye geliyor"
    assert "English source" not in secilen[0]["title"]


def test_same_news_not_repeated_for_post_and_story(tmp_db):
    """Aynı haber hem post hem story yayınlandıysa reels'te iki kez geçmemeli."""
    news_id = tmp_db.add_news(title="English", url="https://example.com/x",
                              category="gaming")
    tmp_db.mark_news_processed(news_id, relevance_score=0.8)
    for tip in ("post", "story"):
        cid = tmp_db.add_content(news_id=news_id, content_type=tip, caption="c",
                                 summary_text="Türkçe özet")
        tmp_db.add_publish_record(content_id=cid, post_type=tip, status="success",
                                  instagram_media_id=f"m-{tip}")

    assert len(tmp_db.get_published_for_reels(limit=10)) == 1


def test_failed_publishes_are_excluded(tmp_db):
    news_id = tmp_db.add_news(title="English", url="https://example.com/f",
                              category="gaming")
    tmp_db.mark_news_processed(news_id, relevance_score=0.8)
    cid = tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                             summary_text="Başarısız yayın")
    tmp_db.add_publish_record(content_id=cid, post_type="post", status="failed")

    assert tmp_db.get_published_for_reels(limit=10) == []


def test_reels_skipped_when_not_enough_published(tmp_db, monkeypatch):
    """
    Reels artık paylaşılanların özeti; yeterli yayın yoksa üretilmemeli.
    Uydurma içerikle doldurmak tutarsızlığı geri getirirdi.
    """
    from src.content_processor import ContentProcessor

    _yayinla(tmp_db, "Tek içerik")
    p = ContentProcessor(db=tmp_db)
    p.client = object()

    assert p.create_reels_content(category="gaming") is None


# =============================================
# Video slaytlarında dil kapısı
# =============================================

def test_english_slides_are_skipped(tmp_db, monkeypatch):
    """Altıncı ve son kapı: video slayt yazıları da denetimden geçmeli."""
    from src import video_generator as vg

    gen = vg.VideoGenerator(db=tmp_db)
    uretilen = []
    monkeypatch.setattr(gen, "_create_intro_slide", lambda t, c: "/tmp/intro.png")
    monkeypatch.setattr(gen, "_create_outro_slide", lambda t, c: "/tmp/outro.png")
    monkeypatch.setattr(
        gen, "_create_news_reel_slide",
        lambda **kw: uretilen.append(kw["segment_text"]) or f"/tmp/{len(uretilen)}.png",
    )

    gen._create_slide_images({
        "intro": "Günün haberleri",
        "segments": [
            "Elden Ring Switch 2'ye geliyor",
            "Girls' Frontline: Fire Control Shuts Down August 26, Nine Months After Launch",
        ],
        "news_titles": ["Elden Ring Switch 2'ye geliyor", "Girls' Frontline Shuts Down"],
        "outro": "Takipte kal",
    }, "gaming")

    assert len(uretilen) == 1
    assert "Elden Ring" in uretilen[0]
