"""Reels metni DUYULMUYOR, OKUNUYOR — ekran bütçesi.

Video sessiz (REELS_SILENT) ve her segment ekranda sadece
REELS_CONFIG["duration_per_slide"] saniye duruyor. Prompt eskiden
"sesli okuma için" diyordu; model de anlatıma göre uzun ve süslü
cümleler yazıyordu.

Ölçüm (8 Ağustos 2026, üretim veritabanı): 165 segmentin 152'si (%92)
okunabilir bütçeyi aşıyordu — ortalama 13,5 kelime / 96 karakter.
"""

import pytest

from config import PROMPTS, REELS_CONFIG, REELS_SEGMENT_MAX_KELIME
from src.content_processor import ContentProcessor


def test_budget_is_derived_not_hardcoded():
    """
    Slayt süresi değişirse bütçe de değişmeli. Sabit yazılırsa prompt
    gerçeğe aykırı bir sayı söylemeye devam eder ve kimse fark etmez.
    """
    beklenen = max(6, int(REELS_CONFIG["duration_per_slide"] * 3.3 * 0.7))
    assert REELS_SEGMENT_MAX_KELIME == beklenen


def test_prompt_states_the_text_is_read_not_heard():
    p = PROMPTS["generate_reels_script"]
    assert "SESLİ OKUNMAYACAK" in p
    assert "sesli okuma için" not in p, "eski anlatım varsayımı geri gelmiş"


def test_prompt_carries_the_real_numbers():
    """Bütçe ve süre prompt'a GERÇEK değerlerden geçmeli."""
    p = PROMPTS["generate_reels_script"]
    assert "{segment_kelime}" in p and "{slayt_saniye}" in p
    dolu = p.format(news_list="x", slayt_saniye=REELS_CONFIG["duration_per_slide"],
                    segment_kelime=REELS_SEGMENT_MAX_KELIME)
    assert str(REELS_SEGMENT_MAX_KELIME) in dolu
    assert str(REELS_CONFIG["duration_per_slide"]) in dolu


@pytest.mark.parametrize("kural", [
    "UYDURMA",          # haberde olmayan rakam/tarih/yorum yasağı
    "SOMUT OL",         # isim/tarih/rakamdan en az biri
    "doğal Türkçe",     # dil kalitesi
    "TEK SATIR",        # parser tek satıra bağlı
])
def test_prompt_has_the_rules_other_prompts_have(kural):
    """
    Diğer dört prompt'ta olan disiplin burada YOKTU — denetimde çıkan
    eksik buydu.
    """
    assert kural in PROMPTS["generate_reels_script"]


def test_fake_cta_is_banned():
    """Sistemde 'detaylar profilde' diye bir hedef yok."""
    assert "Detaylar profilde" in PROMPTS["generate_reels_script"]


def test_overlong_segments_are_logged(tmp_db, caplog):
    """
    Aşım KESİLMİYOR (kelime ortasından bölmek daha kötü olurdu) ama
    günlüğe yazılıyor — prompt değişince oranın düştüğü görülebilsin.
    """
    p = ContentProcessor(db=tmp_db)
    uzun = " ".join(["kelime"] * (REELS_SEGMENT_MAX_KELIME + 5))
    kisa = "Kısa cümle"

    with caplog.at_level("WARNING"):
        sayi = p._uyar_uzun_segmentler([kisa, uzun])

    assert sayi == 1
    assert "bütçesini aşıyor" in caplog.text


def test_within_budget_stays_silent(tmp_db, caplog):
    p = ContentProcessor(db=tmp_db)
    with caplog.at_level("WARNING"):
        sayi = p._uyar_uzun_segmentler(["Kısa cümle", "Bu da kısa"])
    assert sayi == 0
    assert "bütçesini aşıyor" not in caplog.text


def test_total_video_fits_instagram(tmp_db):
    """Slayt süresi uzatıldı — 5 haber + intro + outro hâlâ sınırın altında."""
    toplam = 7 * REELS_CONFIG["duration_per_slide"]
    assert toplam < REELS_CONFIG["max_duration"]
    assert toplam > REELS_CONFIG["min_duration"]
