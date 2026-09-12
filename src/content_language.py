"""İçerik dili güvencesi: İngilizce kaynak metnin render'a ulaşmasını engeller.

Neden ayrı bir modül: bu hata **tekrar tekrar** yaşandı ve her seferinde
farklı bir yoldan geldi. Kaynak haber başlığı İngilizce, hesap Türkçe ve
kod tabanında en az altı yerde "çeviri yoksa başlığı kullan" yedeği vardı:

    content_processor  "summary": summary or news["title"]
    image_generator    content.get("summary_text") or content.get("news_title")
    story_generator    content.get("summary_text") or content.get("news_title")
    content_processor  şablon üreticileri (KALDIRILDI, 8 Ağustos 2026 —
                       üçü de ham kaynak başlığını metne koyuyordu)
    content_processor  result["news_titles"] = [n["title"] for n in news_items]
    video_generator    script.get("news_titles", segments)

Her birini tek tek yamamak işe yaramadı — biri düzeltilirken bir başkası
sızdırdı. Bu yüzden savunma, metnin PİKSELE dönüştüğü sınıra taşındı:
yukarıdaki hangi yol üretirse üretsin, gösterilecek metin buradan geçmek
zorunda.

İlke: çevrilmemiş metni göstermektense içeriği hiç üretmemek. Kota
politikasıyla aynı — eksik yayınlamak, yanlış dilde yayınlamaktan iyidir.
"""

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def publication_from_url(url: str | None) -> str:
    """URL'den gerçek yayın adını çıkar; çıkarılamıyorsa boş dize döndür.

    Neden: harici araştırma bulguları (Claude Research, Gemini Spark) haberi
    BULAN aracın adıyla kaydediliyordu ve bu ad görselin üzerine
    "📰 Kaynak: Claude Research" diye basılıyordu. Claude bir yayın organı
    değil — okuyucuya yanlış kaynak gösteriyordu. Oysa bulguların
    `source_url`'i zaten gerçek yayına işaret ediyor.

    Boş dize döndürmek bilinçli: `_draw_source` boş kaynakta hiçbir şey
    çizmiyor, yani uyduramadığımız bir kaynağı uydurmak yerine hiç
    göstermiyoruz.
    """
    if not url:
        return ""

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""

    if not host or host in ("localhost",):
        return ""
    # internal://external_research/12 gibi sahte URL'ler yayın değildir.
    if url.startswith("internal://"):
        return ""

    for onek in ("www.", "m.", "amp."):
        if host.startswith(onek):
            host = host[len(onek):]

    return host

# İlk bu kadar karakteri aynıysa özet, kaynak başlığın kopyasıdır.
TRANSLATION_COMPARE_LENGTH = 60


def is_translated(item: dict) -> bool:
    """Özet gerçekten çevrilmiş mi, yoksa kaynak başlığın kopyası mı?

    Ölçüt sezgisel değil: `summary_text`, haberin kendi başlığıyla
    (ilk 60 karakter) aynıysa çeviri adımı çalışmamıştır. Gemini'nin
    başarısız olduğu durumlarda kod ham başlığı özet yerine koyuyordu.
    """
    ozet = (item.get("summary_text") or "").strip()
    baslik = (item.get("news_title") or item.get("title") or "").strip()

    if not ozet:
        return False
    if not baslik:
        return True

    n = TRANSLATION_COMPARE_LENGTH
    return ozet[:n].casefold() != baslik[:n].casefold()


# Türkçeye özgü harfler — varlıkları tek başına güçlü kanıt.
_TR_CHARS = set("çğıöşüÇĞİÖŞÜ")

# Türkçede çok sık, İngilizcede geçmeyen kelimeler/ekler.
_TR_WORDS = {
    "ve", "bir", "için", "ile", "bu", "da", "de", "olarak", "oldu", "var",
    "yeni", "daha", "çok", "geliyor", "çıktı", "ediyor", "yapıyor", "oyun",
    "oyunu", "sürüm", "güncelleme", "duyurdu", "açıklandı", "kaldı", "sonra",
    "üzerinde", "hakkında", "kez", "yıl", "gün", "en", "mi", "mu",
}

# İngilizcede çok sık, Türkçede geçmeyen işlev kelimeleri.
# Not: bu listeyi genişletmek güvenli, çünkü fonksiyon ÖNCE Türkçe kanıtına
# bakıp True dönüyor. İngilizce sayımına ancak hiç Türkçe işaret yokken
# geliliyor — yani Türkçe bir cümlenin buraya düşme ihtimali çok düşük.
_EN_WORDS = {
    "the", "is", "are", "to", "for", "with", "on", "and", "of", "in", "has",
    "have", "will", "you", "your", "this", "that", "from", "how", "why",
    "what", "after", "before", "off", "out", "into", "over", "about", "its",
    "launches", "kicks", "shuts", "brings", "comes", "returns", "first",
    "time", "next", "now", "more", "than", "been", "was", "were",
    # Başlıklarda sık geçen, Türkçede karşılığı olmayan kelimeler.
    "down", "up", "back", "full", "game", "games", "months", "month",
    "years", "week", "weeks", "days", "nine", "ten", "all", "get", "gets",
    "adds", "coming", "announced", "confirmed", "reveals", "here", "makes",
    "made", "just", "still", "even", "most", "best", "control", "launch",
    "release", "update", "season", "players", "player",
    # İngilizce işlev kelimeleri (kapalı sınıf — Türkçede hiç geçmezler).
    "a", "an", "at", "as", "by", "be", "or", "if", "it", "we", "they",
    "their", "his", "her", "our", "them", "do", "does", "did", "can",
    "could", "would", "should", "may", "might", "must", "not", "no", "so",
    "when", "where", "who", "which", "while", "also", "any", "some",
    "other", "another", "each", "every", "into", "onto", "than", "then",
    "there", "these", "those", "such", "very", "too", "only", "well",
    "take", "takes", "make", "surprise", "weapon", "supremacy", "rules",
}

