"""
İçerik İşleme Modülü
Toplanan haberleri AI ile özetler, sınıflandırır ve Instagram'a uygun hale getirir.
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    GEMINI_API_KEY, PROMPTS, CAPTION_STYLES, CONTENT_LANGUAGE, MIN_PROCESSING_SCORE,
    DAILY_POST_LIMIT, DAILY_STORY_LIMIT, DAILY_REELS_LIMIT,
    MEDIA_GENERATION_MULTIPLIER, DRAFT_BACKLOG_DAYS,
    REELS_CONFIG, REELS_SEGMENT_MAX_KELIME, CATEGORIES,
)
from src.database import Database
from src.relevance import calculate_relevance

logger = logging.getLogger(__name__)

# Gemini API — google-generativeai kütüphanesi Google tarafından kullanımdan
# kaldırıldı (bkz. FutureWarning), yerine google-genai kullanılıyor.
try:
    from google import genai
    from google.genai import errors as genai_errors
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai_errors = None
    logger.warning("google-genai kütüphanesi bulunamadı. AI özetleme devre dışı.")


# "gemini-flash-latest" (gemini-3.6-flash'a karşılık geliyor) ücretsiz
# planda günde SADECE 20 istekle sınırlı — günlük ~90 çağrılık gerçek
# kullanımımız bunun çok üzerinde olduğundan her gün kota anında tükenip
# İngilizce şablona düşülüyordu. "gemini-flash-lite-latest" test edildi
# (43 ardışık çağrının 42'si sorunsuz geçti, aynı 5s temposuyla) ve çok
# daha yüksek bir günlük kotaya sahip — asıl kalıcı çözüm bu model geçişi.
GEMINI_MODEL_NAME = "gemini-flash-lite-latest"


# Gemini'nin günlük/plan kotasının tükendiğini gösteren metin işaretleri.
# Hız sınırı ile kota aynı HTTP kodunu (429) döndürdüğü için ayırt etmenin
# tek doğrudan yolu mesaj metni. Yalnızca buna güvenilmiyor: mesaj değişirse
# _generate_with_retry içindeki "429 tekrar denemelerden sonra da sürüyor"
# kuralı ikinci bir emniyet katmanı olarak devreye giriyor.
_QUOTA_MARKERS = (
    "exceeded your current quota",
    "resource_exhausted",
    "quota_exceeded",
    "billing details",
)


def _is_quota_exhausted(error) -> bool:
    """Hata, dakikalık hız sınırı değil KOTA tükenmesi mi?"""
    if getattr(error, "code", None) != 429:
        return False
    text = f"{getattr(error, 'message', '')} {error}".lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


# =============================================
# ÜRETİM FRENİ — kural TEK yerde
#
# Tavan formülü hem burada hem `diagnostics` içinde ayrı ayrı yazılıydı.
# Biri değişirse `/durum` "fren açık" derken üretim çalışmaya devam eder
# (ya da tersi) — sessiz ayrışmaya açık bir kopyaydı.
# =============================================

def backlog_ceiling() -> int:
    """Onay kuyruğu kaç taslağı geçince üretim durur."""
    return (DAILY_POST_LIMIT + DAILY_STORY_LIMIT
            + DAILY_REELS_LIMIT) * DRAFT_BACKLOG_DAYS


def backlog_is_full(db) -> tuple[bool, int, int]:
    """(dolu_mu, kuyruk, tavan) — çağıranlar aynı sayıyı raporlayabilsin."""
    kuyruk = db.get_draft_backlog_count()
    tavan = backlog_ceiling()
    return kuyruk >= tavan, kuyruk, tavan


class ContentProcessor:
    """Haberleri Instagram içeriğine dönüştürür."""

    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.client = None
        # Gemini günlük kotası bu turda tükendi mi? Tükendiyse şablona
        # DÜŞÜLMEZ, üretim durdurulur — gerekçe için bkz. _quota_exhausted
        # kullanıldığı yerler ve _is_quota_exhausted().
        # Bayrağın TEK gerçek kaynağı bu zaman damgası; ayrı bir boolean
        # tutmak "bayrak True ama ne zamandan beri bilinmiyor" gibi geçersiz
        # bir durumu mümkün kılardı.
        self._quota_exhausted_at = None
        self._init_ai()

    @property
    def _quota_exhausted(self) -> bool:
        """Gemini günlük kotası tükenmiş durumda mı?

        Bayrak SÜRESİZ değildir. Eskiden yalnızca `__init__`'te False
        yapılıyordu ve `ContentProcessor` süreç ömrü boyunca yaşıyor
        (`Scheduler.__init__` bir kez üretiyor, `main.py` süreci sonsuza
        kadar çalıştırıyor). Yani kota bir kez dolduğunda sistem, kota
        ertesi gün yenilenmiş olmasına rağmen servis elle yeniden
        başlatılana dek HİÇ içerik üretmiyordu: her Gemini çağrısı anında
        None dönüyor, `process_all_news` ilk haberde `break` ediyor, üstüne
        her turda "kota tükendi" bildirimi gidiyordu. Çökme olmadığı için
        sağlık kontrolü de bir şey görmüyordu — sessiz ve kalıcı bir durma.

        Kota günlük yenilendiği için takvim günü değişince bayrak kalkar.
        Gemini'nin sıfırlama anı (Pasifik gece yarısı) yerel güne birebir
        oturmuyor; yanlış tarafa düşerse en fazla bir tur boşa gider,
        kalıcı susma riski ise tamamen kalkar.
        """
        if self._quota_exhausted_at is None:
            return False
        if datetime.now().date() > self._quota_exhausted_at.date():
            logger.info("🔄 Gemini kotası yeni güne geçti, üretim yeniden deneniyor.")
            self._quota_exhausted_at = None
            return False
        return True

    @_quota_exhausted.setter
    def _quota_exhausted(self, value: bool) -> None:
        self._quota_exhausted_at = datetime.now() if value else None

    @property
    def _quota_hit(self) -> bool:
        """Bayrak KONULMUŞ mu? Gün sıfırlamasını UYGULAMAZ.

        `_quota_exhausted` "şimdi denemeli miyim?" sorusunun cevabı ve
        okunduğunda gün değiştiyse bayrağı düşürüyor. Bu, GEÇMİŞİ sorgulayan
        yerlerde yanlış: `process_all_news` bir Gemini çağrısından SONRA
        "kota mı tükendi?" diye bakıyor; tur yerel gece yarısını geçmişse
        property sıfırlanıp False dönüyor, `break` atlanıyor ve haber
        HİÇ İÇERİK ÜRETİLMEDEN `mark_news_processed` ile işaretleniyor —
        yani bir daha asla denenmiyor. Tam olarak oradaki yorumun "KRİTİK"
        diye koruduğu değişmez.

        Gözlem yapan yerler bunu, karar veren yerler `_quota_exhausted`'ı
        kullanmalı.
        """
        return self._quota_exhausted_at is not None

    def _init_ai(self):
        """Gemini AI istemcisini başlat."""
        if GEMINI_AVAILABLE and GEMINI_API_KEY:
            try:
                self.client = genai.Client(api_key=GEMINI_API_KEY)
                logger.info("✅ Gemini AI istemcisi başlatıldı")
            except Exception as e:
                logger.error(f"Gemini başlatma hatası: {e}")
                self.client = None
        else:
            logger.warning("⚠️ Gemini API kullanılamıyor. Basit özetleme kullanılacak.")

    def _generate_with_retry(self, prompt: str, max_retries: int = 3) -> str | None:
        """
        Gemini'ye istek gönderir. Hız sınırına (429) ya da geçici bir sunucu
        hatasına (5xx — ör. "503 UNAVAILABLE: model şu an yoğun") takılırsa
        bir süre bekleyip tekrar dener; ikisi de kalıcı değil, bir süre sonra
        kendiliğinden düzelme eğiliminde. Tek bir pipeline turunda (30 haber ×
        başlık/özet/hikaye/reels için ~29 çağrı, art arda) bu hatalara kolay
        takılınıyor ve önceden sessizce şablona (çeviri yapılmadan)
        düşülüyordu. Çağrılar arası bekleme (5s → dakikada ~12 istek) hız
        sınırının altında kalmayı hedefler; yine de takılırsa artan bekleme
        ile (20s/40s/60s) tekrar denenir. Son deneme de başarısız olursa,
        hemen sıradaki habere geçip aynı pencereye bir kez daha çarpmamak
        için kısa bir soğuma beklemesi yapılır.
        """
        # Kota zaten tükendiyse hiç deneme: her çağrı 5s uyku + 120s'lik
        # anlamsız retry zinciri demek. 2026-08-03 denetiminde tek turda 56
        # kota hatası sayıldı; hepsi tam tur boyunca yeniden denenmişti.
        #
        # Bayrak gün değişince kendiliğinden kalkar (bkz. _quota_exhausted
        # property'si) — aksi halde kota yenilendiği hâlde sistem kalıcı
        # olarak susuyordu.
        if self._quota_exhausted:
            return None

        for attempt in range(max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=GEMINI_MODEL_NAME, contents=[prompt]
                )
                text = response.text.strip()
                time.sleep(5)  # sıradaki çağrı için hız sınırına yaklaşmamak adına bekleme
                return text
            except genai_errors.APIError as e:
                # Günlük kota tükenmesi ile dakikalık hız sınırı AYNI kodu (429)
                # döndürür ama tamamen farklı şeylerdir: hız sınırı saniyeler
                # içinde geçer, kota ertesi güne kadar geçmez. İkincisini
                # yeniden denemek hem boşuna hem de turu dakikalarca uzatır.
                if _is_quota_exhausted(e):
                    logger.error(
                        "🚫 Gemini günlük kotası tükendi. Bu turda AI üretimi "
                        "DURDURULDU — şablona düşülmüyor."
                    )
                    self._quota_exhausted = True
                    return None

                retryable = e.code == 429 or e.code >= 500
                if retryable and attempt < max_retries:
                    wait = 20 * (attempt + 1)
                    logger.warning(
                        f"Gemini geçici hata ({e.code}), {wait}s bekleniyor "
                        f"(deneme {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait)
                elif retryable:
                    # 429 için tüm denemeler tükendiyse: 120 saniye bekleyip
                    # hâlâ reddediliyorsak bu dakikalık bir dalgalanma değil,
                    # kotanın kendisidir. Mesaj metnine bakmadan da bu sonuca
                    # varılabilir — turun geri kalanını hamallık yapmadan kes.
                    if e.code == 429:
                        logger.error(
                            "🚫 Gemini 429 hatası tekrar denemelerden sonra da "
                            "sürüyor — kota tükenmiş kabul ediliyor, tur durduruluyor."
                        )
                        self._quota_exhausted = True
                        return None
                    logger.warning(
                        f"Gemini geçici hata ({e.code}) — tekrar denemeler tükendi, şablona düşülüyor."
                    )
                    time.sleep(10)  # aynı pencerede sıradaki habere hemen çarpmamak için
                    return None
                else:
                    logger.warning(f"Gemini isteği hatası: {e}")
                    return None
            except Exception as e:
                logger.warning(f"Gemini isteği hatası: {e}")
                return None
        return None

    def process_all_news(self) -> dict:
        """Tüm işlenmemiş haberleri işle."""
        logger.info("🔄 İçerik işleme başlıyor...")
        
        stats = {"processed": 0, "posts": 0, "stories": 0, "reels_candidates": 0,
                 "quota_exhausted": False, "skipped_for_quota": 0}

        # İşlenecek haber sayısı GÜNLÜK bir bütçedir — tur başına değil.
        #
        # Eski hali `max(POST*MULT, STORY*MULT)` idi ve "günlük" diye
        # tasarlanmıştı, ama her turda YENİDEN uygulanıyordu. Boru hattı
        # günde birkaç kez çalıştığı için (zamanlanmış tur + yeniden başlatma
        # telafisi + elle çalıştırmalar) gerçekte kat kat fazlası üretildi.
        #
        # Ölçüm (3-5 Ağustos 2026): 251 içerik üretildi, 58'ine karar verildi,
        # birikme +193. Günlük yayın kapasitesi ise yalnızca 8 (2 gönderi +
        # 5 hikaye + 1 reels) — yani üretim kapasitenin 8-11 katıydı. Fazlası
        # Gemini kotası, görsel üretimi, disk ve kullanıcının dikkati olarak
        # harcanıyordu; onay kuyruğunda 4 günlük içerik birikmişti.
        #
        # Artık bugün zaten işlenmiş haberler düşülüyor: kaç kez çalışırsa
        # çalışsın günlük toplam sabit kalıyor.
        gunluk_butce = max(
            DAILY_POST_LIMIT * MEDIA_GENERATION_MULTIPLIER,
            DAILY_STORY_LIMIT * MEDIA_GENERATION_MULTIPLIER,
        )
        bugun_islenen = self.db.get_news_processed_today_count()
        limit = max(0, gunluk_butce - bugun_islenen)

        if limit == 0:
            logger.info(
                f"ℹ️ Günlük işleme bütçesi doldu ({bugun_islenen}/{gunluk_butce} haber). "
                f"Yayın kapasitesi günde {DAILY_POST_LIMIT + DAILY_STORY_LIMIT + DAILY_REELS_LIMIT} "
                f"içerik; fazlası onay kuyruğunda birikiyor."
            )
            return stats

        # ÜRETİM FRENİ. Günlük bütçe tek başına yetmiyordu: bütçe her gün
        # sıfırlanıyor ama kuyruk sıfırlanmıyor. Onay kuyruğunda zaten
        # yayınlanamayacak kadar içerik varken üretmeye devam etmek Gemini
        # kotasını, görsel indirmelerini ve diski boşa harcıyor.
        #
        # Ölçüm (7 Ağustos 2026): 466 onay bekleyen taslak, en eskisi 1
        # Ağustos'tan. Üretim günde ~30 içerik; Telegram'a bildirilen 8;
        # yayınlanan 8. Kuyruk günde ~22 büyüyordu ve hiç erimiyordu.
        #
        # Fren kendiliğinden açılır: kuyruk eridikçe (yayın + bayat taslak
        # emekliliği) üretim yeniden başlar. Yani sistem susmuyor, kendi
        # kapasitesine göre üretiyor.
        dolu, kuyruk, kuyruk_tavani = backlog_is_full(self.db)
        if dolu:
            logger.info(
                f"⏸️ Üretim duraklatıldı: onay kuyruğunda {kuyruk} taslak var "
                f"(tavan {kuyruk_tavani} = {DRAFT_BACKLOG_DAYS} günlük yayın "
                f"kapasitesi). Kuyruk erimeden yeni içerik üretmek boşa "
                f"harcama olur."
            )
            stats["paused_for_backlog"] = kuyruk
            return stats

        unprocessed = self.db.get_unprocessed_news(limit=limit)
        
        if not unprocessed:
            logger.info("ℹ️ İşlenecek yeni haber bulunamadı.")
            return stats

        logger.info(f"📋 {len(unprocessed)} haber işlenecek...")

        for news in unprocessed:
            try:
                # 1. Haberi puanla (relevance score)
                score = self._calculate_relevance(news)

                # Düşük skorlu haberler için AI/caption üretimini tamamen atla
                # (Gemini quota tasarrufu — bu haberler zaten auto-publish eşiğini geçemeyecek)
                if score < MIN_PROCESSING_SCORE:
                    self.db.mark_news_processed(news["id"], relevance_score=score)
                    stats["processed"] += 1
                    continue

                # 2. Feed gönderisi için içerik oluştur
                post_content = self._create_post_content(news)

                # Kota tükendiyse turu burada kes. Kritik: haber
                # mark_news_processed ile İŞARETLENMEDEN çıkılıyor — aksi
                # halde kota yüzünden hiç içerik üretilemeyen haberler
                # "işlendi" sayılıp bir daha asla denenmez, yani kota
                # yenilendiğinde geri gelmezlerdi.
                # GÖZLEM — `_quota_hit` kullanılıyor, `_quota_exhausted`
                # değil: bu satır bir karar değil, yukarıdaki çağrıda ne
                # olduğunun sorgusu. Sıfırlayan property okunsaydı, tur
                # gece yarısını geçtiğinde bayrak düşer, `break` atlanır ve
                # haber içeriksiz "işlendi" damgası yerdi.
                if self._quota_hit:
                    stats["quota_exhausted"] = True
                    stats["skipped_for_quota"] = len(unprocessed) - stats["processed"]
                    logger.error(
                        f"🚫 Gemini kotası tükendi — {stats['skipped_for_quota']} haber "
                        f"işlenmeden bırakıldı (kota yenilenince yeniden denenecek). "
                        f"Şablon içerik ÜRETİLMEDİ."
                    )
                    break

                if post_content:
                    self.db.add_content(
                        news_id=news["id"],
                        content_type="post",
                        caption=post_content["caption"],
                        hashtags=post_content["hashtags"],
                        summary_text=post_content["summary"],
                        list_items=post_content.get("list_content")
                    )
                    stats["posts"] += 1

                # 3. Hikaye için içerik oluştur
                story_text = self._create_story_content(news)
                if story_text:
                    self.db.add_content(
                        news_id=news["id"],
                        content_type="story",
                        summary_text=story_text
                    )
                    stats["stories"] += 1

                # 4. Haberi işlenmiş olarak işaretle
                self.db.mark_news_processed(news["id"], relevance_score=score)
                stats["processed"] += 1

            except Exception as e:
                logger.error(f"Haber işleme hatası [ID={news['id']}]: {e}")
                continue

        logger.info(f"✅ İşleme tamamlandı: {stats['processed']} haber → "
                    f"{stats['posts']} gönderi, {stats['stories']} hikaye")
        
        return stats

    def create_reels_content(self, category: str = None, count: int = 5) -> dict | None:
        """Reels videosu için YAYINLANMIŞ içeriklerden senaryo oluştur.

        Eskiden `get_unused_news` kullanılıyordu — yani reels, hesapta hiç
        paylaşılmamış haberleri anlatıyordu ve izleyici videoda gördüğünü
        profilde bulamıyordu (5 Ağustos 2026 kullanıcı bildirimi). Ayrıca o
        yol ham İngilizce `news_items.title` taşıyordu; artık gelen `title`
        alanı üretilmiş Türkçe özet.
        """
        news_items = self.db.get_published_for_reels(category=category, limit=count)

        if len(news_items) < 2:
            logger.warning(
                "Reels için yeterli YAYINLANMIŞ içerik yok (en az 2 gerekli). "
                "Reels, paylaşılan içeriklerin özeti olduğu için önce normal "
                "gönderilerin yayınlanması gerekiyor."
            )
            return None

        # Reels senaryosu oluştur
        script = self._create_reels_script(news_items)
        if not script:
            return None

        # SIRA KRİTİK: `news_ids` add_content'ten ÖNCE konmalı. `add_content`
        # scripti O ANDA json.dumps ile serileştiriyor; sonradan sözlüğe
        # eklenen alan yalnızca BELLEKTE kalır, veritabanına hiç yazılmaz.
        # İlk denemede tam olarak bu oldu: alan sonradan ekleniyordu, dolayısıyla
        # `scheduler` scripti veritabanından okuduğunda `news_ids` boştu,
        # `mark_news_used` hiç çağrılmıyordu ve eklenen `is_used = 0` filtresi
        # hiçbir şeyi elemiyordu — düzeltme çalışıyor GÖRÜNÜYOR ama çalışmıyordu.
        # Testler de kaçırdı çünkü BELLEKTEKİ dönüş değerini denetliyorlardı.
        script["news_ids"] = [n["id"] for n in news_items]

        # İlk haberin ID'sini ana referans olarak kullan
        content_id = self.db.add_content(
            news_id=news_items[0]["id"],
            content_type="reels",
            reels_script=script,
            caption=script.get("caption", ""),
            hashtags=script.get("hashtags", [])
        )

        # HABERLER BURADA YAKILMIYOR. Eskiden burada hepsi `mark_news_used`
        # ile işaretleniyordu — taslak daha kullanıcıya gösterilmeden.
        #
        # Ölçüm (7 Ağustos 2026): 33 reels taslağı üretilmiş, HİÇBİRİ
        # yayınlanmamış, ama 120 haber yakılmıştı. Yani yakılan haberlerin
        # %100'ü hiç yayınlanmayan taslaklar için harcandı. Reels 3 günde
        # bayatlayıp `rejected` olduğunda da haberler geri gelmiyordu.
        # `is_used = 0` filtresi eklenince (aynı gün) bu, havuzu kalıcı
        # olarak eriten bir sızıntıya dönüştü: tüketim günde 10-40 haber,
        # üretim ise günde 1-5.
        #
        # Artık işaretleme, video GERÇEKTEN kullanıcıya ulaştığında yapılıyor
        # (bkz. scheduler.generate_all_media); `news_ids` yukarıda, kayıttan
        # ÖNCE konuyor ki o an hangi haberlerin yakılacağı bilinsin.

        logger.info(f"🎬 Reels senaryosu oluşturuldu: {len(news_items)} haber birleştirildi")
        return {"content_id": content_id, "script": script, "news_count": len(news_items)}


    def _create_post_content(self, news: dict) -> dict | None:
        """Feed gönderisi için caption ve hashtag oluştur.

        Gemini yoksa üretim YAPILMAZ. Buradaki şablon `summary` alanına ham
        `news["title"]` koyuyordu — yani KAYNAK dilindeki başlığı. Ölçüm
        (8 Ağustos 2026): havuzdaki 200 haberin 153'ü (%76) Türkçe değil.

        `_fallback_generate_story` ve `_fallback_generate_reels` aynı
        gerekçeyle kaldırılmıştı; bu üçüncüsüydü.

        Dil kapısı bunu zaten yakalıyordu, yani İngilizce bir gönderi
        yayınlanmıyordu — ama üretilen satır kuyruğa giriyor, üretim frenini
        besliyor ve sonunda atılıyordu. Son 14 günde özet taşıyan 883
        içeriğin 194'ü (%22) kapıdan geçemedi; 35'i için görsel bile
        üretilmişti. Üretmemek, üretip atmaktan ucuz.
        """
        if not self.client or self._quota_exhausted:
            return None
        return self._ai_generate_post(news)

    def _create_story_content(self, news: dict) -> str | None:
        """Hikaye için kısa metin oluştur."""
        if self.client:
            if self._quota_exhausted:
                return None
            return self._ai_generate_story(news)
        # Gemini yapılandırılmamışsa hikaye ÜRETİLMEZ. Şablon yolu ham
        # İngilizce başlığı emoji ile sarıp dil kapısından geçiriyordu.
        return None

    def _create_reels_script(self, news_items: list[dict]) -> dict | None:
        """Reels için senaryo oluştur. Gemini yoksa üretim YAPILMAZ.

        Eskiden burada bir fallback vardı ve ham `news["title"]` alanını
        doğrudan segment metni yapıyordu. O başlıklar KAYNAK dilinde:
        ölçüm (8 Ağustos 2026) havuzdaki 200 haberin 153'ünün (%76)
        Türkçe olmadığını gösterdi. Yani fallback her çalıştığında
        İngilizce yazılı bir reels üretiyordu.

        Aynı gerekçeyle `_fallback_generate_story` de kaldırılmıştı; bu
        onun ikizi ve aynı sızıntının reels tarafıydı.

        Üretmemek kayıp değil: haberler taslak üretilirken değil, video
        kullanıcıya ULAŞTIĞINDA yakılıyor (bkz. create_reels_content),
        dolayısıyla havuz olduğu gibi kalıyor ve bir sonraki tur yeniden
        deniyor.
        """
        if not self.client or self._quota_exhausted:
            return None
        return self._ai_generate_reels(news_items)

    # =============================================
    # AI İLE İÇERİK ÜRETME (Gemini)
    # =============================================

    def _ai_generate_post(self, news: dict) -> dict | None:
        """Gemini AI ile feed gönderisi içeriği oluştur."""
        # Her caption'ın aynı kalıba düşmemesi için içeriğe göre deterministik
        # bir yazım tarzı seçilir (diğer rotasyonlarla — layout/foto seçimi —
        # aynı seed deseni; rastgele değil, aynı haber yeniden işlenince aynı
        # tarz seçilir).
        seed = news.get("id") if news.get("id") is not None else abs(hash(news["title"]))
        style = CAPTION_STYLES[seed % len(CAPTION_STYLES)]

        prompt = PROMPTS["generate_caption"].format(
            category="Yapay Zeka" if news["category"] == "ai" else "Oyun Dünyası",
            title=news["title"],
            description=news.get("description") or "",
            source=news.get("source_name", ""),
            style=style
        )

        text = self._generate_with_retry(prompt)
        if text:
            try:
                result = self._parse_caption_response(text)
                if result and result.get("caption"):
                    # Özet de oluştur
                    summary = self._ai_summarize(news)

                    # Özet üretilemezse içeriği HİÇ üretme.
                    #
                    # Eskiden `summary or news["title"]` yazıyordu, yani özet
                    # adımı patladığında HAM İNGİLİZCE haber başlığına
                    # düşülüyordu. summary_text görselin üzerine basılan manşet
                    # ve Telegram'da görünen satır olduğu için, Türkçe bir
                    # hesaba İngilizce manşetli gönderiler sızıyordu — 4 Ağustos
                    # 2026'da onay kuyruğunda 6 tane tespit edildi
                    # ("Final Fantasy XIV Launches on Nintendo Switch 2...").
                    #
                    # Caption başarılı olduğu için içerik "üretildi" sayılıyor
                    # ve sessizce akışa giriyordu. Kota politikasıyla aynı
                    # ilke: eksik yayınlamak, yanlış dilde yayınlamaktan iyidir.
                    if not summary:
                        logger.warning(
                            f"Özet üretilemedi, gönderi atlanıyor (İngilizce "
                            f"başlığa düşmemek için): {news['title'][:60]}"
                        )
                        return None

                    return {
                        "caption": result["caption"],
                        "hashtags": result.get("hashtags") or self._default_hashtags(news["category"]),
                        "summary": summary,
                        "list_content": self._ai_detect_list_content(news),
                    }
            except Exception as e:
                logger.warning(f"AI post üretme hatası: {e}")

        # Fallback YOK — bkz. _create_post_content. Yanıt ayrışmazsa gönderi
        # üretilmez; ham İngilizce başlığı özet yapmaktansa bu haberi
        # atlamak doğru. Haber yakılmıyor, bir sonraki tur yeniden denenir.
        logger.warning(
            f"Gönderi metni üretilemedi, haber atlanıyor: {news['title'][:60]}"
        )
        return None

    # Haberin bir LİSTE olabileceğine işaret eden kalıplar. Hem Türkçe hem
    # İngilizce: başlık/açıklama ham kaynak metni (İngilizce) olabiliyor.
    _LISTE_SINYALI = re.compile(
        r"(?:\b(?:top|best|en\s+iyi|en\s+güzel|tüm|all)\s+\d{1,2}\b"
        r"|\b\d{1,2}\s+(?:oyun|şey|neden|ipucu|özellik|madde|film|dizi|"
        r"games?|things?|reasons?|tips?|features?|ways?)\b"
        r"|\blist(?:e|esi)\b|\bsıralama\b|\bderleme\b)",
        re.IGNORECASE,
    )

    @classmethod
    def _liste_sinyali_var(cls, news: dict) -> bool:
        """Başlık/açıklamada liste kalıbı var mı? (Gemini çağrısından önce.)"""
        metin = f"{news.get('title') or ''} {news.get('description') or ''}"
        return bool(cls._LISTE_SINYALI.search(metin))

    def _ai_detect_list_content(self, news: dict) -> dict | None:
        """
        Haber, isimlendirilmiş en az 2 öge içeren bir LİSTE mi (ör. "Ağustos'un
        en iyi 5 oyunu") tespit eder. Öyleyse generate_all_media() tekil görsel
        yerine kaydırmalı (carousel) görsel üretir — kullanıcı geri bildirimi:
        "daha fazla detay vermek gerekirse feed içerisinde kaydırmalı şekilde
        içerikler yapılabilmeli". Liste değilse (çoğu haber) None döner ve
        normal tekil görsel akışı hiç etkilenmez.
        """
        # UCUZ ÖN FİLTRE — çağrıyı yalnızca liste SİNYALİ varsa yap.
        #
        # Ölçüm (8 Ağustos 2026): 279 üretilmiş içeriğin 0'ında `list_items`
        # doluydu, yani bu çağrı her haber için yapılıp hiç sonuç vermemişti.
        # Sebebinin bir kısmı ayrıştırıcıydı (düzeltildi) ama asıl mesele
        # şu: haberlerin ezici çoğunluğu zaten liste değil. Ücretsiz Gemini
        # kotası dar olduğu için haber başına 3 çağrının biri boşa gidiyordu.
        #
        # Filtre metinden, İngilizce kaynak başlığı da dahil, sayı+liste
        # kalıbı arıyor ("top 5", "en iyi 10 oyun", "5 things"). Yanlış
        # negatif riski kabul: kaçırılan liste yalnızca tekil görsel olur,
        # içerik kaybolmaz.
        if not self._liste_sinyali_var(news):
            return None

        prompt = PROMPTS["detect_list_content"].format(
            title=news["title"],
            description=news.get("description") or ""
        )
        text = self._generate_with_retry(prompt)
        if not text:
            return None
        try:
            return self._parse_list_content_response(text)
        except Exception as e:
            logger.warning(f"Liste içerik tespiti hatası: {e}")
            return None

    def _ai_generate_story(self, news: dict) -> str | None:
        """Gemini AI ile hikaye metni oluştur.

        Gerçek kalite kusuru (21 Ağustos 2026): bu metin, post manşetiyle
        (`_ai_summarize`) TAM AYNI görsel rolü oynuyor — görselin üzerine
        basılan TEK başlık — ama kendi bespoke "140 karakter, kelime
        sınırında kes" mantığını kullanıyordu, `_normalize_manset`'in 60/100
        karakter hedefinden habersiz. Canlı ölçüm: aynı turda üretilen 6
        story başlığının HEPSİ 83-107 karakterdi (post'lar 37-57 karakterle
        hedefin içindeyken). `_draw_story_title`'da posttaki gibi otomatik
        font küçültme de yok — sadece 5 satırda kesiliyor — yani uzun
        manşet posttakinden daha kötü bir okunabilirlik defektine yol açar.
        Artık AYNI normalizasyondan geçiyor.
        """
        prompt = PROMPTS["generate_story_text"].format(
            title=news["title"],
            description=news.get("description") or ""
        )
        return self._normalize_manset(self._generate_with_retry(prompt))

    @staticmethod
    def _uyar_uzun_segmentler(segments: list[str]) -> int:
        """Ekranda YETİŞMEYECEK segmentleri günlüğe yazar, sayısını döner.

        Kesmiyor: kesme cümleyi kelime ortasından bölerdi ve sonuç, uzun
        cümleden daha kötü olurdu. Amaç ÖLÇÜLEBİLİRLİK — prompt değişince
        aşım oranının düşüp düşmediği günlükten görülebilsin. Aşımın kendisi
        bir üretim hatası değil, kalite sinyali.
        """
        asan = [s for s in segments
                if len(s.split()) > REELS_SEGMENT_MAX_KELIME]
        if asan:
            logger.warning(
                f"Reels: {len(asan)}/{len(segments)} segment "
                f"{REELS_SEGMENT_MAX_KELIME} kelime bütçesini aşıyor "
                f"(slayt {REELS_CONFIG['duration_per_slide']} sn ekranda "
                f"kalıyor). En uzunu: "
                f"{max(asan, key=lambda s: len(s.split()))[:80]}"
            )
        return len(asan)

    def _ai_generate_reels(self, news_items: list[dict]) -> dict | None:
        """Gemini AI ile Reels senaryosu oluştur."""
        news_list = "\n".join([
            f"- [{n['category'].upper()}] {n['title']} ({n.get('source_name', 'Kaynak yok')})"
            for n in news_items
        ])
        prompt = PROMPTS["generate_reels_script"].format(
            news_list=news_list,
            slayt_saniye=REELS_CONFIG["duration_per_slide"],
            segment_kelime=REELS_SEGMENT_MAX_KELIME,
        )

        text = self._generate_with_retry(prompt)
        if text:
            try:
                result = self._parse_reels_script_response(text)
                if result and result.get("segments"):
                    self._uyar_uzun_segmentler(result["segments"])
                    # CAPTION, VİDEONUN GERÇEKTEN ANLATTIĞI HABERLERDEN.
                    #
                    # Model her zaman istenen sayıda segment üretmiyor.
                    # Canlı örnek (8 Ağustos 2026): 5 haber verildi, 4
                    # segment döndü — video 4 haber anlatırken caption 5
                    # madde listeliyordu. `segment_indices` hangi haberlerin
                    # gerçekten anlatıldığını söylüyor; caption onlardan
                    # kuruluyor.
                    #
                    # Zincir listeleri (image_urls/news_urls/game_titles)
                    # BİLEREK tam uzunlukta bırakılıyor: video üretimi
                    # onlara `segment_indices` ile, yani ORİJİNAL konuma
                    # göre erişiyor. Kısaltmak eşlemeyi kaydırırdı.
                    kapsanan = [news_items[h]
                                for h in (result.get("segment_indices") or [])
                                if h < len(news_items)] or news_items
                    if len(kapsanan) != len(news_items):
                        logger.warning(
                            f"Reels: {len(news_items)} haber verildi, "
                            f"{len(kapsanan)} segment üretildi — caption "
                            "yalnızca anlatılanları listeliyor."
                        )
                    category = news_items[0]["category"]
                    result["caption"] = self._build_reels_caption(kapsanan)
                    result["hashtags"] = self._default_hashtags(category, is_reels=True)
                    result["news_titles"] = [n["title"] for n in news_items]
                    result["image_urls"] = [n.get("image_url") for n in news_items]
                    result["news_urls"] = [n.get("news_url") for n in news_items]
                    result["game_titles"] = [n.get("news_title") for n in news_items]
                    return result
            except Exception as e:
                logger.warning(f"AI reels üretme hatası: {e}")

        # Fallback YOK — bkz. _create_reels_script. Yanıt ayrışmazsa reels
        # üretilmez; ham İngilizce başlıkları slayta basmaktansa bu turu
        # atlamak doğru.
        logger.warning("Reels senaryosu üretilemedi, bu tur atlanıyor.")
        return None

    # Manşet uzunluk hedefi ve tavanı. Prompt "en fazla 60 KARAKTER, sert bir
    # sınır" diyor ve gerekçesini de yazıyor, ama model uymuyor: canlı ölçümde
    # (8 Ağustos 2026, 329 gönderi özeti) ortalama 101, medyan 111, maks 220
    # karakter ve %58'i sınırı aşıyordu. Prompt iyi yazılmış; sorun uyum.
    _MANSET_HEDEF = 60
    _MANSET_TAVAN = 100   # bunun üstü görselde 28px'e kadar küçülüyor

    @staticmethod
    def _ilk_cumle(metin: str) -> str:
        """İlk cümle sınırında kes — manşet için doğal ve kayıpsız kısaltma.

        Model tipik olarak önce iyi bir manşet yazıp ardından caption'a ait
        detayı ekliyor. Ölçüm: 190 uzun özetin 45'i yalnızca ilk cümleye
        indirilerek 60 karakterin altına giriyor (ortalama 151 → 42) ve
        çıkan metinler gerçek manşet gibi okunuyor:
        "iPhone 15'te aşırı ısınma sorunu!" (154 → 33)
        """
        m = re.search(r"^(.{10,}?[.!?])(?:\s|$)", metin.strip())
        return m.group(1).strip() if m else metin.strip()

    @classmethod
    def _normalize_manset(cls, ham: str | None) -> str | None:
        """Model çıktısını görsele basılabilir bir manşete indirger.

        Eskiden `_ai_summarize` çıktıyı HAM döndürüyordu: ne tırnak soyma, ne
        "Manşet:" öneki temizliği, ne uzunluk. Yani promptun önlemek için
        yazıldığı defekt kod tarafında serbest bırakılmıştı.

        Kesme SERT DEĞİL, kademeli: önce süsleme soyulur, sonra ilk cümle
        alınır (doğal sınır), yalnızca hâlâ tavanı aşıyorsa kelime sınırında
        kırpılır. Sert `None` dönmek gönderilerin %58'ini bloklardı;
        körlemesine kırpmak da manşetin çarpıcı kısmını kesebilir.
        """
        if not ham:
            return None

        metin = ham.strip()
        # Modelin sık eklediği süslemeler: markdown, tırnak, "Manşet:" öneki.
        metin = re.sub(r"^\s*(?:manşet|başlık|headline)\s*:\s*", "", metin,
                       flags=re.IGNORECASE)
        metin = metin.strip().strip("*").strip()
        if len(metin) >= 2 and metin[0] in "\"'“‘" and metin[-1] in "\"'”’":
            metin = metin[1:-1].strip()
        if not metin:
            return None

        if len(metin) > cls._MANSET_HEDEF:
            metin = cls._ilk_cumle(metin)

        if len(metin) > cls._MANSET_TAVAN:
            kirpik = metin[:cls._MANSET_TAVAN]
            bosluk = kirpik.rfind(" ")
            if bosluk > cls._MANSET_TAVAN // 2:
                kirpik = kirpik[:bosluk]
            metin = kirpik.rstrip(" ,;:-") + "…"

        if len(metin) > cls._MANSET_HEDEF:
            logger.info(
                f"Manşet hedefin üstünde ({len(metin)}>{cls._MANSET_HEDEF}): "
                f"{metin[:70]}"
            )
        return metin or None

    def _ai_summarize(self, news: dict) -> str | None:
        """Haber başlığını AI ile kısa ve etkileyici şekilde özetle."""
        prompt = PROMPTS["summarize_news"].format(
            title=news["title"],
            description=news.get("description") or ""
        )
        return self._normalize_manset(self._generate_with_retry(prompt))

    # =============================================
    # ŞABLON (FALLBACK) İÇERİK ÜRETME — ARTIK YOK
    #
    # Üç şablon üreticisi de kaldırıldı; üçü de aynı hatayı yapıyordu:
    # ham `news["title"]` alanını kullanıcıya gösterilecek metne koyuyordu.
    # O başlık KAYNAK dilinde. Ölçüm (8 Ağustos 2026): havuzdaki 200
    # haberin 153'ü (%76) Türkçe değil.
    #
    # Gemini yoksa ya da yanıt ayrışmazsa artık hiçbir şey üretilmiyor.
    # Haber yakılmıyor, bir sonraki tur yeniden deniyor.
    # =============================================

    # `_fallback_generate_post` KALDIRILDI (8 Ağustos 2026).
    #
    # `summary` alanına ham İngilizce başlığı koyuyordu. Dil kapısı bunu
    # yakalıyordu — yani İngilizce bir gönderi YAYINLANMIYORDU — ama üretilen
    # satır onay kuyruğuna giriyor, üretim frenini besliyor ve sonunda
    # atılıyordu.
    #
    # Canlı ölçüm: son 14 günde özet taşıyan 883 içeriğin 194'ü (%22) dil
    # kapısından geçemedi (116'sı "özet başlığın kopyası", 78'i "Türkçe
    # görünmüyor"); 35'i için görsel bile üretilmişti. Üretmemek, üretip
    # atmaktan ucuz.

    # `_fallback_generate_story` KALDIRILDI (8 Ağustos 2026).
    #
    # `f"{emoji} {title}"` üretiyordu — yani HAM İNGİLİZCE kaynak başlığının
    # başına bir emoji koyuyordu. Emoji + boşluk, `is_translated`'ın yaptığı
    # "özet ilk 60 karakterde başlığın kopyası mı" karşılaştırmasını 2
    # karakter kaydırıyor ve metin dil kapısından GEÇİYORDU. Emoji olmasa
    # aynı metin doğru şekilde bloklanıyordu.
    #
    # Canlı ölçüm: 78 içerik kapıyı bu yolla geçmişti; hepsi hikaye, hepsi
    # emoji önekli, hepsi kullanıcı tarafından elle reddedilmişti. Yani
    # sistem İngilizce hikaye üretip onay kuyruğuna koyuyor, kullanıcı tek
    # tek eliyordu — "İngilizce içerik" şikayetinin doğrudan kaynağı.
    #
    # `_ai_generate_post` özet üretemediğinde `None` dönüp gönderiyi
    # atlıyor; hikaye yolu da artık aynı şekilde davranıyor. Kalitesiz
    # yayınlamaktansa eksik yayınlamak bu projenin yerleşik kuralı.

    # =============================================
    # YARDIMCI METOTLAR
    # =============================================

    def _calculate_relevance(self, news: dict) -> float:
        """Haberin önem/ilgi skorunu hesapla (0.0 - 1.0).

        Asıl mantık `src/relevance.py`'ye taşındı: puan artık yalnızca içerik
        üretilirken değil, haber TOPLANIRKEN de hesaplanıyor ve hangi haberin
        işleneceğini o belirliyor. `Database` de aynı fonksiyonu kullanabilsin
        diye ortak bir modüle çıkarıldı (buradan import etmek dairesel olurdu).
        """
        return calculate_relevance(news)

    # Caption'daki liste satırı için üst sınır. Instagram 2200 karaktere izin
    # veriyor; 5 maddelik bir liste bunun çok altında kalıyor, dolayısıyla
    # sınır dar tutulmamalı. Eskiden 60 karakterdi ve KELİME ORTASINDAN
    # kesiyordu: canlı caption'larda "hız odakl...", "köpekbalığı geril...",
    # "ilk res..." gibi satırlar vardı. Sınır artık geniş ve kesme kelime
    # sınırında yapılıyor.
    _CAPTION_SATIR_SINIRI = 140

    @classmethod
    def _kisalt(cls, metin: str, sinir: int | None = None) -> str:
        """Kelime sınırında kısalt; kelime ortasından kesme."""
        sinir = sinir or cls._CAPTION_SATIR_SINIRI
        metin = (metin or "").strip()
        if len(metin) <= sinir:
            return metin
        kesik = metin[:sinir]
        bosluk = kesik.rfind(" ")
        if bosluk > sinir * 0.6:
            kesik = kesik[:bosluk]
        return kesik.rstrip(" ,;:-") + "…"

    def _build_reels_caption(self, news_items: list[dict]) -> str:
        """Reels için caption oluştur."""
        category = news_items[0]["category"]
        # Emoji ve kısa ad config.CATEGORIES'ten — video/story ile aynı kaynak.
        kat = CATEGORIES.get(category, CATEGORIES["gaming"])
        emoji = kat["emoji"]
        category_name = kat["caption_name"]

        # "Günün ... Haberleri" DEĞİL: reels havuzu son 7 günde yayınlanmış
        # gönderilerden derleniyor (bkz. get_published_for_reels), tek bir
        # güne ait değil. Giriş slaytındaki aynı iddia da bu yüzden
        # kaldırıldı — caption ile ekranın tutarsız olması daha da kötüydü.
        caption = f"{emoji} Öne Çıkan {category_name} Haberleri\n\n"
        for i, news in enumerate(news_items, 1):
            caption += f"{i}. {self._kisalt(news['title'])}\n"

        caption += "\n💬 Hangisi en çok ilgini çekti? Yorumlara yaz!\n"
        caption += "📲 Kaydet & arkadaşınla paylaş!"
        
        return caption

    # Etkileşim yemi / genel etiketler: Instagram'da erişime katkısı yok,
    # hesabı spam gibi gösteriyor. Modelin prompt'a rağmen ürettiği bu tür
    # etiketler ayıklanır.
    _BANNED_HASHTAGS = {
        "kesfet", "keşfet", "takipet", "takipetmeyiunutma", "viral",
        "trending", "instagood", "explore", "explorepage", "fyp",
        "followme", "like4like", "takip", "instadaily",
    }
    MAX_HASHTAGS = 5

    @classmethod
    def _filter_hashtags(cls, hashtags: list[str]) -> list[str]:
        """Yasaklı/genel etiketleri atar, tekrarları temizler, 5 ile sınırlar."""
        seen: set[str] = set()
        cleaned: list[str] = []
        for tag in hashtags:
            key = tag.lstrip("#").lower()
            if not key or key in cls._BANNED_HASHTAGS or key in seen:
                continue
            seen.add(key)
            cleaned.append(tag)
            if len(cleaned) >= cls.MAX_HASHTAGS:
                break
        return cleaned

    @staticmethod
    def _default_hashtags(category: str, is_reels: bool = False) -> list[str]:
        """
        Gemini etiket üretemezse kullanılan yedek liste.

        Liste config.CATEGORIES'ten gelir; neden kısa tutulduğu orada
        yazıyor. Bunlar yalnızca YEDEK; asıl etiketler habere özel olacak
        şekilde Gemini tarafından üretiliyor (bkz. generate_caption).
        """
        # Bilinmeyen kategori eskiden de "gaming" dalına düşüyordu; korunuyor.
        # list() kopyası: aşağıdaki append config'deki listeyi değiştirmesin.
        tags = list(CATEGORIES.get(category, CATEGORIES["gaming"])["hashtags"])

        if is_reels:
            tags.append("#reels")

        return tags

    @staticmethod
    def _parse_caption_response(text: str) -> dict | None:
        """
        Gemini'nin "CAPTION: ...\\nHASHTAGS: #a #b" düz metin formatındaki
        yanıtını ayrıştırır. Daha önce JSON formatı kullanılıyordu ama Gemini
        caption metni içinde (alıntı/apostrof gibi) kaçışsız tırnak üretince
        sık sık bozuluyor, içerik sessizce İngilizce şablona düşüyordu. Sabit
        ayraçlı düz metin formatı bu sınıf hatayı tamamen ortadan kaldırır.
        """
        # İŞARETÇİLER SATIR BAŞINA BAĞLI ve IGNORECASE YOK.
        #
        # Eskiden `re.search(r"CAPTION:...", re.IGNORECASE)` kullanılıyordu.
        # Model tipik bir nezaket öneki eklediğinde —
        #     Tabii! İşte istediğiniz caption:  ...ardından CAPTION: satırı
        # — `re.search` İLK eşleşmeyi "istediğiniz caption:" içinde buluyor ve
        # caption'ın başına "CAPTION:" işaretçisi geçiyordu. Yani Instagram'da
        # yayınlanan metnin İLK SATIRI "CAPTION:" oluyordu. Markdown kalın
        # (`**CAPTION:**`) durumunda da caption `**` ile başlayıp bitiyordu.
        # İlk satır, Instagram'ın akışta gösterdiği tek satır olduğu için bu
        # doğrudan görünür bir defekt.
        match = re.search(
            r"^[ 	]*\**[ 	]*CAPTION[ 	]*\**[ 	]*:[ 	]*(.*?)"
            r"^[ 	]*\**[ 	]*HASHTAGS[ 	]*\**[ 	]*:[ 	]*(.*)",
            text, re.DOTALL | re.MULTILINE)
        if match:
            caption = match.group(1).strip()
            hashtags = re.findall(r"#\w+", match.group(2))
            # Prompt 3-5 etiket istiyor ama model bazen daha fazlasını
            # döndürüyor; Instagram'da etiket yığını spam sinyali sayıldığı
            # için üst sınır kodda da uygulanır (bkz. _default_hashtags).
            hashtags = ContentProcessor._filter_hashtags(hashtags)
        else:
            # HASHTAGS satırı yoksa en azından caption'ı kurtar
            match = re.search(r"^[ 	]*\**[ 	]*CAPTION[ 	]*\**[ 	]*:[ 	]*(.*)",
                              text, re.DOTALL | re.MULTILINE)
            if not match:
                return None
            caption = match.group(1).strip()
            hashtags = []

        # Model işaretçiyi markdown ile sarabiliyor ya da caption'ı tırnağa
        # alabiliyor; kalıntı Instagram'da görünen İLK SATIRA düşer.
        caption = ContentProcessor._temizle_caption(caption)
        if not caption:
            return None
        return {"caption": caption, "hashtags": hashtags}

    @staticmethod
    def _temizle_caption(metin: str) -> str:
        """Caption'dan markdown/tırnak/işaretçi kalıntısını soy."""
        t = (metin or "").strip()
        t = re.sub(r"^\s*\**\s*CAPTION\s*\**\s*:\s*", "", t, flags=re.IGNORECASE)
        t = t.strip().strip("*").strip()
        if len(t) >= 2 and t[0] in "\"'“‘" and t[-1] in "\"'”’":
            t = t[1:-1].strip()
        return t

    @staticmethod
    def _parse_reels_script_response(text: str) -> dict | None:
        """
        Gemini'nin "INTRO: ...\\nSEGMENT 1: ...\\nSEGMENT 2: ...\\nOUTRO: ..."
        düz metin formatındaki reels senaryosu yanıtını ayrıştırır.
        """
        # SATIR SONUNU YUTMAYAN kalıplar. Eskiden `\s*(.*)` kullanılıyordu
        # ve `\s*` satır sonunu da yiyordu: "SEGMENT 2:" boş bırakılırsa
        # bir sonraki satırın TAMAMI yakalanıyor, yani segmentin içeriği
        # "SEGMENT 3: Diablo haberi" oluyordu — işaretçinin kendisi metne
        # karışıp REELS SLAYTINA BASILIYORDU. `[ 	]*` + MULTILINE ile her
        # eşleşme tek satıra bağlı.
        intro_match = re.search(r"^[ 	]*INTRO[ 	]*:[ 	]*(.*)$", text,
                                re.IGNORECASE | re.MULTILINE)
        outro_match = re.search(r"^[ 	]*OUTRO[ 	]*:[ 	]*(.*)$", text,
                                re.IGNORECASE | re.MULTILINE)

        # SEGMENT NUMARASI KULLANILIYOR. Prompt "haberlerle aynı sırada, her
        # haber için ayrı bir SEGMENT satırı" istiyor ve numarayı yazdırıyor;
        # eski parser o numarayı ATIYOR, boş segmentleri de listeden
        # düşürüyordu. Oysa `video_generator` görselleri segmentlere İNDİSE
        # GÖRE eşliyor — ortadaki bir segment düşerse sonraki her haber
        # BAŞKA haberin fotoğrafıyla eşleşiyordu.
        esler: dict[int, str] = {}
        for m in re.finditer(r"^[ 	]*SEGMENT[ 	]*(\d+)[ 	]*:[ 	]*(.*)$",
                             text, re.IGNORECASE | re.MULTILINE):
            metin = m.group(2).strip()
            if metin:
                esler[int(m.group(1))] = metin

        if not esler:
            return None

        sirali = sorted(esler)
        return {
            "intro": intro_match.group(1).strip() if intro_match else "",
            "segments": [esler[n] for n in sirali],
            # Her segmentin HANGİ habere ait olduğu (0 tabanlı). Görsel/URL
            # eşlemesi bunu kullanmalı, listedeki sırayı değil.
            "segment_indices": [n - 1 for n in sirali],
            "outro": outro_match.group(1).strip() if outro_match else "",
        }

    @staticmethod
    def _parse_list_content_response(text: str) -> dict | None:
        """
        Gemini'nin "LISTE: EVET\\nKAPAK: ...\\nOGE 1: isim | detay\\n..." düz
        metin formatındaki yanıtını ayrıştırır. LISTE: HAYIR ise veya en az
        2 geçerli öge çıkarılamazsa None döner (carousel üretilmez, normal
        tekil görsele devam edilir).
        """
        # TÜRKÇE HARF VARYANTLARI VE MARKDOWN KABUL EDİLİR.
        #
        # Promptun GÖVDESİ "LİSTE" ve "ÖGELER" (noktalı İ, Ö) yazıyor, FORMAT
        # BLOĞU ise "LISTE" ve "OGE" yazıyor. Model doğal olarak gövdedeki
        # yazımı taklit edip `ÖGE 1:` üretiyor; eski kalıpta `OGE` için
        # IGNORECASE bile yoktu ve Ö ≠ O olduğu için hiçbir öge ayrışmıyordu.
        #
        # Ölçüm (8 Ağustos 2026): 279 üretilmiş içeriğin 0'ında `list_items`
        # doluydu. Yani carousel özelliği hiç çalışmadı ve `detect_list_content`
        # her haber için boşuna bir Gemini çağrısı harcadı.
        list_match = re.search(r"^[ 	]*\**[ 	]*L[İI]STE[ 	]*\**[ 	]*:[ 	]*\**[ 	]*(EVET|HAYIR)",
                               text, re.IGNORECASE | re.MULTILINE)
        if not list_match or list_match.group(1).upper() != "EVET":
            return None

        cover_match = re.search(r"^[ 	]*\**[ 	]*KAPAK[ 	]*\**[ 	]*:[ 	]*\**[ 	]*(.+)$",
                                text, re.IGNORECASE | re.MULTILINE)
        cover = cover_match.group(1).strip().strip("*").strip() if cover_match else None

        # Önce TÜM öge satırları toplanır, sonra ayraç kontrolü yapılır.
        ham_ogeler = re.findall(r"^[ 	]*\**[ 	]*[ÖO][GĞ]E[ 	]*\d+[ 	]*\**[ 	]*:[ 	]*\**[ 	]*(.+)$",
                                text, re.IGNORECASE | re.MULTILINE)

        # AYRACI OLMAYAN TEK BİR ÖGE VARSA TÜM LİSTE REDDEDİLİR.
        #
        # Eskiden ayraçsız öge sessizce düşüyordu. Sonuç: kapakta "en iyi 5
        # oyun" yazarken carousel'de 3 slayt oluyordu — yani kapak metni
        # UYDURMA bir sayı vaat ediyordu. Bu projenin en katı kuralı
        # ("uydurma bilgi yok") tam da burada ihlal ediliyordu.
        items = []
        for ham in ham_ogeler:
            if "|" not in ham:
                logger.warning(
                    "Liste ögesinde ayraç yok, carousel reddedildi "
                    f"(kapaktaki sayı yanlış olurdu): {ham[:60]}"
                )
                return None
            ad, _, detay = ham.partition("|")
            ad, detay = ad.strip().strip("*").strip(), detay.strip()
            if ad and detay:
                items.append({"name": ad, "detail": detay})

        if not cover or len(items) < 2:
            return None
        return {"cover": cover, "items": items[:8]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    processor = ContentProcessor()
    stats = processor.process_all_news()
    print(f"\n📊 Sonuç: {stats}")
