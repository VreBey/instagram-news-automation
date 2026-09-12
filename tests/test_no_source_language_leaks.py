"""DEĞİŞMEZ TEST: kaynak dili (İngilizce) hiçbir çıktı yüzeyine ulaşmamalı.

Bu dosya tek tek hataları değil, HATA SINIFINI kapatmak için var.

Neden gerekli — 4-5 Ağustos 2026 geçmişi. Aynı hata dört kez "düzeltildi",
her seferinde başka bir yoldan geri geldi:

  1. content_processor  "summary": summary or news["title"]
  2. image/story        summary_text or news_title
  3. video_generator    news_titles = ham İngilizce başlıklar
  4. tespitçinin kendisi 's iyelik ekini Türkçe eki sandı

Dördünden sonra yapılan kapsamlı tarama DÖRT sızıntı daha buldu:

  5. image_generator carousel  news.get("title", summary_text)  <- sıra TERS
  6. image_generator carousel  subtitle = ham İngilizce description
  7. image_generator post      subtitle ... or description
  8. roundup                   summary_text or news_title

Yapısal sebep: `X or <kaynak alan>` deyimi 5 dosyada 8+ kez KOPYALANMIŞ.
Her biri ayrı yazıldığı için birini düzeltmek diğerlerini düzeltmiyor, ve
her yeni çizim yolu deyimi yeniden üretiyor.

Bu testin yaklaşımı farklı: çağrı yerlerini denetlemiyor, PIL'in metin
çizme çağrısını dinleyip GÖRSELE BASILAN HER DİZEYİ topluyor. Yani hangi
kod yolu eklenirse eklensin, İngilizce kaynak metin piksele dönüşürse
test kırılır.
"""

import pytest
from PIL import ImageDraw

# Kaynak alanlara konan işaret. Türkçe üretimde asla oluşmayacak bir dize;
# çıktıda görünürse kaynak metin sızmış demektir.
IZ = "ZZSOURCELEAKZZ"

TURKCE_OZET = "Elden Ring, 28 Ağustos'ta Switch 2'ye geliyor"
TURKCE_CAPTION = "Elden Ring Switch 2'ye geliyor. Oyun 28 Ağustos'ta çıkıyor."


@pytest.fixture
def cizilen_metinler(monkeypatch):
    """Görsele basılan tüm metinleri toplayan dinleyici.

    PIL seviyesinde yakalıyor: hangi yardımcı fonksiyon çizerse çizsin
    (başlık, altyazı, kaynak damgası, slayt numarası) buradan geçmek zorunda.
    """
    toplanan = []

    for isim in ("text", "multiline_text"):
        orijinal = getattr(ImageDraw.ImageDraw, isim)

        def sarmalayici(self, xy, text="", *a, _orj=orijinal, **kw):
            if text:
                toplanan.append(str(text))
            return _orj(self, xy, text, *a, **kw)

        monkeypatch.setattr(ImageDraw.ImageDraw, isim, sarmalayici)

    return toplanan


def _icerik(**ek):
    """Kaynak alanları işaretli, üretilmiş alanları Türkçe bir içerik."""
    temel = {
        "id": 1,
        "news_title": f"{IZ} English Source Headline",
        "title": f"{IZ} English Source Headline",
        "description": f"{IZ} English source description text goes here.",
        "summary_text": TURKCE_OZET,
        "caption": TURKCE_CAPTION,
        "category": "gaming",
        "source_name": "example.com",
    }
    temel.update(ek)
    return temel


def _sizinti(toplanan):
    return [t for t in toplanan if IZ in t]


# =============================================
# Her çıktı yüzeyi ayrı ayrı
# =============================================

def test_post_image_never_renders_source_text(tmp_db, cizilen_metinler):
    from src.image_generator import ImageGenerator

    ImageGenerator(db=tmp_db).generate_post_image(news_data=_icerik())

    assert _sizinti(cizilen_metinler) == [], (
        f"Kaynak metin gorsele basildi: {_sizinti(cizilen_metinler)}"
    )


def test_story_image_never_renders_source_text(tmp_db, cizilen_metinler):
    from src.story_generator import StoryGenerator

    StoryGenerator(db=tmp_db).generate_story_image(news_data=_icerik())

    assert _sizinti(cizilen_metinler) == []


def test_carousel_never_renders_source_text(tmp_db, cizilen_metinler):
    """
    Carousel'de sıra TERSTİ: `news.get("title", summary_text)` — yani
    İngilizce başlığı Türkçe özete tercih ediyordu.
    """
    from src.image_generator import ImageGenerator

    ImageGenerator(db=tmp_db).generate_carousel_images([_icerik(), _icerik()])

    assert _sizinti(cizilen_metinler) == []


