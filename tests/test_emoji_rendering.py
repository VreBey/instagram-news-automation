"""Emoji görsellere GERÇEKTEN çiziliyor mu?

NotoColorEmoji (Linux) BİTMAP bir font: yalnızca kendi gömülü boyutunda
(109 px) açılır, başka her boyutta PIL "invalid pixel size" hatası verir.
Kod bu hatayı `except Exception: continue` ile yutuyordu.

Ölçüm (8 Ağustos 2026, üretim sunucusu): 28 / 56 / 100 px isteklerinin
ÜÇÜ DE hata veriyordu. Sonuç: reels giriş slaytındaki büyük 🎮/🤖 hiç
görünmüyordu — slayt bomboş duruyordu — ve hikâye prompt'unun açıkça
istediği emoji'ler de sessizce kayboluyordu. Hiçbir yerde hata görünmediği
için sorun uzun süre fark edilmedi.
"""

from PIL import Image, ImageDraw, ImageStat

import pytest

from src.image_generator import ImageGenerator


@pytest.fixture
def gen(tmp_db):
    return ImageGenerator(db=tmp_db)


def _emoji_var_mi(gen) -> bool:
    return gen._get_emoji_font(56) is not None


def test_emoji_font_loads_at_arbitrary_sizes(gen):
    """
    Sistemde emoji fontu varsa HER boyutta bir font dönmeli. Eskiden
    yalnızca 109 px'te dönüyordu, diğerlerinde None'a düşüyordu.
    """
    if not _emoji_var_mi(gen):
        pytest.skip("sistemde emoji fontu yok")
    for boyut in (28, 56, 100):
        assert gen._get_emoji_font(boyut) is not None, f"{boyut}px yüklenemedi"


def test_emoji_is_scaled_to_the_requested_size(gen):
    if not _emoji_var_mi(gen):
        pytest.skip("sistemde emoji fontu yok")
    for boyut in (28, 56, 100):
        gorsel = gen._emoji_bitmap("🎮", boyut)
        assert gorsel is not None
        assert gorsel.height == boyut, \
            f"{boyut}px istendi, {gorsel.height}px üretildi"


def test_emoji_actually_paints_pixels(gen):
    """
    ASIL TEST: çizimden sonra tuval DEĞİŞMELİ. Ölçü fonksiyonu bir sayı
    döndürüyor diye emoji çizilmiş sayılmaz — eski kod tam olarak bu yüzden
    sessizce başarısızdı.
    """
    if not _emoji_var_mi(gen):
        pytest.skip("sistemde emoji fontu yok")
    img = Image.new("RGB", (300, 200), (0, 0, 0))
    d = ImageDraw.Draw(img)

    gen._draw_mixed_text(d, (20, 20), "🎮", gen._get_font("accent", 80),
                         fill=(255, 255, 255))

    assert ImageStat.Stat(img.convert("L")).mean[0] > 0.5, "tuval boş kaldı"


def test_measured_width_matches_what_is_drawn(gen):
    """
    Ölçü ile çizim aynı şeyi söylemeli. Ölçü bitmap font boyutundan
    (109 px) hesaplanırsa rozet/arka plan kutuları gerçeğin katları çıkar.
    """
    if not _emoji_var_mi(gen):
        pytest.skip("sistemde emoji fontu yok")
    img = Image.new("RGB", (600, 300), (0, 0, 0))
    d = ImageDraw.Draw(img)
    font = gen._get_font("accent", 56)

    olculen, _ = gen._measure_mixed_text(d, "🎮 Oyun", font)
    cizilen = gen._draw_mixed_text(d, (0, 0), "🎮 Oyun", font, fill=(255, 255, 255))

    assert abs(olculen - cizilen) <= 2, f"ölçü {olculen}, çizim {cizilen}"


def test_mixed_text_keeps_the_words(gen):
    """Emoji çizilemese bile metnin kendisi kaybolmamalı."""
    img = Image.new("RGB", (600, 200), (0, 0, 0))
    d = ImageDraw.Draw(img)

    genislik = gen._draw_mixed_text(d, (10, 10), "🎮 Oyun haberleri",
                                    gen._get_font("body", 40), fill=(255, 255, 255))

    assert genislik > 0
    assert ImageStat.Stat(img.convert("L")).mean[0] > 0.5


def test_missing_font_degrades_quietly(gen, monkeypatch):
    """Font hiç yoksa emoji atlanır; çizim patlamaz."""
    monkeypatch.setattr(gen, "_get_emoji_font", lambda size: None)
    img = Image.new("RGB", (400, 200), (0, 0, 0))
    d = ImageDraw.Draw(img)

    genislik = gen._draw_mixed_text(d, (10, 10), "🎮 Oyun",
                                    gen._get_font("body", 40), fill=(255, 255, 255))

    assert genislik > 0, "emoji atlanınca metin de kayboldu"
