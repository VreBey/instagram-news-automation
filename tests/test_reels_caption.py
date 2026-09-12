"""Reels caption'ı kelime ortasından kesmemeli ve tazelik iddia etmemeli.

Canlı caption'lardan (8 Ağustos 2026) alınan gerçek satırlar:
    "1. Epic Games Launcher V2, Unreal Engine'ı bırakıp hız odakl..."
    "5. Prime Video'da bu hafta sonu; yeni bir köpekbalığı geril..."
    "3. Escape from Tarkov, 1.1.0 "Kord Breach" yamasıyla ilk res..."

Sebep: `title[:57] + "..."` — 60 karakterlik sert bir kesme. Instagram 2200
karaktere izin veriyor, 5 maddelik liste bunun çok altında; sınır gereksiz
yere dardı.

Ayrıca caption "Günün ... Haberleri!" diyordu. Reels havuzu SON 7 GÜNDE
yayınlanmış gönderilerden derleniyor, tek bir güne ait değil — giriş
slaytındaki aynı iddia da bu yüzden kaldırıldı.
"""

import pytest

from src.content_processor import ContentProcessor


@pytest.fixture
def p(tmp_db):
    return ContentProcessor(db=tmp_db)


def _haberler(basliklar, kategori="gaming"):
    return [{"title": b, "category": kategori, "source_name": "IGN"}
            for b in basliklar]


def test_short_titles_are_untouched(p):
    caption = p._build_reels_caption(_haberler(["Kısa bir başlık"]))
    assert "1. Kısa bir başlık" in caption
    assert "…" not in caption


def test_long_title_is_cut_at_a_word_boundary(p):
    uzun = ("Epic Games Launcher V2, Unreal Engine altyapısını bırakarak çok "
            "daha hız odaklı bir tasarımla baştan aşağı yenileniyor ve "
            "kullanıcılar bunu çok sevecek gibi görünüyor")
    caption = p._build_reels_caption(_haberler([uzun]))

    satir = [s for s in caption.splitlines() if s.startswith("1. ")][0]
    govde = satir[3:].rstrip("…")
    assert govde.endswith(tuple("abcçdefgğhıijklmnoöprsştuüvyz0123456789")), \
        f"kelime ortasından kesilmiş: {satir!r}"
    assert govde in uzun, "kesilen metin kaynakta yok"


def test_the_old_60_char_cut_is_gone(p):
    """Gerçek bir canlı örnek: 60'ta kesilirse 'hız odakl...' olurdu."""
    uzun = ("Epic Games Launcher V2, Unreal Engine'ı bırakıp hız odaklı "
            "bir tasarıma geçiyor")
    caption = p._build_reels_caption(_haberler([uzun]))
    assert "hız odakl…" not in caption
    assert uzun in caption, "kısa başlık gereksiz yere kesildi"


def test_caption_makes_no_freshness_claim(p):
    caption = p._build_reels_caption(_haberler(["Bir haber"]))
    assert "Günün" not in caption
    assert "Öne Çıkan" in caption


def test_category_drives_the_heading(p):
    oyun = p._build_reels_caption(_haberler(["a"], "gaming"))
    yapay = p._build_reels_caption(_haberler(["a"], "ai"))
    assert "Gaming" in oyun and "🎮" in oyun
    assert "AI" in yapay and "🤖" in yapay


@pytest.mark.parametrize("metin,sinir", [
    ("", 20),
    ("tek", 20),
    ("a" * 200, 140),          # boşluksuz: sert kesmeye düşmeli
])
def test_truncation_never_crashes(metin, sinir):
    sonuc = ContentProcessor._kisalt(metin, sinir)
    assert len(sonuc) <= sinir + 1  # + kısaltma işareti


def test_caption_lists_only_the_news_the_video_covers(tmp_db, monkeypatch):
    """
    Model her zaman istenen sayıda segment üretmiyor.

    Canlı örnek (8 Ağustos 2026): 5 haber verildi, 4 segment döndü — video
    4 haber anlatırken caption 5 madde listeliyordu. İzleyici videoda
    olmayan bir haberi caption'da görüyordu.
    """
    p = ContentProcessor(db=tmp_db)
    p.client = object()
    # 4 segment: 1, 2, 4, 5 — ÜÇÜNCÜ haber atlanıyor.
    monkeypatch.setattr(p, "_generate_with_retry", lambda *a, **k: (
        "INTRO: Selam\n"
        "SEGMENT 1: Bir\nSEGMENT 2: İki\nSEGMENT 4: Dört\nSEGMENT 5: Beş\n"
        "OUTRO: Bay"
    ))
    haberler = _haberler([f"Haber {i}" for i in range(1, 6)])
    for i, h in enumerate(haberler):
        h.update(id=i, image_url=None, news_url=f"https://h/{i}",
                 news_title=f"English {i}")

    sonuc = p._ai_generate_reels(haberler)

    assert len(sonuc["segments"]) == 4
    assert "Haber 3" not in sonuc["caption"], "anlatılmayan haber caption'da"
    for beklenen in ("Haber 1", "Haber 2", "Haber 4", "Haber 5"):
        assert beklenen in sonuc["caption"]


def test_chain_lists_keep_full_length(tmp_db, monkeypatch):
    """
    Zincir listeleri KISALTILMAMALI: video üretimi onlara `segment_indices`
    ile, yani ORİJİNAL konuma göre erişiyor. Kısaltmak her segmenti başka
    haberin fotoğrafıyla eşleştirirdi.
    """
    p = ContentProcessor(db=tmp_db)
    p.client = object()
    monkeypatch.setattr(p, "_generate_with_retry", lambda *a, **k: (
        "SEGMENT 1: Bir\nSEGMENT 5: Beş"
    ))
    haberler = _haberler([f"Haber {i}" for i in range(1, 6)])
    for i, h in enumerate(haberler):
        h.update(id=i, image_url=f"https://cdn/{i}.jpg",
                 news_url=f"https://h/{i}", news_title=f"English {i}")

    sonuc = p._ai_generate_reels(haberler)

    assert sonuc["segment_indices"] == [0, 4]
    assert len(sonuc["news_urls"]) == 5
    assert sonuc["news_urls"][4] == "https://h/4"


def test_truncation_marker_is_a_single_character():
    """Üç nokta yerine tek karakterli '…' — satır sonu daha temiz görünüyor."""
    sonuc = ContentProcessor._kisalt("kelime " * 40, 50)
    assert sonuc.endswith("…")
    assert not sonuc.endswith("....")