def test_video_slides_never_render_source_text(tmp_db, cizilen_metinler):
    from src.video_generator import VideoGenerator

    VideoGenerator(db=tmp_db)._create_slide_images({
        "intro": "Günün oyun haberleri",
        "segments": [TURKCE_OZET, "Path of Exile 2 güncellemesi yayınlandı"],
        "news_titles": [f"{IZ} English Title One", f"{IZ} English Title Two"],
        "outro": "Takipte kal",
    }, "gaming")

    assert _sizinti(cizilen_metinler) == []


# =============================================
# Metin (görsel olmayan) yüzeyler
# =============================================

def test_roundup_slides_carry_no_source_text():
    from src.roundup import build_roundup_slides

    slaytlar = build_roundup_slides([_icerik(), _icerik()])

    for s in slaytlar:
        assert IZ not in str(s.get("display_title") or "")
        assert IZ not in str(s.get("display_subtitle") or "")


def test_roundup_caption_carries_no_source_text():
    from src.roundup import build_roundup_caption

    metin = build_roundup_caption([_icerik(), _icerik()])

    assert IZ not in metin


def test_untranslated_content_produces_nothing(tmp_db, cizilen_metinler):
    """
    Özet üretilememişse (summary_text yok) hiçbir şey çizilmemeli —
    kaynak başlığa düşmek yasak.
    """
    from src.image_generator import ImageGenerator

    sonuc = ImageGenerator(db=tmp_db).generate_post_image(
        news_data=_icerik(summary_text=None, caption=None)
    )

    assert sonuc is None
    assert _sizinti(cizilen_metinler) == []


# =============================================
# Deyimin kendisi geri gelmesin
# =============================================

def test_source_fallback_idiom_is_not_reintroduced():
    """
    Asıl yapısal koruma: `X or <kaynak alan>` deyimi 5 dosyada 8+ kez
    kopyalanmıştı ve tekrarlamanın sebebi buydu. Bu test deyimin geri
    gelmesini yakalar.

    Yalnızca ÇIKTI üreten modülleri tarar; content_processor'da kaynak
    alanlar prompt kurmak için meşru şekilde kullanılıyor.
    """
    import re
    from pathlib import Path

    kok = Path(__file__).parent.parent / "src"
    yasak = re.compile(
        r"""or\s+(news|content|item|n)\s*(\[|\.get\(\s*)['"](title|news_title|description)['"]""",
        re.X,
    )

    bulgular = []
    for dosya in ("image_generator.py", "story_generator.py",
                  "video_generator.py", "roundup.py"):
        yol = kok / dosya
        if not yol.exists():
            continue
        for i, satir in enumerate(yol.read_text(encoding="utf-8").splitlines(), 1):
            # Açık istisna: bazı yerlerde kaynak alanı okumak meşru (ör. log
            # mesajında hangi içeriğin atlandığını yazmak). Bu satırlar
            # `# dil-kapisi-haric` ile işaretlenir — sessiz muafiyet yok,
            # istisna kodda görünür ve gözden geçirilebilir olsun diye.
            if "dil-kapisi-haric" in satir:
                continue
            kod = satir.split("#")[0]
            if yasak.search(kod):
                bulgular.append(f"{dosya}:{i}: {satir.strip()[:70]}")

    assert not bulgular, (
        "Kaynak-metin yedegi deyimi geri gelmis:\n  " + "\n  ".join(bulgular)
    )


# =============================================
# Liste/carousel yolu da kapıdan geçmeli
#
# `generate_post_image` liste içeriğinde carousel dalına ERKEN dönüyordu ve
# `safe_display_title` ondan SONRA geliyordu — yani `list_items` dolu olan
# her gönderi kapıyı hiç görmüyordu. Kapak metni doğrudan
# `list_items["cover"]`den geliyor ve Gemini'nin oraya ham İngilizce başlığı
# yankılamasını engelleyen bir şey yok.
# =============================================

