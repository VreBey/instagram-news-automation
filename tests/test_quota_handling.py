"""Gemini kotası tükendiğinde davranış testleri.

Denetim bulgusu (2026-08-03): kota dolunca sistem durmuyor, "basit
özetleme" şablonuna düşüp yayına devam ediyordu. Yani AI kalitesinde
OLMAYAN içerik, AI içeriğiymiş gibi Instagram'a gidiyordu. Doğru davranış
"bugün eksik yayınla"dır. Bu testler o kuralı kilitler.
"""

import pytest

from src.content_processor import ContentProcessor, _is_quota_exhausted


class _FakeAPIError(Exception):
    """google-genai APIError'ın test ikizi (code + message alanlarıyla)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@pytest.fixture
def processor(tmp_db, monkeypatch):
    """Gemini yapılandırılmış GİBİ davranan bir işlemci (gerçek çağrı yok)."""
    monkeypatch.setattr("src.content_processor.genai_errors.APIError", _FakeAPIError)
    proc = ContentProcessor(db=tmp_db)
    proc.client = object()  # "yapılandırılmış" say
    return proc


def _news(i=1):
    return {
        "id": i,
        "title": f"Test haberi {i}",
        "description": "açıklama",
        "category": "ai",
        "source_name": "TestKaynak",
    }


# =============================================
# Kota ile hız sınırını ayırt etme
# =============================================

def test_quota_message_is_detected():
    err = _FakeAPIError(429, "You exceeded your current quota, please check your plan and billing details.")
    assert _is_quota_exhausted(err) is True


def test_plain_rate_limit_is_not_quota():
    """Dakikalık hız sınırı saniyeler içinde geçer — kota gibi ele alınmamalı."""
    err = _FakeAPIError(429, "Too many requests, please slow down.")
    assert _is_quota_exhausted(err) is False


def test_server_error_is_not_quota():
    assert _is_quota_exhausted(_FakeAPIError(503, "UNAVAILABLE: model overloaded")) is False


# =============================================
# Şablona düşmeme kuralı
# =============================================

def test_quota_error_does_not_produce_template_post(processor, monkeypatch):
    """
    Asıl regresyon: kota hatası şablon gönderi ÜRETMEMELİ.

    Artık yapısal garanti — şablon yolu tamamen kaldırıldı.
    """
    monkeypatch.setattr(
        processor, "_generate_with_retry",
        lambda *a, **k: (setattr(processor, "_quota_exhausted", True), None)[1],
    )

    assert processor._ai_generate_post(_news()) is None
    assert not hasattr(processor, "_fallback_generate_post")


def test_quota_error_does_not_produce_template_story(processor, monkeypatch):
    """
    Kota dolunca hikaye ÜRETİLMEZ. Artık yapısal garanti: şablon yolu
    (`_fallback_generate_story`) tamamen kaldırıldı — ham İngilizce başlığın
    başına emoji koyup dil kapısından geçiriyordu (canlıda 78 vaka).
    """
    monkeypatch.setattr(
        processor, "_generate_with_retry",
        lambda *a, **k: (setattr(processor, "_quota_exhausted", True), None)[1],
    )

    assert processor._ai_generate_story(_news()) is None
    # Şablon yolu artık HİÇ yok; geri dönülecek bir yedek kalmadı.
    assert not hasattr(processor, "_fallback_generate_story")


def test_story_fails_closed_without_gemini(tmp_db):
    """Gemini hiç yapılandırılmamışsa da hikaye üretilmemeli."""
    from src.content_processor import ContentProcessor

    p = ContentProcessor(db=tmp_db)
    p.client = None

    assert p._create_story_content(_news()) is None


def test_post_fails_closed_without_gemini(tmp_db):
    """
    Gemini hiç yapılandırılmamışsa da GÖNDERİ ÜRETİLMEZ.

    Bu test eskiden bunun TERSİNİ söylüyordu: "Gemini hiç yapılandırılmamışsa
    şablon yolu korunmalı, bu bilinçli bir kurulum tercihi." O tercih
    ölçülünce boş çıktı.

    Şablon, `summary` alanına ham `news["title"]` koyuyordu — yani KAYNAK
    dilindeki başlığı (havuzdaki 200 haberin 153'ü, %76, Türkçe değil).
    Böyle bir özet dil kapısından ASLA geçemiyor: `is_translated` "özet
    başlığın kopyası mı" diye bakıyor ve kopya olduğu için `safe_display_title`
    her seferinde None dönüyor — kırpılmış başlıkta bile
    (bkz. test_template_output_can_never_be_displayed).

    Yani şablon yolu, medyası hiçbir zaman üretilemeyecek satırlar
    üretiyordu. Bunlar onay kuyruğuna giriyor, üretim frenini besliyor ve
    sonunda atılıyordu: son 14 günde özet taşıyan 883 içeriğin 194'ü (%22)
    kapıdan geçemedi, 35'i için görsel bile üretilmişti.

    `_fallback_generate_story` ve `_fallback_generate_reels` de aynı
    gerekçeyle kaldırıldı; üçü de aynı hatayı yapıyordu.
    """
    proc = ContentProcessor(db=tmp_db)
    proc.client = None

    assert proc._create_post_content(_news()) is None
    assert not hasattr(proc, "_fallback_generate_post")


def test_template_output_can_never_be_displayed():
    """
    Yukarıdaki kararın DAYANAĞI: özet ham başlığın kopyasıysa dil kapısı
    onu her zaman bloklar. Kırpma da kurtarmaz — karşılaştırma ilk 60
    karaktere bakıyor.
    """
    from src.content_language import safe_display_title

    baslik = "Apex Legends Ends Original Nintendo Switch Support on August 4"
    assert safe_display_title({"summary_text": baslik,
                               "news_title": baslik}) is None

    uzun = "Nintendo Switch 2 Stealth Release Is a Free Upgrade for Owners of the Original Console Worldwide"
    assert safe_display_title({"summary_text": uzun[:97] + "...",
                               "news_title": uzun}) is None


# =============================================
# Boşuna çağrı yapmama
# =============================================

def test_no_api_call_after_quota_exhausted(processor):
    """Kota bittikten sonra her çağrı 5s uyku + 120s retry demekti."""
    calls = []
    processor.client = type("C", (), {
        "models": type("M", (), {
            "generate_content": lambda self, **k: calls.append(1)
        })()
    })()
    processor._quota_exhausted = True

    assert processor._generate_with_retry("prompt") is None
    assert calls == []


# =============================================
# Turu kesme + haberi yakmama
# =============================================

def test_run_stops_and_does_not_mark_news_processed(processor, tmp_db, monkeypatch):
    """
    En kritik davranış: kota yüzünden işlenemeyen haber "işlendi" olarak
    İŞARETLENMEMELİ — aksi halde kota yenilendiğinde bir daha hiç denenmez.
    """
    for i in range(3):
        tmp_db.add_news(title=f"Haber {i}", url=f"https://example.com/{i}", category="ai")

    monkeypatch.setattr(processor, "_calculate_relevance", lambda news: 0.9)
    monkeypatch.setattr("src.content_processor.MIN_PROCESSING_SCORE", 0.1)

    def _quota_dies(news):
        processor._quota_exhausted = True
        return None

    monkeypatch.setattr(processor, "_create_post_content", _quota_dies)

    stats = processor.process_all_news()

    assert stats["quota_exhausted"] is True
    assert stats["posts"] == 0
    # Hiçbir haber "işlendi" sayılmamalı → hepsi yeniden denenebilir durumda.
    assert len(tmp_db.get_unprocessed_news(limit=10)) == 3


def test_normal_run_is_unaffected(processor, tmp_db, monkeypatch):
    """Kota sorunu yokken akış aynen eskisi gibi çalışmalı."""
    tmp_db.add_news(title="Haber", url="https://example.com/1", category="ai")

    monkeypatch.setattr(processor, "_calculate_relevance", lambda news: 0.9)
    monkeypatch.setattr("src.content_processor.MIN_PROCESSING_SCORE", 0.1)
    monkeypatch.setattr(
        processor, "_create_post_content",
        lambda news: {"caption": "c", "hashtags": ["#a"], "summary": "s"},
    )
    monkeypatch.setattr(processor, "_create_story_content", lambda news: "hikaye")

    stats = processor.process_all_news()

    assert stats["quota_exhausted"] is False
    assert stats["posts"] == 1 and stats["stories"] == 1
    assert tmp_db.get_unprocessed_news(limit=10) == []


# =============================================
# Bayrak SÜRESİZ değil: kota günlük, bayrak da öyle olmalı
#
# `_quota_exhausted` yalnızca __init__'te False yapılıyordu ve
# ContentProcessor süreç ömrü boyunca yaşıyor (Scheduler.__init__ bir kez
# üretiyor, main.py süreci sonsuza kadar çalıştırıyor). Yani kota bir kez
# dolduğunda sistem, kota ertesi gün yenilenmiş olmasına rağmen servis elle
# yeniden başlatılana dek HİÇ içerik üretmiyordu — üstüne her turda
# "kota tükendi" bildirimi gidiyordu. Çökme olmadığı için de fark edilmiyor.
# =============================================

def test_quota_flag_clears_on_a_new_day(tmp_db, monkeypatch):
    from datetime import datetime, timedelta
    from src.content_processor import ContentProcessor

    p = ContentProcessor(db=tmp_db)
    cagrildi = []
    p.client = type("C", (), {"models": type("M", (), {
        "generate_content": staticmethod(
            lambda **kw: cagrildi.append(1) or type("R", (), {"text": "cevap"})()
        )})()})()
    monkeypatch.setattr("src.content_processor.time.sleep", lambda s: None)

    # Dün kota dolmuş.
    p._quota_exhausted_at = datetime.now() - timedelta(days=1)

    assert p._generate_with_retry("prompt") == "cevap"
    assert cagrildi, "yeni günde Gemini'ye hiç istek atılmadı"
    assert p._quota_exhausted is False


def test_quota_flag_holds_within_the_same_day(tmp_db, monkeypatch):
    from datetime import datetime
    from src.content_processor import ContentProcessor

    p = ContentProcessor(db=tmp_db)
    cagrildi = []
    p.client = type("C", (), {"models": type("M", (), {
        "generate_content": staticmethod(lambda **kw: cagrildi.append(1))})()})()

    p._quota_exhausted = True

    assert p._generate_with_retry("prompt") is None
    assert not cagrildi, "aynı gün içinde boşuna istek atıldı"
    assert p._quota_exhausted is True
