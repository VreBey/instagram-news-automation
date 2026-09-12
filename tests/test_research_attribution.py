"""Harici araştırma bulgularının kaynak atfı.

Kullanıcı isteği (4 Ağustos 2026): "Claude ve Gemini research ve Gemini Spark
ile topladığın bilgileri diğer haber kaynakları gibi ekrana damgalama."

Haklı bir itiraz: Claude bir yayın organı değil, haberi BULAN araç. Görselin
üzerine "📰 Kaynak: Claude Research" basmak okuyucuya yanlış kaynak gösterir.
Üstelik bulguların `source_url`'i zaten gerçek yayına işaret ediyor
(screenrant.com, pushsquare.com, massivelyop.com...), yani eski davranış
gerçek yayının hakkını da yiyordu.
"""

import pytest

from src.content_language import publication_from_url


# =============================================
# Yayın adı çıkarma
# =============================================

@pytest.mark.parametrize("url,beklenen", [
    ("https://screenrant.com/metal-gear-solid-4-return/", "screenrant.com"),
    ("https://www.pushsquare.com/news/2026/08/black-ops", "pushsquare.com"),
    ("https://massivelyop.com/2026/08/04/lost-ark-merges/", "massivelyop.com"),
    ("https://www.thefpsreview.com/2026/07/21/cod-beta/", "thefpsreview.com"),
    ("https://m.blizzardwatch.com/2026/07/28/wow-12-1/", "blizzardwatch.com"),
    ("https://amp.rpgsite.net/news/20973-ff14-switch-2", "rpgsite.net"),
])
def test_publication_extracted_from_url(url, beklenen):
    assert publication_from_url(url) == beklenen


@pytest.mark.parametrize("url", [
    None, "", "internal://external_research/12", "not-a-url", "http://localhost/x",
])
def test_unknown_source_returns_empty(url):
    """
    Boş dize bilinçli: _draw_source boş kaynakta hiçbir şey çizmiyor.
    Uyduramadığımız bir kaynağı uydurmaktansa hiç göstermemek doğru.
    """
    assert publication_from_url(url) == ""


def test_never_returns_tool_name():
    """Araç adı hiçbir koşulda kaynak olarak dönmemeli."""
    for url in ["https://claude.ai/x", "https://gemini.google.com/y", None]:
        sonuc = publication_from_url(url)
        assert sonuc not in ("Claude Research", "Gemini Spark", "Harici Araştırma")


# =============================================
# Araştırma → haber akışı
# =============================================

def test_promoted_research_credits_real_publication(tmp_db):
    """Asıl regresyon: eskiden source_name 'Claude Research' yazılıyordu."""
    rid = tmp_db.add_external_research(
        source="claude_research",
        title="Metal Gear Solid 4 Returns to Modern Platforms",
        summary="Konami duyurdu.",
        source_url="https://screenrant.com/metal-gear-solid-4-return-august-2026/",
    )
    news_id = tmp_db.promote_research_to_news(rid, category="gaming")

    with tmp_db._get_connection() as conn:
        kaynak = conn.execute(
            "SELECT source_name FROM news_items WHERE id = ?", (news_id,)
        ).fetchone()["source_name"]

    assert kaynak == "screenrant.com"
    assert "Claude" not in kaynak
    assert "Gemini" not in kaynak


def test_gemini_spark_also_credits_publication(tmp_db):
    rid = tmp_db.add_external_research(
        source="gemini_spark", title="Lost Ark Server Merge",
        source_url="https://massivelyop.com/2026/08/04/lost-ark/",
    )
    news_id = tmp_db.promote_research_to_news(rid, category="gaming")

    with tmp_db._get_connection() as conn:
        kaynak = conn.execute(
            "SELECT source_name FROM news_items WHERE id = ?", (news_id,)
        ).fetchone()["source_name"]
    assert kaynak == "massivelyop.com"


def test_research_without_url_has_no_source_label(tmp_db):
    """
    Kaynak URL'si yoksa etiket boş kalır — 'Manuel Araştırma' gibi bir şey
    basmak, olmayan bir yayın uydurmak olurdu.
    """
    rid = tmp_db.add_external_research(
        source="telegram_manual", title="Elle girilmiş bir bulgu",
    )
    news_id = tmp_db.promote_research_to_news(rid, category="ai")

    with tmp_db._get_connection() as conn:
        kaynak = conn.execute(
            "SELECT source_name FROM news_items WHERE id = ?", (news_id,)
        ).fetchone()["source_name"]
    assert kaynak == ""