# "Switch 2'ye", "Ağustos'ta", "PS5'in" — kesme işaretiyle ek alma
# Türkçeye özgü bir yazım biçimi ve Türkçe harf içermeyen cümlelerde bile
# güçlü bir işaret.
#
# Tek başına `'s` HARİÇ: o İngilizce iyelik eki ("Europe's", "China's",
# "Fortune's Weave"). Türkçede kesmeden sonra 'ye/'ta/'nin/'den gibi ekler
# gelir, tek harflik `s` gelmez. Bu ayrım olmadan tespitçi düpedüz İngilizce
# başlıkları Türkçe sanıyordu — canlı veride 5 başlığın 3'ü böyle kaçtı.
_TR_APOSTROPHE = re.compile(r"['’](?!s\b)[a-zçğıöşü]{1,5}\b")

# Eşik düşük tutulabiliyor çünkü buraya ancak METİNDE HİÇ TÜRKÇE İŞARET
# YOKKEN geliniyor: ne Türkçe harf, ne kesme-işareti eki, ne de yaygın
# Türkçe kelime. Öyle bir metnin Türkçe olma ihtimali zaten çok düşük.
_MIN_ENGLISH_EVIDENCE = 2


def is_probably_turkish(text: str | None) -> bool:
    """Metin Türkçe mi? Şüphede kalırsa TRUE döner.

    Bilinçli olarak izin verici: amacı Türkçe içeriği engellemek değil, açıkça
    İngilizce olanı yakalamak. Daha önce aceleyle yazılmış katı bir tespitçi
    "Final Fantasy XIV Online Switch 2'ye geliyor" gibi düpedüz Türkçe
    cümleleri İngilizce sanmıştı (Türkçe harf içermiyor diye) — bu yüzden
    kural tersine kuruldu: yalnızca İNGİLİZCE kanıtı varken ve Türkçe kanıtı
    hiç yokken False döner.

    Kısa/boş metinler True sayılır; onlar hakkında karar verecek kadar bilgi
    yok ve asıl korumayı `safe_display_title` yapıyor.
    """
    if not text:
        return True

    metin = text.strip()
    if len(metin) < 12:
        return True

    if any(ch in _TR_CHARS for ch in metin):
        return True
    if _TR_APOSTROPHE.search(metin):
        return True

    kelimeler = re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]+", metin.lower())
    if any(k in _TR_WORDS for k in kelimeler):
        return True

    return sum(1 for k in kelimeler if k in _EN_WORDS) < _MIN_ENGLISH_EVIDENCE


def safe_display_title(content: dict) -> str | None:
    """Görselde/hikayede gösterilecek başlığı döndür; güvenli değilse None.

    None dönerse çağıran taraf medyayı ÜRETMEMELİ. Kaynak başlığına
    düşmek bir seçenek değil — bu fonksiyonun varlık sebebi tam olarak
    o yedeği ortadan kaldırmak.
    """
    ozet = (content.get("summary_text") or "").strip()

    if not ozet:
        logger.warning(
            "Özet yok, medya üretilmiyor (İngilizce başlığa düşmemek için): "
            f"{(content.get('news_title') or '')[:60]}"
        )
        return None

    if not is_translated(content):
        logger.warning(
            "Özet kaynak başlığın kopyası (çevrilmemiş), medya üretilmiyor: "
            f"{ozet[:60]}"
        )
        return None

    # İKİNCİ SAVUNMA: kopya olmasa bile metnin kendisi İngilizceyse geçmesin.
    #
    # `is_translated` yalnızca "özet başlığın kopyası mı" diye bakıyor ve bu
    # KARAKTER KARŞILAŞTIRMASI kandırılabiliyor: şablon yolu ham İngilizce
    # başlığın başına emoji koyuyordu, emoji + boşluk karşılaştırmayı 2
    # karakter kaydırıyor ve metin "çevrilmiş" sayılıyordu. Canlı ölçümde 78
    # içerik kapıyı tam olarak bu yoldan geçmişti.
    #
    # Şablon yolu kaldırıldı ama kapının kendisi de kapatıldı: aynı sınıftan
    # yeni bir yol açılırsa (ör. başka bir yerde metnin başına işaret
    # eklenmesi) burada yakalanır. `is_probably_turkish` izin verici — yalnızca
    # AÇIKÇA İngilizce olanı eler, şüphede kalırsa geçirir.
    #
    # Not: `video_generator` bu kontrolü zaten yapıyordu, `story_generator`
    # yapmıyordu. Asimetriyi kapatmak yerine kuralı ORTAK KAPIYA taşımak
    # doğru yer — aksi halde her yeni çağıranda tekrar unutulur.
    if not is_probably_turkish(ozet):
        logger.warning(
            "Özet Türkçe görünmüyor, medya üretilmiyor: "
            f"{ozet[:60]}"
        )
        return None

    return ozet
