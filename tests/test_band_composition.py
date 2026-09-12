"""Yatay görsel dikey kareye ZORLANMAZ — bant düzeni.

Ölçüm (8 Ağustos 2026): reels'te (9:16) kaynak görselin ortalama **%66'sı**
kırpılıp atılıyordu; 60 görselin 59'u yarısından fazlasını kaybediyordu.
Haber görselleri ortalama 16:9 yatay.

Görünen sonuç: haber görsellerinin çoğu üzerine başlık basılmış "thumbnail"
tipi olduğu için kırpma o yazıyı KELİME ORTASINDAN kesiyordu. Yayınlanan bir
slaytta arka planda "IVALR" ve "OSOFT & EPIC vs ST" yazıyordu, üstünde de
Türkçe metnimiz — iki yazı yarışıyordu, biri İngilizce ve sakat.

Bulanıklaştırma denendi, yetmedi (yarıçap 14'te bile dev yazı okunuyordu ve
görsel tanınmaz hâle geliyordu). Otomatik "yazılı görsel" tespiti de denendi;
40 gerçek görselde hiçbir eşik ayırt edemedi. Çözüm kırpmayı azaltmak oldu.
"""

from PIL import Image

import pytest

from src.image_generator import ImageGenerator


@pytest.fixture
def gen(tmp_db):
    return ImageGenerator(db=tmp_db)


def _foto(tmp_path, w, h, ad="f.jpg"):
    yol = tmp_path / ad
    Image.new("RGB", (w, h), (180, 90, 40)).save(yol)
    return str(yol)


REELS = (1080, 1920)
POST = (1080, 1350)


def test_landscape_into_portrait_uses_the_band(gen, tmp_path):
    """16:9 kaynak, 9:16 hedef: kırpma %66 — bant devreye girmeli."""
    gen._compose_photo_background(REELS, _foto(tmp_path, 1600, 900), "gaming")
    assert gen._last_band_rect is not None, "bant düzeni kullanılmadı"


def test_matching_aspect_keeps_cover_fit(gen, tmp_path):
    """Oran zaten yakınsa tam kaplama daha iyi görünür; bant kullanılmamalı."""
    gen._compose_photo_background(REELS, _foto(tmp_path, 1080, 1800), "gaming")
    assert gen._last_band_rect is None, "gereksiz yere bant kullanıldı"


def test_post_never_uses_the_band(gen, tmp_path):
    """
    Gönderi (4:5) yerleşimi bandı BİLMİYOR — metni sabit koordinatlara
    çiziyor. Bant orada açılırsa metin görselin üzerine biner. Ağır kırpılan
    kaynakta bile cover-fit kalmalı.
    """
    gen._compose_photo_background(POST, _foto(tmp_path, 1600, 900), "ai")
    assert gen._last_band_rect is None, "bant gönderiye sızdı"


def test_band_shows_the_whole_image(gen, tmp_path):
    """
    Bandın oranı kaynağın oranıyla aynı olmalı — kırpma olmadığının kanıtı.
    """
    gen._compose_photo_background(REELS, _foto(tmp_path, 1600, 900), "gaming")
    ust, alt = gen._last_band_rect
    bant_yuksekligi = alt - ust
    beklenen = round(1080 / (1600 / 900))
    assert abs(bant_yuksekligi - beklenen) <= 2, \
        f"bant oranı bozuk: {bant_yuksekligi} != {beklenen}"


def test_band_leaves_room_above_for_the_slide_number(gen, tmp_path):
    """
    Slayt numarası bandın ÜSTÜNDEKİ boşluğa konuyor. İlk denemede bant
    yukarıdaydı ve balon görselin üzerine biniyordu.
    """
    gen._compose_photo_background(REELS, _foto(tmp_path, 1600, 900), "gaming")
    ust, _ = gen._last_band_rect
    assert ust > 150, f"bandın üstünde numara için yer yok ({ust}px)"


def test_band_leaves_room_below_for_text(gen, tmp_path):
    gen._compose_photo_background(REELS, _foto(tmp_path, 1600, 900), "gaming")
    _, alt = gen._last_band_rect
    assert REELS[1] - alt > 400, "bandın altında metin için yer yok"


def test_very_wide_panorama_still_fits(gen, tmp_path):
    """Aşırı geniş kaynakta bant çok inceleşir ama taşmamalı."""
    img = gen._compose_photo_background(REELS, _foto(tmp_path, 3000, 600), "ai")
    ust, alt = gen._last_band_rect
    assert 0 <= ust < alt <= REELS[1]
    assert img.size == REELS


def test_tall_source_band_is_capped(gen, tmp_path):
    """
    Çok uzun dikey kaynak bandı ekranı taşırmamalı; yükseklik sınırlanır.
    """
    gen._compose_photo_background(REELS, _foto(tmp_path, 600, 3000), "ai")
    if gen._last_band_rect:
        ust, alt = gen._last_band_rect
        assert alt <= REELS[1], "bant ekrandan taştı"


def test_output_size_is_always_the_target(gen, tmp_path):
    for w, h in ((1600, 900), (900, 1600), (1080, 1080), (3000, 600)):
        img = gen._compose_photo_background(REELS, _foto(tmp_path, w, h, f"{w}x{h}.jpg"), "ai")
        assert img.size == REELS


def test_cover_fit_clears_the_band_marker(gen, tmp_path):
    """
    Bant işareti bir sonraki çağrıda TEMİZLENMELİ; kalırsa cover-fit ile
    üretilen slaytın yerleşimi yanlış banda göre hesaplanır.
    """
    gen._compose_photo_background(REELS, _foto(tmp_path, 1600, 900, "a.jpg"), "ai")
    assert gen._last_band_rect is not None
    gen._compose_photo_background(REELS, _foto(tmp_path, 1080, 1800, "b.jpg"), "ai")
    assert gen._last_band_rect is None, "önceki bant işareti temizlenmedi"