def test_list_carousel_does_not_require_a_summary(tmp_db, monkeypatch):
    """
    Carousel `summary_text` KULLANMAZ — kapak metnini list_items["cover"]den
    alır. Bu yüzden tekil yolun kapısını carousel dalının önüne taşımak,
    özeti olmayan ama tamamen geçerli liste içeriklerini öldürür. Bu hata
    bir kez yapıldı; test onu kilitliyor.
    """
    from src.image_generator import ImageGenerator

    gen = ImageGenerator(db=tmp_db)
    news_id = tmp_db.add_news(title="English source", url="https://example.com/l1",
                              category="gaming")
    cid = tmp_db.add_content(
        news_id=news_id, content_type="post", caption="c",
        summary_text=None,  # özet yok, ama liste var
        list_items={"cover": "Ağustos'un en iyi 2 oyunu",
                    "items": [{"name": "Oyun A", "detail": "d"},
                              {"name": "Oyun B", "detail": "d"}]},
    )
    monkeypatch.setattr(gen, "generate_carousel_images",
                        lambda slides, size=None: ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"])

    assert gen.generate_post_image(content_id=cid) is not None


def test_english_cover_falls_back_instead_of_being_drawn(tmp_db):
    """
    Kapak İngilizce yankılanmışsa carousel üretilmez; çağıran taraf tekil
    görsele düşer ve orada Türkçe özet kullanılır.
    """
    from src.image_generator import ImageGenerator

    gen = ImageGenerator(db=tmp_db)
    news_id = tmp_db.add_news(title="English", url="https://example.com/l2",
                              category="gaming")
    cid = tmp_db.add_content(
        news_id=news_id, content_type="post", caption="c",
        summary_text="Ağustos'ta ertelenen oyunlar",
        list_items={"cover": "Every Game That Has Been Delayed To The Next Year",
                    "items": [{"name": "Oyun A", "detail": "d"}]},
    )

    assert gen._generate_post_carousel(cid, tmp_db.get_content_by_id(cid)) is None


def test_turkish_cover_with_english_game_names_is_allowed(tmp_db, monkeypatch):
    """
    Kapı ÖGE ADLARINA uygulanmamalı: oyun adları meşru İngilizce özel
    isimler ("The Last of Us Part II") ve tespitçi onları İngilizce sayar.
    """
    from src.image_generator import ImageGenerator

    gen = ImageGenerator(db=tmp_db)
    news_id = tmp_db.add_news(title="English", url="https://example.com/l3",
                              category="gaming")
    cid = tmp_db.add_content(
        news_id=news_id, content_type="post", caption="c",
        summary_text="Ağustos'un en iyi oyunları",
        list_items={"cover": "Ağustos'un en iyi 2 oyunu",
                    "items": [{"name": "The Last of Us Part II", "detail": "d"},
                              {"name": "Path of Exile 2", "detail": "d"}]},
    )
    monkeypatch.setattr(gen, "generate_carousel_images",
                        lambda slides, size=None: ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"])

    assert gen._generate_post_carousel(cid, tmp_db.get_content_by_id(cid)) is not None


# =============================================
# Emoji öneki dil kapısını kandıramaz
#
# `is_translated` "özet ilk 60 karakterde başlığın kopyası mı" diye bakan bir
# KARAKTER karşılaştırması. Şablon yolu ham İngilizce başlığın başına emoji
# koyuyordu; emoji + boşluk karşılaştırmayı 2 karakter kaydırıyor ve metin
# "çevrilmiş" sayılıp kapıdan geçiyordu.
#
# Canlı ölçüm (8 Ağustos 2026): 78 içerik kapıyı tam olarak bu yoldan
# geçmişti — hepsi hikaye, hepsi emoji önekli, hepsi kullanıcı tarafından
# elle reddedilmişti. "İngilizce içerik" şikayetinin doğrudan kaynağı.
# =============================================

def test_emoji_prefix_does_not_smuggle_english_through():
    from src.content_language import safe_display_title

    baslik = "Xbox Game Pass Adds Five New Games This August"
    for onek in ("\U0001F3AE ", "\U0001F916 ", "> ", "* ", "1. "):
        icerik = {"summary_text": onek + baslik, "news_title": baslik}
        assert safe_display_title(icerik) is None, \
            f"{onek!r} öneki İngilizce başlığı kapıdan geçirdi"


def test_gate_still_passes_real_turkish():
    """Kapı izin verici kalmalı — Türkçe metni engellemek daha kötü olurdu."""
    from src.content_language import safe_display_title

    for ozet in (
        "Xbox Game Pass'e ağustosta beş yeni oyun geliyor",
        "Elden Ring'in yeni bölümü çıktı",
        "\U0001F3AE Elden Ring Switch 2'ye geliyor, tarih belli oldu",
    ):
        icerik = {"summary_text": ozet, "news_title": "Some English Source Title"}
        assert safe_display_title(icerik) == ozet, f"Türkçe metin bloklandı: {ozet}"


def test_story_generator_and_video_generator_share_one_gate():
    """
    Asimetri kapatıldı: kural ortak kapıda. Eskiden `video_generator`
    `is_probably_turkish` çağırıyor, `story_generator` çağırmıyordu — yeni
    her çağıranda tekrar unutulacak bir desendi.
    """
    import inspect
    from src.content_language import safe_display_title

    kaynak = inspect.getsource(safe_display_title)
    assert "is_probably_turkish" in kaynak, \
        "dil tespiti ortak kapıda değil; çağıranlara bırakılmış"
