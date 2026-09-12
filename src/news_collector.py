"""
Haber Toplama Modülü
RSS feed'leri ve News API'ler üzerinden AI ve Gaming haberlerini toplar.
"""

import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    RSS_FEEDS, NEWS_API_KEY, CURRENTS_API_KEY,
    NEWS_API_QUERIES, CONTENT_LANGUAGE,
    BOT_NAME, BOT_CONTACT_URL,
    DEDUP_TITLE_SIMILARITY_THRESHOLD, DEDUP_WINDOW_HOURS,
    GAMING_RELEVANCE_KEYWORDS, AI_RELEVANCE_KEYWORDS, GAMBLING_BLOCKLIST_KEYWORDS,
    ENTERTAINMENT_ROUNDUP_TITLE_KEYWORDS, GAME_ADAPTATION_KEYWORDS,
    GAME_ADAPTATION_FRANCHISES, SCREEN_MEDIA_CONTEXT_WORDS,
    PR_WIRE_SOURCES, AD_CONTENT_KEYWORDS,
    FINANCIAL_ADVICE_SOURCES, FINANCIAL_ADVICE_KEYWORDS, UNRELIABLE_SOURCES,
    OFF_TOPIC_SOURCES
)
from src.database import Database

logger = logging.getLogger(__name__)

# Besleme içeriğine gömülü ilk <img> — bkz. NewsCollector._extract_image.
_CONTENT_IMG_PATTERN = re.compile(r"""<img[^>]+src=["']([^"']+)""", re.I)


