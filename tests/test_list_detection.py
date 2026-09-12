"""Carousel (liste) tespiti — ayrıştırıcı ve ön filtre.

Ölçüm (8 Ağustos 2026): 279 üretilmiş içeriğin **0'ında** `list_items`
doluydu. Yani carousel özelliği hiç çalışmadı ve `detect_list_content` her
haber için boşuna bir Gemini çağrısı harcadı.

Sebebi promptun kendi içindeki tutarsızlıktı: GÖVDE "LİSTE" ve "ÖGELER"
(noktalı İ, Ö) yazıyor, FORMAT BLOĞU "LISTE" ve "OGE" yazıyordu. Model
doğal olarak gövdedeki yazımı taklit edip `ÖGE 1:` üretiyor; ayrıştırıcının
`OGE` kalıbında IGNORECASE bile yoktu ve Ö ≠ O olduğu için hiçbir öge
ayrışmıyordu.
"""

import pytest

from src.content_processor import ContentProcessor as C


def _ayristir(metin):
    return C._parse_list_content_response(metin)


# =============================================
# Yazım varyantları — model gövdedeki biçimi taklit ediyor
# =============================================

@pytest.mark.parametrize("etiketler", [
    ("LISTE", "KAPAK", "OGE"),      # format bloğundaki yazım
    ("LİSTE", "KAPAK", "ÖGE"),      # gövdedeki yazım — eskiden HİÇ çalışmıyordu
    ("Liste", "Kapak", "Öge"),      # karışık büyük/küçük
    ("liste", "kapak", "öge"),      # küçük harf
])
def test_all_spelling_variants_parse(etiketler):
    liste, kapak, oge = etiketler
    r = _ayristir(f"{liste}: EVET\n{kapak}: Ağustos'un en iyi 2 oyunu\n"
                  f"{oge} 1: Elden Ring | 5 Ağustos'ta çıkıyor\n"
                  f"{oge} 2: Silksong | 6 Ağustos'ta çıkıyor")
    assert r is not None, f"{etiketler} ayrıştırılamadı"
    assert len(r["items"]) == 2
    assert r["cover"] == "Ağustos'un en iyi 2 oyunu"


def test_markdown_wrapping_is_tolerated():
    r = _ayristir("**LISTE:** EVET\n**KAPAK:** Başlık\n"
                  "**OGE 1:** A | detay\n**OGE 2:** B | detay")
    assert r is not None
    assert r["cover"] == "Başlık"
    assert [i["name"] for i in r["items"]] == ["A", "B"]


# =============================================
# Uydurma sayı koruması — en katı kural
# =============================================

def test_item_without_separator_rejects_the_whole_list():
    """
    Ayraçsız öge sessizce düşseydi kapak "en iyi 5 oyun" derken carousel'de
    3 slayt olurdu — yani kapak metni UYDURMA bir sayı vaat ederdi. Bu
    projenin en katı kuralı ("uydurma bilgi yok") tam da burada ihlal
    ediliyordu.
    """
    r = _ayristir("LISTE: EVET\nKAPAK: Ağustos'un en iyi 3 oyunu\n"
                  "OGE 1: A | detay\n"
                  "OGE 2: B - detay\n"      # ayraç yerine tire
                  "OGE 3: C | detay")
    assert r is None, "eksik ayraçlı liste kabul edildi; kapaktaki sayı yalan olurdu"


# =============================================
# Fail-closed
# =============================================

def test_hayir_produces_nothing():
    assert _ayristir("LISTE: HAYIR") is None


def test_single_item_is_not_a_carousel():
    assert _ayristir("LISTE: EVET\nKAPAK: x\nOGE 1: A | detay") is None


def test_missing_cover_is_rejected():
    assert _ayristir("LISTE: EVET\nOGE 1: A | d\nOGE 2: B | d") is None


def test_unusable_response_is_rejected():
    assert _ayristir("Model bugün başka bir şey söyledi.") is None
    assert _ayristir("") is None


def test_items_are_capped():
    satirlar = "\n".join(f"OGE {i}: Ad{i} | detay" for i in range(1, 12))
    r = _ayristir(f"LISTE: EVET\nKAPAK: Uzun liste\n{satirlar}")
    assert len(r["items"]) == 8


# =============================================
# Ön filtre — boşa Gemini çağrısını keser
#
# Canlı ölçüm: 400 işlenmiş haberin yalnızca 7'si (%2) filtreyi geçiyor.
# Haber başına çağrı 3 → 2.02, yani ~%33 kota tasarrufu.
# =============================================

@pytest.mark.parametrize("baslik", [
    "Ağustos'un en iyi 5 oyunu",
    "World's Top 5 Real Estate Companies by Revenue",
    "10 things you should know about the update",
    "Bu hafta çıkan 3 oyun",
    "Best 7 games of the year",
])
def test_list_signals_pass_the_prefilter(baslik):
    assert C._liste_sinyali_var({"title": baslik, "description": ""}) is True


@pytest.mark.parametrize("baslik", [
    "Elden Ring Switch 2'ye geliyor",
    "Apex Legends Ends Original Nintendo Switch Support",
    "OpenAI yeni modelini duyurdu",
])
def test_ordinary_news_is_filtered_out(baslik):
    assert C._liste_sinyali_var({"title": baslik, "description": ""}) is False


def test_signal_in_description_also_counts():
    """Başlık sade olabilir, liste sinyali açıklamada geçebilir."""
    assert C._liste_sinyali_var({
        "title": "Yeni oyunlar",
        "description": "İşte bu ay çıkacak 5 oyun ve çıkış tarihleri.",
    }) is True


def test_prefilter_skips_the_gemini_call(tmp_db, monkeypatch):
    """Sinyal yoksa model HİÇ çağrılmamalı — asıl kazanç bu."""
    from src.content_processor import ContentProcessor

    p = ContentProcessor(db=tmp_db)
    p.client = object()
    cagrildi = []
    monkeypatch.setattr(p, "_generate_with_retry",
                        lambda *a, **k: cagrildi.append(1))

    assert p._ai_detect_list_content(
        {"title": "Elden Ring Switch 2'ye geliyor", "description": ""}) is None
    assert cagrildi == [], "liste sinyali yokken Gemini çağrıldı"
