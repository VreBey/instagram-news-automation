"""İngilizce içeriğin Türkçe akışa sızmasını engelleyen testler.

Denetim bulgusu (4 Ağustos 2026): onay kuyruğunda 6 tamamen İngilizce içerik
bulundu — "Final Fantasy XIV Launches on Nintendo Switch 2 August 4...",
"Call of Duty: Modern Warfare 4 Beta Kicks Off August 21..." vb.

Kök neden `summary or news["title"]` idi: özet üretimi patladığında ham
İngilizce haber başlığına düşülüyordu. `summary_text` görselin üzerine
basılan manşet ve Telegram'da görünen satır olduğu için, caption Türkçe
üretilmiş olsa bile gönderi İngilizce manşetle akışa giriyordu.
"""

import pytest

from src.content_processor import ContentProcessor


@pytest.fixture
def processor(tmp_db):
    p = ContentProcessor(db=tmp_db)
    p.client = object()  # "Gemini yapılandırılmış" say
    return p


def _news():
    return {
        "id": 1,
        "title": "Final Fantasy XIV Launches on Nintendo Switch 2 August 4",
        "description": "Square Enix announced the release date.",
        "category": "gaming",
        "source_name": "Claude Research",
    }


# =============================================
# Asıl regresyon
# =============================================

def test_english_title_is_not_used_as_summary(processor, monkeypatch):
    """
    Özet üretilemezse içerik HİÇ üretilmemeli. Eskiden İngilizce başlık
    summary_text olarak kullanılıyordu.
    """
    monkeypatch.setattr(processor, "_generate_with_retry",
                        lambda *a, **k: "CAPTION: Türkçe bir açıklama\nHASHTAGS: #oyun")
    monkeypatch.setattr(processor, "_parse_caption_response",
                        lambda t: {"caption": "Türkçe bir açıklama", "hashtags": ["#oyun"]})
    monkeypatch.setattr(processor, "_ai_summarize", lambda news: None)   # özet patlıyor

    sonuc = processor._ai_generate_post(_news())

    assert sonuc is None, "ozet uretilemezse gonderi uretilmemeli"


def test_successful_summary_is_used(processor, monkeypatch):
    monkeypatch.setattr(processor, "_generate_with_retry", lambda *a, **k: "x")
    monkeypatch.setattr(processor, "_parse_caption_response",
                        lambda t: {"caption": "Türkçe açıklama", "hashtags": ["#oyun"]})
    monkeypatch.setattr(processor, "_ai_summarize",
                        lambda news: "Final Fantasy XIV Switch 2'ye geliyor")
    monkeypatch.setattr(processor, "_ai_detect_list_content", lambda news: None)

    sonuc = processor._ai_generate_post(_news())

    assert sonuc["summary"] == "Final Fantasy XIV Switch 2'ye geliyor"
    assert sonuc["summary"] != _news()["title"]


def test_empty_summary_also_skips(processor, monkeypatch):
    """Boş string de başarısızlıktır."""
    monkeypatch.setattr(processor, "_generate_with_retry", lambda *a, **k: "x")
    monkeypatch.setattr(processor, "_parse_caption_response",
                        lambda t: {"caption": "c", "hashtags": []})
    monkeypatch.setattr(processor, "_ai_summarize", lambda news: "   ".strip() or None)

    assert processor._ai_generate_post(_news()) is None


def test_skip_does_not_fall_back_to_template(processor, monkeypatch):
    """
    Özet başarısızlığında şablona düşmek de İngilizce başlık kullanırdı.

    Artık YAPISAL garanti: `_fallback_generate_post` tamamen kaldırıldı
    (8 Ağustos 2026), tıpkı `_story` ve `_reels` ikizleri gibi. Geri
    dönülecek bir yedek yok.
    """
    monkeypatch.setattr(processor, "_generate_with_retry", lambda *a, **k: "x")
    monkeypatch.setattr(processor, "_parse_caption_response",
                        lambda t: {"caption": "c", "hashtags": []})
    monkeypatch.setattr(processor, "_ai_summarize", lambda news: None)

    assert processor._ai_generate_post(_news()) is None
    assert not hasattr(processor, "_fallback_generate_post")


# =============================================
# Derleme kendini içermemeli
# =============================================

def _taslak(db, i, ozet, carousel=False):
    news_id = db.add_news(title=f"H{i}", url=f"https://example.com/{i}", category="ai")
    db.mark_news_processed(news_id, relevance_score=0.8)
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text=ozet)
    if carousel:
        db.update_content_carousel(cid, ["/tmp/a.png", "/tmp/b.png"])
    return cid


def test_roundup_does_not_include_itself(tmp_db):
    """
    Derleme de content_type='post' ve status='draft' olarak kaydediliyor;
    filtrelenmezse bir sonraki derleme öncekini madde olarak içine alıyor.
    4 Ağustos 2026'da üretilen derlemenin 4. maddesi "Günün 6 Haberi" çıktı.
    """
    _taslak(tmp_db, 1, "Normal bir haber özeti")
    _taslak(tmp_db, 2, "Başka bir haber")
    _taslak(tmp_db, 3, "Günün 6 Haberi", carousel=True)   # önceki derleme

    adaylar = tmp_db.get_roundup_candidates(limit=10)

    ozetler = [a["summary_text"] for a in adaylar]
    assert "Günün 6 Haberi" not in ozetler
    assert len(adaylar) == 2


def test_normal_posts_still_selected(tmp_db):
    """Filtre yalnızca derlemeleri elemeli, normal gönderileri değil."""
    for i in range(4):
        _taslak(tmp_db, i, f"Haber özeti {i}")
    assert len(tmp_db.get_roundup_candidates(limit=10)) == 4
