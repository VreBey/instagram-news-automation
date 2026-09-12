"""Ortak dil kapısı: İngilizce kaynak metnin render'a ulaşamaması.

Bu hata TEKRAR TEKRAR yaşandı ve her seferinde farklı bir yoldan geldi.
Kod tabanında en az altı yerde "çeviri yoksa kaynak başlığı kullan" yedeği
vardı; birini düzeltirken bir başkası sızdırdı.

Bu yüzden savunma, metnin PİKSELE dönüştüğü sınıra taşındı. Buradaki testler
o sınırı kilitler — yukarıdaki hangi kod yolu üretirse üretsin, çevrilmemiş
metin görsele/hikayeye basılamaz.
"""

import pytest

from src.content_language import is_translated, safe_display_title


# =============================================
# Çeviri tespiti
# =============================================

def test_translated_summary_passes():
    assert is_translated({
        "summary_text": "Elden Ring 28 Ağustos'ta Switch 2'ye geliyor",
        "news_title": "Elden Ring Comes to Nintendo Switch 2 on August 28",
    }) is True


def test_copy_of_source_title_is_not_translated():
    baslik = "Final Fantasy XIV Launches on Nintendo Switch 2 August 4"
    assert is_translated({"summary_text": baslik, "news_title": baslik}) is False


def test_comparison_is_case_insensitive():
    assert is_translated({"summary_text": "ABC HABER", "news_title": "abc haber"}) is False


def test_long_titles_compared_by_prefix():
    """İlk 60 karakter aynıysa, sonu farklı olsa da kopyadır."""
    ortak = "Call of Duty Modern Warfare 4 Beta Kicks Off August 21 After "
    assert is_translated({
        "summary_text": ortak + "COD NEXT",
        "news_title": ortak + "the COD NEXT Event",
    }) is False


def test_missing_source_title_is_accepted():
    """Kaynak başlık yoksa kıyaslanacak bir şey yok; özet kabul edilir."""
    assert is_translated({"summary_text": "Türkçe bir özet"}) is True


def test_title_key_also_works():
    """Bazı çağrılar 'title', bazıları 'news_title' taşıyor."""
    assert is_translated({"summary_text": "X", "title": "X"}) is False


# =============================================
# Render kapısı — asıl koruma
# =============================================

def test_translated_title_is_returned():
    baslik = safe_display_title({
        "summary_text": "Elden Ring Switch 2'ye geliyor",
        "news_title": "Elden Ring Comes to Switch 2",
    })
    assert baslik == "Elden Ring Switch 2'ye geliyor"


def test_untranslated_returns_none():
    """
    Asıl regresyon: eskiden `summary_text or news_title` yazıyordu ve
    İngilizce başlık doğrudan görselin üzerine basılıyordu.
    """
    baslik = "Final Fantasy XIV Launches on Nintendo Switch 2 August 4"
    assert safe_display_title({"summary_text": baslik, "news_title": baslik}) is None


@pytest.mark.parametrize("ozet", [None, "", "   ", "\n\t"])
def test_empty_summary_returns_none(ozet):
    assert safe_display_title({"summary_text": ozet, "news_title": "English Title"}) is None


def test_never_falls_back_to_source_title():
    """
    Bu fonksiyonun varlık sebebi: kaynak başlığa düşmemek. Hiçbir girdi
    kombinasyonu İngilizce başlığı geri döndürmemeli.
    """
    ing = "Some Very English Headline About Games"
    for icerik in [
        {"news_title": ing},
        {"summary_text": None, "news_title": ing},
        {"summary_text": "", "news_title": ing},
        {"summary_text": ing, "news_title": ing},
    ]:
        assert safe_display_title(icerik) != ing


def test_shared_with_roundup():
    """
    İki ayrı kopya olması, birinin düzeltilip diğerinin eskide kalmasına
    yol açardı — bu hata zaten tam olarak öyle tekrarlandı.
    """
    from src.roundup import is_translated as roundup_is_translated
    assert roundup_is_translated is is_translated


# =============================================
# Render fonksiyonları gerçekten duruyor mu
# =============================================

def test_image_generator_aborts_on_untranslated(tmp_db, monkeypatch):
    from src.image_generator import ImageGenerator

    gen = ImageGenerator(db=tmp_db)
    baslik = "Girls Frontline Fire Control Shuts Down August 26"
    sonuc = gen.generate_post_image(news_data={
        "summary_text": baslik,
        "news_title": baslik,
        "category": "gaming",
    })
    assert sonuc is None, "cevrilmemis icerik icin gorsel uretilmemeli"


def test_story_generator_aborts_on_untranslated(tmp_db):
    from src.story_generator import StoryGenerator

    gen = StoryGenerator(db=tmp_db)
    baslik = "Call of Duty Modern Warfare 4 Beta Kicks Off August 21"
    sonuc = gen.generate_story_image(news_data={
        "summary_text": baslik,
        "news_title": baslik,
        "category": "gaming",
    })
    assert sonuc is None, "cevrilmemis icerik icin hikaye uretilmemeli"
