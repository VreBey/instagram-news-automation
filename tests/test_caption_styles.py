"""Caption tarzları KOPYALANACAK bir cümle vermemeli.

Rotasyon caption'ların hep aynı kalıba düşmemesi için var. Ama tarzlar
örnek cümleyle anlatılıyordu (ör. "Bunu bekliyor muydunuz?", "Vay be") ve
model o örnekleri kelimesi kelimesine kopyalıyordu — yani çeşitlilik aracı
yeni bir kalıp üretiyordu.

Ölçüm (8 Ağustos 2026, son 14 günün 446 gönderi caption'ı):
    "vay ..."                  → 52 caption (%12)
    "bunu ..."                 → 69 caption (%15)
    "Bunu bekliyor muydunuz?"  → 20 kez, birebir aynı
    "Bunu okuyunca açıkçası"   → 15 kez, birebir aynı
"""

import re

import pytest

from config import CAPTION_STYLES, PROMPTS
from src.content_processor import ContentProcessor


# Canlıda birebir kopyalandığı ölçülen ifadeler.
KOPYALANAN = ["Bunu bekliyor muydunuz", "Bunu okuyunca", "Vay be",
              "Şu işe bakın"]


@pytest.mark.parametrize("ifade", KOPYALANAN)
def test_copied_phrases_are_gone(ifade):
    birlesik = " ".join(CAPTION_STYLES)
    assert ifade not in birlesik, \
        f"model bu ifadeyi birebir kopyalıyordu, tarzdan çıkarılmalı: {ifade!r}"


def test_styles_give_no_quoted_example_sentence():
    """
    Tırnak içinde verilen her örnek cümle kopyalanma adayı. Tarz NE
    YAPILACAĞINI tarif etmeli, hangi kelimelerle olacağını değil.
    """
    for tarz in CAPTION_STYLES:
        assert not re.search(r'"[^"]{4,}"', tarz), \
            f"tarz kopyalanabilir bir örnek cümle içeriyor: {tarz!r}"


def test_prompt_says_the_style_is_not_a_template():
    assert "TARZ BİR KALIP DEĞİL" in PROMPTS["generate_caption"]


def test_styles_stay_distinct():
    """Tarzlar birbirinin aynısı olmamalı — rotasyonun anlamı kalmaz."""
    assert len(set(CAPTION_STYLES)) == len(CAPTION_STYLES)
    assert len(CAPTION_STYLES) >= 4


def test_style_selection_is_deterministic_and_rotates(tmp_db, monkeypatch):
    """
    Aynı haber aynı tarzı almalı (yeniden işlense de caption tutarlı olsun),
    farklı haberler farklı tarzlara dağılmalı.
    """
    p = ContentProcessor(db=tmp_db)
    p.client = object()
    gorulen = []
    monkeypatch.setattr(p, "_generate_with_retry",
                        lambda prompt: gorulen.append(prompt) or None)

    def _uret(haber_id):
        gorulen.clear()
        p._ai_generate_post({"id": haber_id, "title": "T", "category": "ai",
                             "description": "a", "source_name": "IGN"})
        return gorulen[0]

    assert _uret(7) == _uret(7), "aynı haber farklı tarz aldı"

    tarzlar = {_uret(i) for i in range(len(CAPTION_STYLES) * 2)}
    assert len(tarzlar) >= len(CAPTION_STYLES), "rotasyon tüm tarzları kullanmıyor"