class NewsCollector:
    """Dünya genelinden AI ve Gaming haberlerini toplar."""

    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.session = requests.Session()
        self.session.headers.update({
            # Şeffaf, kaynak sitesi yöneticisinin ne olduğunu anlayabileceği bir
            # User-Agent — genel/kimliksiz bir bot dizesi (ya da tarayıcı taklidi)
            # WAF/anti-bot sistemlerince şüpheli görülüp engellenmeye daha yatkın.
            # +URL, sitenin bizi "saldırgan" değil meşru bir haber toplayıcı
            # olarak tanıyıp iletişime geçebilmesini sağlar.
            # Ad ve iletişim URL'si .env'den gelir (BOT_NAME / BOT_CONTACT_URL);
            # kendi kurulumunuzda kendi kimliğinizi göndermelisiniz.
            "User-Agent": f"{BOT_NAME}/1.0 (+{BOT_CONTACT_URL}; AI & Gaming news aggregator)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })

    @staticmethod
    def _is_gaming_relevant(title: str, description: str = "") -> bool:
        """
        "gaming" kategorisindeki geniş RSS/NewsAPI kaynakları (IGN, Kotaku,
        Polygon vb. genel "geek culture" siteleri, ya da "esports"/"game
        developer" gibi geniş OR sorguları) oyunla hiç ilgisi olmayan içerik
        de getiriyor — spor maçı haberleri, TV/dizi/anime/reality show
        haberleri gibi. Başlık+açıklamada GAMING_RELEVANCE_KEYWORDS'ten en
        az biri yoksa alakasız kabul edilir. Kumar/casino içeriği (bkz.
        GAMBLING_BLOCKLIST_KEYWORDS) "gaming" kelimesinin iGaming/kumar
        anlamını kullanarak bu filtreyi atlatabildiğinden, bir gambling
        terimi varsa diğer eşleşmelere bakılmaksızın alakasız sayılır.
        """
        text = f"{title} {description}".lower()
        if any(keyword in text for keyword in GAMBLING_BLOCKLIST_KEYWORDS):
            return False
        # Oyun uyarlamaları (film/dizi/anime) oyun dünyasının parçasıdır —
        # bu sinyal varsa aşağıdaki derleme filtresi hiç uygulanmaz.
        #
        # İki yoldan tespit edilir:
        # 1) Açık uyarlama ifadesi ("based on the video game", "anime adaptation")
        # 2) Bilinen bir uyarlama serisi ADI + ekran-medyası bağlam kelimesi.
        #    İkisi BİRLİKTE aranır çünkü "fallout"/"halo"/"arcane" gibi adlar
        #    günlük İngilizcede de geçiyor ("political fallout from the merger").
        is_adaptation = any(keyword in text for keyword in GAME_ADAPTATION_KEYWORDS)
        if not is_adaptation:
            is_adaptation = (
                any(name in text for name in GAME_ADAPTATION_FRANCHISES)
                and any(word in text for word in SCREEN_MEDIA_CONTEXT_WORDS)
            )
        # "Ne izlesem" derlemeleri yalnızca BAŞLIKTAN elenir: bir oyun
        # haberinin açıklamasında dizi/platform adı geçmesi normaldir.
        if not is_adaptation:
            title_lower = title.lower()
            if any(keyword in title_lower for keyword in ENTERTAINMENT_ROUNDUP_TITLE_KEYWORDS):
                return False
        return is_adaptation or any(keyword in text for keyword in GAMING_RELEVANCE_KEYWORDS)

    @staticmethod
    def _is_ai_relevant(title: str, description: str = "") -> bool:
        """
        "ai" kategorisindeki NewsAPI/Currents sorguları bazen tamamen
        alakasız sonuçlar getiriyor (ör. futbol transfer haberleri "ai"
        etiketiyle gelmiş — bkz. Onefootball.com). Başlık+açıklamada
        AI_RELEVANCE_KEYWORDS'ten biri ya da "ai" kelimesi tek başına
        (kelime sınırıyla, "fair"/"again" gibi kelimelerin içine
        yanlışlıkla eşleşmesin diye) yoksa alakasız kabul edilir.
        """
        text = f"{title} {description}".lower()
        if re.search(r"\bai\b", text):
            return True
        return any(keyword in text for keyword in AI_RELEVANCE_KEYWORDS)

    @staticmethod
    def _is_pr_or_ad_content(title: str, description: str = "", source_name: str = "") -> bool:
        """
        NewsAPI/Currents'ın geniş taraması PR bülteni dağıtım servislerinden
        (GlobeNewswire vb.) şirket duyurularını ve doğrudan reklam/affiliate
        metinlerini de getiriyor. Bunlar habermiş gibi işlenip paylaşılırsa
        hem güvenilirliği zedeler hem Türkiye'deki örtülü reklam mevzuatı
        açısından risk oluşturur — kategori/dil ayrımı yapılmadan her ikisi
        için de elenir.
        """
        source_lower = (source_name or "").lower()
        if any(wire in source_lower for wire in PR_WIRE_SOURCES):
            return True
        text = f"{title} {description}".lower()
        # PR bültenleri başka sitelerce alıntılandığında source_name artık
        # wire servisi değil, alıntılayan site oluyor — ama açıklama
        # metninde hâlâ orijinal dateline kalıyor (parantezli "(GLOBE
        # NEWSWIRE)" ya da eğik çizgili "/PRNewswire/" gibi farklı
        # noktalamalarla). Bu yüzden aynı servis adları noktalamaya
        # bakılmaksızın açıklama metninde de aranıyor.
        if any(wire in text for wire in PR_WIRE_SOURCES):
            return True
        return any(keyword in text for keyword in AD_CONTENT_KEYWORDS)

    @staticmethod
    def _is_financial_advice_content(title: str, description: str = "", source_name: str = "") -> bool:
        """
        "Motley Fool", "MarketBeat" gibi ABD borsa-tavsiyesi siteleri "ai"
        sorgularımıza "Is There a Better Way To Play The AI Boom?" tarzı
        hisse senedi/opsiyon tavsiyesi sızdırıyor. Bunlar gerçek AI haberi
        değil — 2026'da yürürlüğe giren Ticaret Bakanlığı reklam yönetmeliği
        kâr garantili/sermaye piyasası aracı niteliğindeki reklamları ve
        lisanssız yatırım tavsiyesini yasaklıyor (6362 sayılı Kanun/SPK),
        bu yüzden habermiş gibi çevrilip paylaşılması hukuki risk oluşturur.
        """
        source_lower = (source_name or "").lower()
        if any(src in source_lower for src in FINANCIAL_ADVICE_SOURCES):
            return True
        text = f"{title} {description}".lower()
        # Currents API'de source_name genelde makale YAZARININ adı oluyor,
        # yayıncı adı değil — bkz. "European stocks... By Investing.com"
        # (source_name="Navamya Acharya", "Investing.com" başlığın içinde).
        # Bu yüzden kaynak adları metinde de aranıyor.
        if any(src in text for src in FINANCIAL_ADVICE_SOURCES):
            return True
        return any(keyword in text for keyword in FINANCIAL_ADVICE_KEYWORDS)

    @staticmethod
    def _is_unreliable_source(source_name: str) -> bool:
        """
        Bugüne kadarki tüm filtreler KONU alakasına bakıyordu (gaming/ai/
        reklam mı), kaynağın GÜVENİLİRLİĞİNE değil. Gerçek bir olayda,
        İngiliz hiciv sitesi Thedailymash.co.uk'nin Elon Musk hakkında
        uydurma bir "haberi" konu filtresini rahatça geçti ve Gemini, hiciv
        olduğunu fark etmeyip "The Economist ile röportaj" gibi HİÇ VAR
        OLMAYAN detaylar uydurarak gerçek bir haber gibi Türkçe caption
        üretti. Bu yüzden bilinen hiciv/yanlış bilgi kaynakları, konuları ne
        olursa olsun, kategori kontrolünden tamamen bağımsız olarak elenir.
        """
        source_lower = (source_name or "").lower()
        if any(src in source_lower for src in UNRELIABLE_SOURCES):
            return True
        # Konu dışı kaynaklar da aynı kapıdan elenir: teknik olarak
        # "güvenilmez" değiller ama yayınladıkları şey haber değil
        # (paket sürüm listeleri, bölgesel genel haber siteleri).
        return any(src in source_lower for src in OFF_TOPIC_SOURCES)

    def _is_duplicate(self, title: str, category: str) -> tuple[bool, list | None]:
        """
        Aynı olayın farklı bir kaynakta zaten toplanıp toplanmadığını kontrol et.
        Eşleşme bulunursa orijinal haberin mention_count'unu artırır.

        (dubleks_mi, embedding) döner. Dubleks DEĞİLSE ve `find_similar_recent`
        tekilleştirme kontrolü sırasında bir embedding hesapladıysa, bu vektör
        `add_news(embedding=...)`'e geçirilmeli — aksi halde aynı vektör
        gereksiz yere İKİNCİ bir NVIDIA çağrısıyla tekrar hesaplanır.
        """
        dup_id, embedding = self.db.find_similar_recent(
            title, category, DEDUP_WINDOW_HOURS, DEDUP_TITLE_SIMILARITY_THRESHOLD
        )
        if dup_id is not None:
            self.db.increment_mention_count(dup_id)
            return True, None
        return False, embedding

    def collect_all(self) -> dict:
        """Tüm kaynaklardan haberleri topla."""
        logger.info("=" * 60)
        logger.info("🔄 Haber toplama başlıyor...")
        
        stats = {"rss": 0, "newsapi": 0, "currents": 0, "total": 0}

        # 1. RSS Feed'lerden haberleri topla
        rss_count = self._collect_from_rss()
        stats["rss"] = rss_count

        # 2. NewsAPI'den haberleri topla
        if NEWS_API_KEY:
            newsapi_count = self._collect_from_newsapi()
            stats["newsapi"] = newsapi_count

        # 3. Currents API'den haberleri topla
        if CURRENTS_API_KEY:
            currents_count = self._collect_from_currents()
            stats["currents"] = currents_count

        # Kaynak başına sonuç: sağlık kontrolü "N kaynak sessiz" diyebilsin.
        stats["feeds"] = getattr(self, "last_feed_results", {})
        stats["failed_feeds"] = sorted(
            a for a, r in stats["feeds"].items() if r["error"]
        )
        stats["total"] = stats["rss"] + stats["newsapi"] + stats["currents"]
        
        logger.info(f"✅ Toplam {stats['total']} yeni haber toplandı")
        logger.info(f"   📡 RSS: {stats['rss']} | 📰 NewsAPI: {stats['newsapi']} | ⚡ Currents: {stats['currents']}")
        logger.info("=" * 60)
        
        return stats

    # =============================================
    # RSS FEED TOPLAMA
    # =============================================

    def _collect_from_rss(self) -> int:
        """Tüm RSS feed'lerden haberleri topla.

        Kaynak başına sonuç `self.last_feed_results` içine yazılır. Neden:
        bir feed patladığında yalnızca `logger.warning` yazılıp döngü devam
        ediyordu ve HANGİ kaynağın öldüğü hiçbir yere taşınmıyordu. Sağlık
        kontrolü ise `MAX(collected_at)` bakıyor — yani TÜM kaynaklar
        arasındaki en yeniye. Tek bir feed çalıştığı sürece kontrol yeşil
        kalır; kaynakların yarısı aylarca ölü olabilir ve hiçbir şey
        söylemez.
        """
        total_added = 0
        self.last_feed_results = {}

        for category, feeds in RSS_FEEDS.items():
            for feed_info in feeds:
                ad = feed_info["name"]
                try:
                    count = self._parse_rss_feed(
                        feed_url=feed_info["url"],
                        source_name=ad,
                        category=category,
                        language=feed_info.get("lang", "en")
                    )
                    total_added += count
                    self.last_feed_results[ad] = {"count": count, "error": None}
                    if count > 0:
                        logger.info(f"  📡 {ad}: {count} yeni haber")
                except Exception as e:
                    logger.warning(f"  ⚠️ RSS hatası [{ad}]: {e}")
                    self.last_feed_results[ad] = {"count": 0, "error": str(e)[:200]}

                # Rate limiting — RSS sunucularını yormamak için
                time.sleep(0.5)

        hatali = [a for a, r in self.last_feed_results.items() if r["error"]]
        if hatali:
            logger.warning(
                f"  ⚠️ {len(hatali)}/{len(self.last_feed_results)} RSS kaynağı "
                f"hata verdi: {', '.join(hatali[:8])}"
            )
        return total_added

    def _parse_rss_feed(self, feed_url: str, source_name: str,
                        category: str, language: str = "en") -> int:
        """
        Tek bir RSS feed'i ayrıştır ve veritabanına kaydet.

        ÖNEMLİ: Eskiden feedparser.parse(feed_url) çağrısı kendi iç HTTP
        istemcisiyle feed'i çekiyordu — bu, self.session'daki kimliğimizi
        tanıtan User-Agent'ı HİÇ kullanmıyordu (feedparser kendi varsayılan
        User-Agent'ını gönderiyordu) ve sabit bir timeout yoktu. Artık istek
        önce session ile (doğru User-Agent + timeout ile) yapılıyor, yanıt
        feedparser'a öyle veriliyor. Ayrıca koşullu GET (ETag/If-Modified-
        Since) kullanılıyor: kaynak "304 Not Modified" dönerse feed hiç
        değişmemiş demektir, tekrar indirip ayrıştırmaya gerek yok — bu hem
        bizim hem karşı sunucunun yükünü azaltır, düzenli/öngörülebilir bir
        istemci gibi davranmamızı sağlar (sürekli aynı içeriği tekrar tekrar
        çeken botlar, engellenme ihtimali en yüksek olanlardır).
        """
        conditional_headers = {}
        etag = self.db.get_setting(f"rss_etag:{feed_url}")
        last_modified = self.db.get_setting(f"rss_last_modified:{feed_url}")
        if etag:
            conditional_headers["If-None-Match"] = etag
        if last_modified:
            conditional_headers["If-Modified-Since"] = last_modified

        try:
            response = self.session.get(feed_url, headers=conditional_headers, timeout=15)
        except requests.RequestException as e:
            logger.warning(f"RSS feed'e ulaşılamadı [{source_name}]: {e}")
            return 0

        if response.status_code == 304:
            # Feed son kontrolden beri değişmemiş — normal ve beklenen durum.
            return 0

        if response.status_code in (403, 429):
            logger.warning(
                f"RSS kaynağı bizi engelliyor/hız sınırı uyguluyor [{source_name}] "
                f"HTTP {response.status_code} — bu turda atlanıyor."
            )
            return 0

        if response.status_code >= 400:
            logger.warning(f"RSS feed hatası [{source_name}]: HTTP {response.status_code}")
            return 0

        if response.headers.get("ETag"):
            self.db.set_setting(f"rss_etag:{feed_url}", response.headers["ETag"])
        if response.headers.get("Last-Modified"):
            self.db.set_setting(f"rss_last_modified:{feed_url}", response.headers["Last-Modified"])

        feed = feedparser.parse(response.content)

        if feed.bozo and not feed.entries:
            logger.warning(f"RSS feed ayrıştırma hatası: {feed_url}")
            return 0

        added_count = 0
        for entry in feed.entries[:15]:  # Her feed'den en fazla 15 haber
            try:
                title = self._clean_text(entry.get("title", ""))
                if not title:
                    continue

                url = entry.get("link", "")
                if not url:
                    continue

                description = self._clean_text(
                    entry.get("summary", entry.get("description", ""))
                )
                # HTML etiketlerini temizle
                description = self._strip_html(description)
                # Çok uzun açıklamaları kısalt
                if description and len(description) > 500:
                    description = description[:497] + "..."

                if self._is_unreliable_source(source_name):
                    continue
                if category == "gaming" and not self._is_gaming_relevant(title, description):
                    continue
                if category == "ai" and not self._is_ai_relevant(title, description):
                    continue
                if self._is_pr_or_ad_content(title, description, source_name):
                    continue
                if self._is_financial_advice_content(title, description, source_name):
                    continue

                # Yayınlanma tarihini çıkar
                published_at = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published_at = datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M:%S")
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    published_at = datetime(*entry.updated_parsed[:6]).strftime("%Y-%m-%d %H:%M:%S")

                # Görsel URL'si bul
                image_url = self._extract_image(entry)

                # Aynı olay başka bir kaynakta zaten toplandı mı?
                dubleks, embedding = self._is_duplicate(title, category)
                if dubleks:
                    continue

                # Veritabanına ekle
                result = self.db.add_news(
                    title=title,
                    url=url,
                    category=category,
                    description=description,
                    source_name=source_name,
                    source_url=feed_url,
                    language=language,
                    image_url=image_url,
                    published_at=published_at,
                    embedding=embedding,
                )
                if result is not None:
                    added_count += 1

            except Exception as e:
                logger.debug(f"Haber ayrıştırma hatası: {e}")
                continue

        return added_count

    # =============================================
    # NEWSAPI TOPLAMA
    # =============================================

    def _collect_from_newsapi(self) -> int:
        """NewsAPI.org'dan haberleri topla."""
        if not NEWS_API_KEY:
            return 0

        total_added = 0
        base_url = "https://newsapi.org/v2/everything"

        for category, queries in NEWS_API_QUERIES.items():
            for query in queries:
                try:
                    params = {
                        "q": query,
                        "apiKey": NEWS_API_KEY,
                        "language": "en",
                        "sortBy": "publishedAt",
                        "pageSize": 10,
                        "from": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                    }
                    
                    response = self.session.get(base_url, params=params, timeout=15)
                    response.raise_for_status()
                    data = response.json()

                    if data.get("status") != "ok":
                        logger.warning(f"NewsAPI hatası: {data.get('message', 'Bilinmeyen hata')}")
                        continue

                    for article in data.get("articles", []):
                        title = article.get("title", "")
                        url = article.get("url", "")
                        if not title or not url or title == "[Removed]":
                            continue

                        description = article.get("description", "")
                        source_name = article.get("source", {}).get("name", "")
                        if self._is_unreliable_source(source_name):
                            continue
                        if category == "gaming" and not self._is_gaming_relevant(title, description):
                            continue
                        if category == "ai" and not self._is_ai_relevant(title, description):
                            continue
                        if self._is_pr_or_ad_content(title, description, source_name):
                            continue
                        if self._is_financial_advice_content(title, description, source_name):
                            continue

                        dubleks, embedding = self._is_duplicate(title, category)
                        if dubleks:
                            continue

                        result = self.db.add_news(
                            title=title,
                            url=url,
                            category=category,
                            description=description,
                            source_name=source_name,
                            language="en",
                            image_url=article.get("urlToImage"),
                            published_at=article.get("publishedAt"),
                            embedding=embedding,
                        )
                        if result is not None:
                            total_added += 1

                    time.sleep(1)  # Rate limiting

                except requests.RequestException as e:
                    logger.warning(f"NewsAPI istek hatası [{query}]: {e}")
                except Exception as e:
                    logger.warning(f"NewsAPI işleme hatası [{query}]: {e}")

        if total_added > 0:
            logger.info(f"  📰 NewsAPI: {total_added} yeni haber")
        return total_added

    # =============================================
    # CURRENTS API TOPLAMA
    # =============================================

    def _collect_from_currents(self) -> int:
        """Currents API'den haberleri topla."""
        if not CURRENTS_API_KEY:
            return 0

        total_added = 0
        base_url = "https://api.currentsapi.services/v1/search"

        search_terms = {
            "ai": "artificial intelligence",
            "gaming": "video games",
        }

        for category, keyword in search_terms.items():
            try:
                params = {
                    "keywords": keyword,
                    "apiKey": CURRENTS_API_KEY,
                    "language": "en",
                    "page_size": 10,
                }

                response = self.session.get(base_url, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()

                for article in data.get("news", []):
                    title = article.get("title", "")
                    url = article.get("url", "")
                    if not title or not url:
                        continue

                    description = article.get("description", "")
                    source_name = self._clean_source_name(article.get("author", ""))
                    if self._is_unreliable_source(source_name):
                        continue
                    if category == "gaming" and not self._is_gaming_relevant(title, description):
                        continue
                    if category == "ai" and not self._is_ai_relevant(title, description):
                        continue
                    if self._is_pr_or_ad_content(title, description, source_name):
                        continue
                    if self._is_financial_advice_content(title, description, source_name):
                        continue

                    dubleks, embedding = self._is_duplicate(title, category)
                    if dubleks:
                        continue

                    result = self.db.add_news(
                        title=title,
                        url=url,
                        category=category,
                        description=description,
                        source_name=source_name,
                        language=article.get("language", "en"),
                        image_url=article.get("image") or None,
                        published_at=article.get("published"),
                        embedding=embedding,
                    )
                    if result is not None:
                        total_added += 1

                time.sleep(1)

            except requests.RequestException as e:
                logger.warning(f"Currents API istek hatası [{category}]: {e}")
            except Exception as e:
                logger.warning(f"Currents API işleme hatası [{category}]: {e}")

        if total_added > 0:
            logger.info(f"  ⚡ Currents API: {total_added} yeni haber")
        return total_added

    # =============================================
    # YARDIMCI METOTLAR
    # =============================================

    @staticmethod
    def _clean_text(text: str) -> str:
        """Metni temizle."""
        if not text:
            return ""
        text = text.strip()
        # Gereksiz boşlukları temizle
        text = " ".join(text.split())
        return text

    @staticmethod
    def _clean_source_name(source_name: str) -> str:
        """
        Currents API bazı makalelerde (ör. Flipboard üzerinden gelenler)
        "author" alanına gerçek bir isim yerine kırık bir inline CSS
        parçası koyuyor — bkz. "Css-; Object-Fitcover; Border-Radius;
        Border; 0" (gerçek olay). Böyle bir kaynak adı hem caption'daki
        "📰 Kaynak: X" satırında saçma görünür hem de ileride bir
        güvenilirlik/kaynak filtresini yanlışlıkla atlatabilir — bu yüzden
        CSS'e benzeyen değerler boş string'e düşürülür.
        """
        if not source_name:
            return source_name
        lowered = source_name.lower()
        if "border-radius" in lowered or "object-fit" in lowered or lowered.startswith("css-"):
            return ""
        return source_name

    @staticmethod
    def _strip_html(text: str) -> str:
        """HTML etiketlerini metinden kaldır."""
        if not text:
            return ""
        import re
        clean = re.compile(r"<.*?>")
        text = re.sub(clean, "", text)
        # HTML entity'lerini temizle
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = text.replace("&#39;", "'")
        return text.strip()

    @staticmethod
    def _extract_image(entry) -> str | None:
        """RSS entry'sinden görsel URL'si çıkar."""
        # media:content
        if hasattr(entry, "media_content") and entry.media_content:
            for media in entry.media_content:
                if media.get("medium") == "image" or "image" in media.get("type", ""):
                    return media.get("url")

        # media:thumbnail
        if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
            return entry.media_thumbnail[0].get("url")

        # enclosures
        if hasattr(entry, "enclosures") and entry.enclosures:
            for enc in entry.enclosures:
                if "image" in enc.get("type", ""):
                    return enc.get("href", enc.get("url"))

        # links
        if hasattr(entry, "links"):
            for link in entry.links:
                if "image" in link.get("type", ""):
                    return link.get("href")

        # Son çare: içerik HTML'indeki ilk <img>.
        #
        # Bazı beslemeler görseli hiçbir standart alanda vermiyor, doğrudan
        # yazının HTML'ine gömüyor — massivelyop.com ve mmorpg.com bunu
        # yapıyor (5 Ağustos 2026 ölçümü). Bu alanlar kontrol edilmediği için
        # o haberler görselsiz kaydediliyor, sonra makale sayfası kazınmaya
        # çalışılıyor ve WAF 403 veriyordu — oysa görsel beslemede hazırdı.
        #
        # İlk <img> alınıyor: her iki beslemede de kapak görseli başta.
        # Yanlış bir aday gelse bile aşağı akışta boyut/format doğrulaması
        # (fetch_article_photo) onu eliyor.
        html = ""
        if getattr(entry, "content", None):
            try:
                html = entry.content[0].get("value", "") or ""
            except (AttributeError, IndexError, TypeError):
                html = ""
        if not html:
            html = getattr(entry, "summary", "") or ""

        if html:
            match = _CONTENT_IMG_PATTERN.search(html)
            if match:
                return match.group(1)

        return None


if __name__ == "__main__":
    # Test çalıştırma
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    collector = NewsCollector()
    stats = collector.collect_all()
    print(f"\n📊 Sonuç: {stats}")
