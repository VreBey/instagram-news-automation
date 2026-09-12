"""Model çıktısı görsele/caption'a basılmadan önce temizlenmeli.

Gemini düz metin döndürüyor (yapılandırılmış çıktı modu yok). Promptlar
biçimi açıkça söylüyor ama model küçük sapmalar yapıyor ve bunlar doğrudan
yayınlanan metne düşüyordu.

İki ölçülmüş kusur (8 Ağustos 2026):

1. `summarize_news` promptu "en fazla 60 KARAKTER, sert bir sınır" diyor ve
   gerekçesini de yazıyor. Kodda HİÇBİR uygulama yoktu. Canlı ölçüm, 329
   gönderi özeti: ortalama 101, medyan 111, maks 220 karakter; %58'i sınırı
   aşıyor. Görsel üreteci fontu 28px'e kadar küçültüyor, yani promptun
   önlemek için yazıldığı defekt serbest bırakılmıştı.

2. `_parse_caption_response` işaretçiyi satır başına bağlamıyor ve
   IGNORECASE kullanıyordu. Model nezaket öneki eklediğinde ("Tabii! İşte
   istediğiniz caption:") ilk eşleşme o önekte bulunuyor ve caption
   "CAPTION:" ile başlıyordu — Instagram'ın akışta gösterdiği İLK SATIR.
"""

from src.content_processor import ContentProcessor as C


# =============================================
# Manşet normalleştirme
# =============================================

def test_marker_prefix_is_stripped():
    assert C._normalize_manset("Manşet: iPhone 15'te sorun") == "iPhone 15'te sorun"
    assert C._normalize_manset("Başlık: Yeni oyun çıktı") == "Yeni oyun çıktı"


def test_markdown_and_quotes_are_stripped():
    assert C._normalize_manset("**Yeni oyun duyuruldu**") == "Yeni oyun duyuruldu"
    assert C._normalize_manset('"Elden Ring çıktı"') == "Elden Ring çıktı"
    assert C._normalize_manset("“Elden Ring çıktı”") == "Elden Ring çıktı"


def test_long_output_is_cut_at_the_first_sentence():
    """
    Model tipik olarak önce iyi bir manşet yazıp ardından caption detayı
    ekliyor. İlk cümle sınırı doğal ve kayıpsız bir kısaltma veriyor:
    canlı veride 45 vaka ortalama 151 → 42 karaktere indi.
    """
    uzun = ("iPhone 15'te aşırı ısınma sorunu! Kullanıcılar şarjdayken "
            "cihazın çok ısındığını bildiriyor ve Apple konuyu inceliyor.")
    assert C._normalize_manset(uzun) == "iPhone 15'te aşırı ısınma sorunu!"


def test_short_output_is_left_alone():
    """Hedefin altındaki manşete dokunulmamalı."""
    kisa = "Elden Ring Switch 2'ye geliyor"
    assert C._normalize_manset(kisa) == kisa


def test_ceiling_is_enforced_at_a_word_boundary():
    """Cümle sınırı yoksa bile 100 karakteri aşmamalı — font 28px'e iniyor."""
    cumlesiz = "Bu çok uzun bir manşet " * 8
    sonuc = C._normalize_manset(cumlesiz)
    assert len(sonuc) <= 101, f"tavan aşıldı: {len(sonuc)}"
    assert sonuc.endswith("…")
    assert not sonuc[:-1].endswith(" "), "kelime ortasından/boşluktan kesilmiş"


def test_empty_and_none_fail_closed():
    assert C._normalize_manset(None) is None
    assert C._normalize_manset("   ") is None
    assert C._normalize_manset("**  **") is None


# =============================================
# Caption ayrıştırma
# =============================================

def _ilk_satir(yanit):
    return C._parse_caption_response(yanit)["caption"].split("\n")[0]


def test_politeness_prefix_does_not_leak_into_the_caption():
    yanit = ("Tabii! İşte istediğiniz caption:\n\n"
             "CAPTION:\nElden Ring'in yeni DLC'si bugün çıktı.\n"
             "HASHTAGS: #EldenRing #FromSoftware #Oyun")
    assert _ilk_satir(yanit) == "Elden Ring'in yeni DLC'si bugün çıktı."


def test_markdown_markers_do_not_leak():
    yanit = ("**CAPTION:**\nElden Ring DLC bugün çıktı.\n"
             "**HASHTAGS:** #EldenRing #Oyun #Haber")
    r = C._parse_caption_response(yanit)
    assert r["caption"] == "Elden Ring DLC bugün çıktı."
    assert len(r["hashtags"]) == 3


def test_normal_response_still_parses():
    yanit = ("CAPTION:\nBirinci satır burada.\nİkinci satır da var.\n"
             "HASHTAGS: #a #b #c")
    r = C._parse_caption_response(yanit)
    assert r["caption"] == "Birinci satır burada.\nİkinci satır da var."
    assert r["hashtags"] == ["#a", "#b", "#c"]


def test_missing_hashtags_line_still_recovers_the_caption():
    r = C._parse_caption_response("CAPTION:\nSadece caption var.")
    assert r["caption"] == "Sadece caption var."
    assert r["hashtags"] == []


def test_unusable_response_fails_closed():
    assert C._parse_caption_response("Model bugün başka bir şey söyledi.") is None
    assert C._parse_caption_response("") is None
