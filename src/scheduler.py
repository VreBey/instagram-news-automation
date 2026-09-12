"""
Zamanlama ve Orkestrasyon Modülü
Tüm pipeline'ı otomatik olarak çalıştırır ve zamanlar.
"""

import logging
import time
from datetime import datetime
from pathlib import Path

import schedule as schedule_lib

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    SCHEDULE, SCHEDULE_SLOTS_BY_TYPE, DAILY_POST_LIMIT, DAILY_STORY_LIMIT,
    DAILY_REELS_LIMIT, AUTO_PUBLISH_THRESHOLD, MEDIA_GENERATION_MULTIPLIER,
    APPROVAL_MODE, TELEGRAM_NOTIFY_DELAY_SECONDS, TELEGRAM_NOTIFY_MAX_AGE_DAYS,
    ROUNDUP_ENABLED, ROUNDUP_TIME, REELS_SILENT, HEALTHCHECK_TIMES,
    TELEGRAM_REMINDER_AFTER_HOURS, TELEGRAM_REMINDER_MAX_ITEMS,
)
from src.database import Database
from src.joblock import job_lock
from src.news_collector import NewsCollector
from src.content_processor import ContentProcessor
from src.image_generator import ImageGenerator
from src.story_generator import StoryGenerator
from src.instagram_client import InstagramClient, InsightsPermissionError
from src.token_manager import TokenManager
from src.schedule_utils import compute_next_schedule_time
import src.telegram_bot as telegram_bot

logger = logging.getLogger(__name__)

# Insights izin durumunun saklandığı ayar anahtarı. Sağlık kontrolü buraya
# bakıp "yayın yapılıyor ama hiç performans verisi toplanmıyor" durumunu
# görünür kılıyor — bu sessiz arıza 2026-08-04'e kadar fark edilmemişti.
_INSIGHTS_PERMISSION_KEY = "insights_permission_status"


class Scheduler:
    """Otomasyon pipeline orkestratörü."""

    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.collector = NewsCollector(db=self.db)
        self.processor = ContentProcessor(db=self.db)
        self.img_gen = ImageGenerator(db=self.db)
        self.story_gen = StoryGenerator(db=self.db)
        # Reels videoları otomatik üretiliyor (sessiz); import burada değil
        # modül düzeyinde yapılırsa moviepy her scheduler açılışında yükleniyor
        # ve ffmpeg bağımlılığı olmayan ortamlarda (ör. yalnızca dashboard
        # çalıştıran bir kurulum) gereksiz yere patlıyor.
        from src.video_generator import VideoGenerator
        self.video_gen = VideoGenerator(db=self.db)
        self.token_manager = TokenManager(db=self.db)
        self.ig_client = InstagramClient(db=self.db)

        # Başlangıçta süresi dolmak üzere olan token varsa hemen yenile
        self.token_manager.check_and_refresh_if_needed()

    # =============================================
    # TAM PIPELINE
    # =============================================

    def run_full_pipeline(self):
        """Tüm pipeline'ı sırayla çalıştır."""
        logger.info("🚀 Tam pipeline başlatılıyor...")
        start_time = time.time()

        try:
            # 1. Haberleri topla
            collect_stats = self.collect_news()
            
            # 2. İçerikleri işle
            process_stats = self.process_content()
            
            # 3. Görselleri oluştur
            media_stats = self.generate_all_media()

            # 4. APPROVAL_MODE'a göre: eşiği geçeni otomatik zamanla ya da Telegram'dan onay iste
            if APPROVAL_MODE == "auto":
                schedule_stats = self.auto_schedule_content()
            else:
                schedule_stats = self.notify_pending_approvals()

            elapsed = time.time() - start_time
            logger.info(f"✅ Pipeline tamamlandı ({elapsed:.1f}s)")
            logger.info(f"   📡 Toplanan: {collect_stats.get('total', 0)} haber")
            logger.info(f"   ⚙️ İşlenen: {process_stats.get('processed', 0)} haber")
            logger.info(f"   🎨 Üretilen: {media_stats.get('total', 0)} medya")
            logger.info(f"   🎯 Zamanlanan/bildirilen: {schedule_stats.get('total', 0)} içerik")

            return {
                "collected": collect_stats,
                "processed": process_stats,
                "media": media_stats,
                "scheduled": schedule_stats,
                "elapsed_seconds": elapsed
            }

        except Exception as e:
            logger.error(f"Pipeline hatası: {e}")
            return {"error": str(e)}

    # =============================================
    # ADIM ADIM İŞLEMLER
    # =============================================

    def collect_news(self) -> dict:
        """Haberleri topla ve hangi kaynakların hata verdiğini KAYDET.

        Kayıt olmadan ölü bir RSS kaynağı hiçbir alarm üretmiyordu: hata
        yalnızca `logger.warning`'e yazılıyor, sağlık kontrolü ise
        `MAX(collected_at)` bakıyor — yani tüm kaynaklar arasındaki en
        yeniye. Tek feed çalıştığı sürece yeşil kalıyor ve kaynakların
        yarısı aylarca ölü olabiliyordu.
        """
        logger.info("📡 Haber toplama...")
        stats = self.collector.collect_all()
        try:
            self.db.set_setting("last_failed_feeds",
                                "|".join(stats.get("failed_feeds", [])))
        except Exception as e:
            logger.warning(f"Başarısız kaynak listesi kaydedilemedi: {e}")
        return stats

    def process_content(self) -> dict:
        """İçerikleri işle ve AI ile zenginleştir.

        Kilit ZORUNLU: bu işi başlatabilen üç bağımsız kapı var (zamanlayıcı
        servisi, panelin /api/run/* uçları, elle `main.py --process`) ve
        `process_all_news` içindeki SELECT → Gemini → `is_processed=1`
        penceresi haber başına DAKİKALARCA açık kalıyor. 1 Ağustos 2026'da
        dokuz haber bu yüzden ikişer kez işlendi ve mükerrer post+story
        çiftleri üretildi. Günlük bütçe ve üretim freni bu yarışı kapatmaz
        — ikisi de çalışmanın başında okunuyor. Bkz. src/joblock.py.
        """
        logger.info("⚙️ İçerik işleme...")
        with job_lock("icerik_isleme") as alindi:
            if not alindi:
                return {"processed": 0, "posts": 0, "stories": 0,
                        "skipped_already_running": True}
            stats = self.processor.process_all_news()

            # Kota tükendiyse reels üretimini hiç denemeyelim: her çağrı yeni bir
            # 429 ve boşuna bekleme demek. Kullanıcı da haberdar edilmeli — aksi
            # halde "bugün neden az içerik var?" sorusu ancak loglara elle bakınca
            # cevaplanır (sağlık kontrolünün çözdüğü sessiz arıza deseninin aynısı).
            if stats.get("quota_exhausted"):
                self._notify_quota_exhausted(stats)
                return stats

            # REELS ARTIK POST/STORY ONAY KUYRUĞUNDAN BAĞIMSIZ. Eskiden aynı
            # `backlog_is_full` freni reels'i de durduruyordu (gerekçe: 7
            # Ağustos 2026'da fren açıkken üretilen tek içerik türü reels'ti
            # ve her turda boşa bir Gemini çağrısı harcıyordu). Ama reels
            # zaten onay kuyruğuna hiç YÜK BİNDİRMİYOR — REELS_SILENT
            # sayesinde Telegram onayından geçmiyor, üretilir üretilmez
            # elle-paylaşım videosu olarak doğrudan gönderiliyor. Kullanıcı
            # talebi (21 Ağustos 2026): post/story kuyruğu kalabalık diye
            # hesabın en güçlü büyüme kanalı (reels) günlerce sıfır üretimde
            # kaldı — kuyruk 5+ gündür hiç tavanın altına inmemişti.
            #
            # Aşırı üretimi ÖNLEMEK için kendi ölçütü var: teslim edilmemiş
            # (Telegram'a gönderilmemiş) reels taslağı zaten DAILY_REELS_LIMIT
            # kadar VARSA yenisi üretilmez — aksi halde günde 3 process_content
            # turu × 2 kategori = 6 yeni Gemini senaryo çağrısı, hiçbiri asla
            # teslim edilmeden birikirdi.
            bekleyen_reels = len(self.db.get_draft_content(
                content_type="reels", needs_delivery=True, limit=DAILY_REELS_LIMIT + 1
            ))
            if bekleyen_reels >= DAILY_REELS_LIMIT:
                logger.info(
                    f"⏸️ Reels üretimi atlandı (teslim edilmemiş {bekleyen_reels} "
                    f"≥ günlük limit {DAILY_REELS_LIMIT})."
                )
                return stats

            # Kilidin İÇİNDE: bu da aynı `is_used` bayrağını okuyup yazıyor,
            # dışarıda kalsaydı iki çalışma aynı yayınlanmış haberlerden
            # ikişer reels üretirdi.
            for category in ["ai", "gaming"]:
                reels_result = self.processor.create_reels_content(category=category)
                if reels_result:
                    stats["reels_candidates"] = stats.get("reels_candidates", 0) + 1

            return stats

    def _notify_quota_exhausted(self, stats: dict) -> None:
        """Kota tükendiğinde tek bir Telegram uyarısı gönder."""
        from config import TELEGRAM_CHAT_ID

        if not TELEGRAM_CHAT_ID:
            return

        mesaj = (
            "🚫 Gemini kotası tükendi\n\n"
            f"• {stats.get('skipped_for_quota', 0)} haber işlenmeden bırakıldı\n"
            f"• Bu turda {stats.get('posts', 0)} gönderi, {stats.get('stories', 0)} hikaye üretildi\n\n"
            "Şablon (AI'sız) içerik bilinçli olarak ÜRETİLMEDİ — kalitesiz "
            "gönderi yayınlamaktansa eksik yayınlamak tercih ediliyor. "
            "İşlenmeyen haberler kota yenilendiğinde otomatik yeniden denenecek."
        )
        try:
            telegram_bot._send_text(TELEGRAM_CHAT_ID, mesaj)
        except Exception as e:
            logger.warning(f"Kota uyarısı gönderilemedi: {e}")

    def generate_all_media(self) -> dict:
        """Tüm taslak içerikler için medya oluştur.

        `process_content` ile AYNI gerekçeyle kilitli (bkz. oradaki açıklama
        ve src/joblock.py): panelin `/api/run/generate` ucu bu işi dashboard
        sürecinde çalıştırıyor ve zamanlayıcı servisinden habersiz. İki
        çalışma aynı taslağı görüp aynı görseli iki kez üretir, üstelik
        reels dalında MoviePy videosu iki kez render edilir.
        """
        with job_lock("medya_uretimi") as alindi:
            if not alindi:
                return {"posts": 0, "stories": 0, "reels": 0, "total": 0,
                        "skipped_already_running": True}
            return self._generate_all_media()

    def _generate_all_media(self) -> dict:
        """Medya üretiminin gövdesi (kilit `generate_all_media`'da alınır)."""
        logger.info("🎨 Medya üretimi...")
        stats = {"posts": 0, "stories": 0, "reels": 0, "total": 0}

        # Not: limit, günlük yayın limitinin MEDIA_GENERATION_MULTIPLIER katı —
        # en iyi skorlular auto-publish'e gidince manuel inceleme kuyruğunda da
        # içerik kalsın diye fazladan medya üretilir.
        post_quota = DAILY_POST_LIMIT * MEDIA_GENERATION_MULTIPLIER
        story_quota = DAILY_STORY_LIMIT * MEDIA_GENERATION_MULTIPLIER

        # Medya HABER BAZINDA üretilir: aynı haberin post'u ve story'si aynı
        # turda hazır olur.
        #
        # Gerçek kullanıcı şikayeti ("hikaye ve feed'leri sıralı değil ayrı
        # ayrı gönderdi"): eskiden post ve story AYRI sorgularla, farklı
        # kotalarla (6 post / 15 story) seçiliyordu. Sıralamada 7-15.
        # aralıktaki haberlerin STORY'si medyaya kavuşup Telegram'a tek
        # başına gidiyor, POST'u ise günler sonra başka bir turda
        # gönderiliyordu — bildirim sorgusu yan yana dizmeye çalışsa bile
        # kardeşi henüz uygun olmadığı için eşleştiremiyordu.
        drafts = self.db.get_draft_content(
            limit=(post_quota + story_quota) * 2, order_by_relevance=True,
            needs_media=True,
        )
        # `needs_media=True` (yukarıda): medyası hazır taslak sorguya HİÇ
        # girmez. Eskiden hepsi geliyordu ve her tur, onay kuyruğunda
        # bekleyen bütün taslakların görselini baştan üretiyordu. İki somut
        # zararı vardı:
        #
        #  1. Günlük derleme (carousel) tek bir düz görsele çeviriliyordu:
        #     derlemenin `list_items` alanı yok, o yüzden generate_post_image
        #     onu sıradan bir gönderi sanıp tek görsel üretiyor ve
        #     update_content_media `carousel_paths`i NULL'a çekiyordu.
        #     Canlı veri: 5 derlemenin 4'ünde slaytlar böyle kaybolmuştu.
        #  2. carousel_paths NULL'a düşünce derleme yeniden "derlemeye
        #     uygun aday" hâline geliyor ve bir sonraki derlemenin içine
        #     madde olarak giriyordu — kapatılan hatanın geri dönüş yolu.
        #
        # Elemenin SQL'de olması şart: burada atlansaydı pencere işi bitmiş
        # taslaklarla dolar ve iş bekleyenler hiç sıraya gelmezdi.
        groups: dict[int, list[dict]] = {}
        for content in drafts:
            if content["content_type"] in ("post", "story"):
                groups.setdefault(content["news_id"], []).append(content)

        for items in groups.values():
            # GRUP BÜTÜNLÜĞÜ: haberin postu kotaya sığmıyorsa hikayesi de
            # üretilmez. Bu bilinçli — gerçek kullanıcı şikayeti "hikaye ve
            # feed'leri sıralı değil ayrı ayrı gönderdi" idi: kardeşi
            # olmayan bir story Telegram'a tek başına gidiyor, postu günler
            # sonra başka bir turda çıkıyordu. Ayrıca hikaye CTA'sı
            # ("detaylar profilde") kardeş postun yayınlanmış olmasına
            # bağlı (bkz. get_sibling_post_status).
            #
            # Sonucu bilerek kabul ediliyor: her grupta post olduğu için
            # hikaye üretimi pratikte POST kotasına bağlı kalıyor. Yarım
            # grup üretmektense az üretmek tercih edildi.
            # (tests/test_scheduler.py bu davranışı kilitliyor.)
            wants_post = any(c["content_type"] == "post" for c in items)
            wants_story = any(c["content_type"] == "story" for c in items)
            if wants_post and stats["posts"] >= post_quota:
                continue
            if wants_story and not wants_post and stats["stories"] >= story_quota:
                continue

            for content in items:
                # Tek bir bozuk haber (ör. decode edilemeyen dev bir görsel)
                # tüm medya turunu düşürmemeli — sonrasındaki Telegram onay
                # bildirimi de o zaman hiç çalışmıyordu.
                try:
                    if content["content_type"] == "post":
                        if stats["posts"] >= post_quota:
                            continue
                        if self.img_gen.generate_post_image(content_id=content["id"]):
                            stats["posts"] += 1
                    else:
                        if stats["stories"] >= story_quota:
                            continue
                        if self.story_gen.generate_story_image(content_id=content["id"]):
                            stats["stories"] += 1
                except Exception as e:
                    logger.warning(
                        f"Medya üretilemedi [id={content['id']}, "
                        f"{content['content_type']}]: {e}"
                    )
                    continue

        # Reels: video OTOMATİK üretilir (MoviePy) ama SESSİZ olur ve API ile
        # YAYINLANMAZ — Telegram'a dosya olarak gönderilir, kullanıcı
        # Instagram uygulamasında müziği ekleyip elle paylaşır.
        #
        # Gerekçe: Instagram'ın lisanslı müzik kütüphanesi Graph API'den
        # erişilemiyor (Meta referansında reels'in tek ses parametresi
        # `audio_name` ve o da yalnızca mevcut sesi yeniden adlandırıyor).
        # Yani "otomatik yayın" ile "Instagram müziği" birbirini dışlıyor.
        # Bu kurulumda müzik tercih edildi; reels yeni hesaplarda takipçi
        # olmayanlara ulaşmanın ana kanalı ve trend ses o kanalın parçası.
        #
        # Önceki sürüm videoyu da kullanıcıya ürettiriyordu (Gemini/Veo
        # promptu). Sonuç: 20 reels taslağı üretildi, 0 reels yayınlandı —
        # her seferinde manuel iş istediği için hiç yapılmadı. Artık yalnızca
        # son adım (müzik + paylaş) manuel.
        # ÖLÇÜ "medyası yok" DEĞİL, "TESLİM EDİLMEDİ".
        #
        # Reels için gönderim işareti `manual_video_prompt_message_id`.
        # Pencere `needs_media=True` ile seçilseydi, videosu ÜRETİLMİŞ ama
        # Telegram gönderimi başarısız olmuş bir reels bir daha hiç
        # seçilmezdi: medya yolu dolu olduğu için pencereye girmiyor, onay
        # kuyruğuna da girmiyor (REELS_SILENT onu ayıklıyor). Yani video
        # diskte duruyor, kullanıcı hiç görmüyor ve hiçbir tur bunu
        # düzeltmiyordu — sessizce kaybolan bir içerik.
        #
        # Artık teslim edilmemiş tüm reels taslakları seçiliyor; videosu
        # zaten varsa yeniden üretilmiyor, sadece gönderiliyor.
        #
        # ELEME SQL'DE (`needs_delivery=True`), çağıran tarafta DEĞİL. İlk
        # denememde Python'da eliyordum ve projenin bilinen hatasını
        # tekrarladım: `limit` sıralamanın ilk N'ini alıyor, sonraki eleme
        # onları düşürünce pencere boşalıyor ama YERİNE KİMSE GELMİYOR.
        # Somut sonuç: 4 reels'in ilk 3'ü teslim edilince dördüncüsü
        # pencereye hiç giremedi; tur "0 reels" dedi ve hata vermedi.
        draft_reels = self.db.get_draft_content(
            content_type="reels",
            limit=DAILY_REELS_LIMIT * MEDIA_GENERATION_MULTIPLIER,
            order_by_relevance=True, needs_delivery=True,
        )
        for content in draft_reels:
            mevcut = content.get("media_path")
            if mevcut and Path(mevcut).exists():
                video_path = mevcut
            else:
                video_path = self.video_gen.generate_reels(content_id=content["id"])
            if not video_path:
                continue

            # `generate_reels` medya yolunu VE kapak görselini zaten yazdı
            # (bkz. video_generator.generate_reels). Burada tekrar yazmak
            # gereksizdi ve zararlıydı: `update_content_media`'nın
            # `thumbnail_path` varsayılanı None olduğu için her seferinde
            # kapağı NULL'a çekiyordu. Sonuç: panelde her reels'in önizlemesi
            # boştu (templates/index.html `d.thumbnail_path` kullanıyor) ve
            # REELS_SILENT kapatılırsa publish_reels'in cover_url'ü de
            # kaybolurdu.
            content["media_path"] = video_path
            message_id = telegram_bot.send_reels_for_manual_publish(content, video_path)
            if message_id:
                # Bu alan "Telegram'a gönderildi" işareti olarak kullanılıyor;
                # tekrar gönderimi ve API yayın yolunu engelliyor.
                self.db.set_manual_video_prompt(content["id"], message_id)
                stats["reels"] += 1

                # HABERLER ANCAK BURADA YAKILIR — video gerçekten kullanıcıya
                # ulaştığında. Eskiden taslak üretilirken yakılıyordu ve
                # ölçüm şunu gösterdi: 33 reels taslağının hiçbiri
                # yayınlanmamasına rağmen 120 haber yakılmıştı, yani yakılan
                # haberlerin %100'ü boşa gitti. `is_used = 0` filtresi
                # eklenince bu, havuzu kalıcı eriten bir sızıntıya dönüştü
                # (tüketim günde 10-40, üretim 1-5).
                script = content.get("reels_script") or {}
                for news_id in script.get("news_ids", []):
                    self.db.mark_news_used(news_id)

        stats["total"] = stats["posts"] + stats["stories"] + stats["reels"]
        logger.info(f"🎨 Medya üretimi: {stats['posts']} post, "
                    f"{stats['stories']} story, {stats['reels']} reels videosu gönderildi")
        return stats

    def auto_schedule_content(self) -> dict:
        """
        relevance_score'u AUTO_PUBLISH_THRESHOLD'un üzerinde olan, medyası hazır
        taslak içerikleri günlük limitler dahilinde otomatik zamanlar.
        Eşiği geçemeyen içerik 'draft' durumunda kalır — dashboard'daki manuel
        inceleme kuyruğunun ta kendisi.
        """
        logger.info("🎯 Otomatik zamanlama kontrolü...")
        stats = {"post": 0, "story": 0, "reels": 0, "total": 0}
        limits = {
            "post": DAILY_POST_LIMIT,
            "story": DAILY_STORY_LIMIT,
            "reels": DAILY_REELS_LIMIT,
        }

        today_published = self.db.get_today_publish_count()

        for content_type, slot_keys in SCHEDULE_SLOTS_BY_TYPE.items():
            # Sessiz reels API ile YAYINLANMAZ: müzik Instagram uygulamasında
            # ekleniyor, API'den yayınlansa müziksiz çıkardı. Video Telegram'a
            # gönderiliyor ve elle paylaşılıyor. Bkz. config.REELS_SILENT.
            if content_type == "reels" and REELS_SILENT:
                continue
            already_committed = (
                today_published.get(content_type, 0)
                + self.db.get_scheduled_count_today(content_type)
            )
            remaining = limits[content_type] - already_committed
            if remaining <= 0:
                continue

            # ELEME SQL'DE. Eskiden `limit` kadar taslak çekilip medyasızlar
            # ve eşiği geçemeyenler BURADA atılıyordu; pencere uygun olmayan
            # kayıtlarla dolduğunda gerçekten uygun içerik hiç sıraya
            # gelmiyordu. Bu, projede tekrarlayan bir hata sınıfı.
            eligible = self.db.get_draft_content(
                content_type=content_type, limit=remaining, order_by_relevance=True,
                has_media=True, min_relevance=AUTO_PUBLISH_THRESHOLD,
            )

            for content in eligible:
                # SLOT SAYIYLA DEĞİL, DOLULUĞA GÖRE seçilir.
                #
                # Eskiden `slot_keys[i % len(slot_keys)]` kullanılıyordu: slot
                # zaten dolu ya da saati geçmiş olsa bile sıradaki indeks
                # veriliyordu. `compute_next_schedule_time` tam olarak bu
                # hatayı düzeltmek için yazılmıştı (gerçek olay: art arda iki
                # onay AYNI yarınki slota çakıştı, bugünün hâlâ boş olan gece
                # slotu hiç kullanılmadı) — ama bu yol onu çağırmıyordu, yani
                # düzeltme yalnızca testlerde yaşıyordu.
                #
                # Bu mod varsayılan değil (APPROVAL_MODE=telegram), o yüzden
                # hata canlıda görünmüyordu; açıldığı gün geri gelirdi.
                scheduled_time = compute_next_schedule_time(self.db, content_type)

                self.db.schedule_post(content["id"], scheduled_time, content_type, source="auto")
                self.db.update_content_status(content["id"], "approved")
                stats[content_type] += 1
                logger.info(
                    f"🎯 Otomatik zamanlandı: [{content_type}] ID={content['id']} "
                    f"(skor={content.get('relevance_score', 0):.2f}) → {scheduled_time}"
                )

        stats["total"] = stats["post"] + stats["story"] + stats["reels"]
        logger.info(f"🎯 Otomatik zamanlama: {stats['total']} içerik zamanlandı "
                    f"({stats['post']} post, {stats['story']} story, {stats['reels']} reels)")
        return stats

    def notify_pending_approvals(self) -> dict:
        """
        Medyası hazır ama henüz Telegram'a bildirilmemiş taslak içerikler için
        onay isteği gönderir. Hiçbir içerik burada otomatik zamanlanmaz —
        yalnızca kullanıcı Telegram'daki '✅ Onayla' butonuna basınca zamanlanır
        (bkz. src/telegram_bot.py).
        """
        logger.info("📲 Telegram onay bildirimleri kontrol ediliyor...")
        stats = {"post": 0, "story": 0, "reels": 0, "total": 0}

        # TEK bir sorgu: aynı habere ait post+story yan yana, doğru sırada
        # gelsin diye (bkz. get_content_pending_telegram_notification'daki
        # açıklama) tüm türler birlikte, tek bir listede çekilir.
        # Sessiz reels bu kuyruğa girmez: onay butonları API yayınını
        # tetikliyor, oysa reels elle paylaşılıyor. Videosu zaten ayrı bir
        # mesajla gönderiliyor (send_reels_for_manual_publish).
        pending = self.db.get_content_pending_telegram_notification(
            limit=20, max_age_days=TELEGRAM_NOTIFY_MAX_AGE_DAYS
        )
        if REELS_SILENT:
            pending = [c for c in pending if c.get("content_type") != "reels"]
        for index, content in enumerate(pending):
            # Telegram bir botun AYNI sohbete gönderimini kabaca dakikada 20
            # mesajla sınırlar. Bu döngü 20 içeriği (her biri fotoğraf +
            # metin) araya hiç boşluk koymadan ~20 saniyede gönderiyordu —
            # yani limitin tam sınırında. Araya küçük bir bekleme koymak hem
            # 429'ları önlüyor hem de bildirimlerin tek seferde sel gibi
            # akmasını engelliyor.
            if index:
                time.sleep(TELEGRAM_NOTIFY_DELAY_SECONDS)
            message_id = telegram_bot.send_approval_request(content)
            if message_id:
                self.db.set_content_telegram_message(content["id"], message_id)
                stats[content["content_type"]] += 1

        stats["total"] = stats["post"] + stats["story"] + stats["reels"]
        logger.info(f"📲 Telegram bildirimi gönderildi: {stats['total']} içerik "
                    f"({stats['post']} post, {stats['story']} story, {stats['reels']} reels)")
        return stats

    def send_reminders(self) -> dict:
        """Zaten gönderilmiş ama cevapsız kalmış onay isteklerini hatırlatır.

        notify_pending_approvals SADECE hiç gönderilmemiş içerikleri bulur;
        bir kez gönderilip kullanıcının Telegram akışında kaybolan/unutulan
        öğeler için bir daha asla bildirim gitmiyordu. Gerçek olay (13
        Ağustos 2026): 10-12 Ağustos'ta gönderilen 26 onay isteği fark
        edilmedi, kuyruk günlerce erimeden kaldı ve üretim freni devrede
        kaldı. Bkz. TELEGRAM_REMINDER_AFTER_HOURS docstring'i.
        """
        logger.info("🔔 Cevapsız onay istekleri kontrol ediliyor...")
        stats = {"reminded": 0}

        stale = self.db.get_stale_pending_approvals(
            hours=TELEGRAM_REMINDER_AFTER_HOURS, limit=TELEGRAM_REMINDER_MAX_ITEMS
        )
        for index, content in enumerate(stale):
            if index:
                time.sleep(TELEGRAM_NOTIFY_DELAY_SECONDS)
            message_id = telegram_bot.send_reminder(content)
            if message_id:
                self.db.set_content_telegram_message(content["id"], message_id)
                stats["reminded"] += 1

        if stats["reminded"]:
            logger.info(f"🔔 {stats['reminded']} içerik için hatırlatma gönderildi.")
        return stats

    def run_healthcheck(self) -> dict:
        """Sessiz arızaları kontrol eder, durum değiştiyse Telegram'dan uyarır."""
        from src.healthcheck import check_and_alert
        return check_and_alert(self.db)

    def create_daily_roundup(self) -> dict:
        """Günün öne çıkan haberlerini TEK kaydırmalı feed gönderisinde topla.

        Kapsam sorununun çözümü: günde ~75 haber toplanıyor ama feed kotası 2
        gönderi. Tek tek yayınlayarak kapsamı büyütmek spam sinyali (2 Ağustos
        2026'da 18 gönderi yayınlandı). Derleme, kapsamı gönderi SAYISINI
        artırmadan büyütür.

        Üretilen derleme normal onay akışından geçer — otomatik yayınlanmaz.
        Telegram'a bildirilir, kullanıcı onaylarsa yayınlanır.
        """
        from src.roundup import (
            build_roundup_caption, build_roundup_slides, select_roundup_items,
        )

        stats = {"created": False, "item_count": 0}

        if not ROUNDUP_ENABLED:
            return stats

        items = select_roundup_items(self.db)
        if not items:
            return stats

        slides = build_roundup_slides(items)
        paths = self.img_gen.generate_carousel_images(slides)
        if len(paths) < 2:
            logger.warning("Derleme yeterli slaytla üretilemedi, atlanıyor.")
            return stats

        # Derleme bir haberin gönderisi değil ama processed_content bir
        # news_id istiyor; en yüksek puanlı öğenin haberi temsilci olarak
        # kullanılıyor (kaynak/kategori bağlamı da ondan geliyor).
        content_id = self.db.add_content(
            news_id=items[0]["news_id"],
            content_type="post",
            caption=build_roundup_caption(items),
            hashtags=None,
            summary_text=f"Günün {len(items)} Haberi",
        )
        self.db.update_content_carousel(content_id, paths)
        self.db.set_background_source(
            content_id, self.img_gen.last_background_source())

        # Dahil edilen taslakları işaretle: aynı haber hem derlemede hem
        # tekil gönderi olarak yayınlanmamalı.
        self.db.mark_used_in_roundup([i["id"] for i in items])

        stats["created"] = True
        stats["item_count"] = len(items)
        logger.info(
            f"🗞️ Günlük derleme üretildi: {len(items)} haber, "
            f"{len(paths)} slayt (content_id={content_id})"
        )
        return stats

    def run_maintenance(self) -> dict:
        """Günlük bakım: yedekle, temizle, sıkıştır, diski buda."""
        from src.maintenance import run_daily_maintenance
        return run_daily_maintenance(self.db)

    def sync_insights(self) -> dict:
        """Son yayınların performans metriklerini (impressions/reach/likes vb.) senkronize et."""
        if not self.ig_client.is_configured():
            logger.warning("⚠️ Instagram API yapılandırılmamış. Insights alınamıyor.")
            return {"synced": 0}

        records = self.db.get_publish_history_for_insights(days=14)
        synced = 0

        for record in records:
            try:
                insights = self.ig_client.get_media_insights(
                    record["instagram_media_id"], post_type=record["post_type"]
                )
            except InsightsPermissionError as e:
                # İzin eksikse sorun tek bir medyada değil, hesabın tamamında.
                # Turu burada kesiyoruz: kalan medyaları denemek garantili
                # başarısız çağrı üretir ve asıl sebebi onlarca uyarı satırının
                # içinde gizler. Durum ayrıca kaydediliyor ki sağlık kontrolü
                # bunu görünür kılabilsin — 2026-08-04'e kadar sistem 58/58
                # başarısız oluyordu ve hiçbir uyarı üretmiyordu.
                logger.error(
                    "🚫 Insights alınamıyor: uygulama token'ında "
                    "'instagram_manage_insights' izni yok. Meta uygulama "
                    "ayarlarından bu izni ekleyip token'ı yeniden üretin. "
                    f"(Meta mesajı: {e})"
                )
                self.db.set_setting(_INSIGHTS_PERMISSION_KEY, "missing")
                return {"synced": 0, "total": len(records), "permission_error": True}

            if not insights:
                continue

            likes = insights.get("likes", 0) or 0
            comments = insights.get("comments", 0) or 0
            reach = insights.get("reach") or 0
            engagement_rate = round((likes + comments) / reach, 4) if reach else None

            self.db.add_media_insight(
                publish_history_id=record["id"],
                impressions=insights.get("impressions"),
                reach=reach,
                likes=likes,
                comments=comments,
                saves=insights.get("saved"),
                shares=insights.get("shares"),
                engagement_rate=engagement_rate,
            )
            synced += 1

        if synced:
            # İzin sorunu çözülmüş demektir; bayrağı temizle ki sağlık
            # kontrolü düzelmiş bir durumu bildirmeye devam etmesin.
            self.db.set_setting(_INSIGHTS_PERMISSION_KEY, "ok")

        logger.info(f"📊 Insights senkronize edildi: {synced}/{len(records)} kayıt")
        return {"synced": synced, "total": len(records)}

    def publish_scheduled(self):
        """Zamanı gelen içerikleri paylaş."""
        if not self.ig_client.is_configured():
            logger.warning("⚠️ Instagram API yapılandırılmamış. Paylaşım yapılamıyor.")
            return

        pending = self.db.get_pending_scheduled()
        logger.info(f"📤 {len(pending)} zamanlanmış gönderi kontrol ediliyor...")

        for item in pending:
            try:
                content = self.db.get_content_by_id(item["content_id"])
                if not content or not content.get("media_path"):
                    self._safe_update_schedule_status(item["id"], "failed")
                    self._notify_publish_failure(
                        content or {}, item["post_type"],
                        "İçerik ya da medya dosyası bulunamadı")
                    continue

                # Hikaye ise, CTA'yı yayından hemen önceki en güncel duruma
                # göre yeniden çiz: aynı habere ait feed gönderisi GERÇEKTEN
                # yayınlanmışsa "detaylar profilde" denebilir, aksi halde asla
                # profilde olmayan bir şeye atıf yapılmaz (bkz. get_sibling_
                # post_status). Görsel üretim zamanı (onaydan hemen önce) ile
                # yayın zamanı arasında sibling'in durumu değişmiş olabilir,
                # bu yüzden kontrol burada -- yayına en yakın anda -- yapılır.
                if item["post_type"] == "story" and content.get("news_id"):
                    sibling_status = self.db.get_sibling_post_status(content["news_id"])
                    regenerated = self.story_gen.generate_story_image(
                        content_id=item["content_id"],
                        sibling_post_published=(sibling_status == "published"),
                    )
                    if regenerated:
                        content["media_path"] = regenerated

                post_type = item["post_type"]
                # "Liste" tespit edilmiş bir haberin kaydırmalı (carousel)
                # gönderisi — bkz. image_generator._generate_post_carousel.
                # Her slayt ayrı ayrı hosting'e yüklenip publish_carousel ile
                # tek bir gönderi olarak paylaşılır.
                carousel_paths = content.get("carousel_paths") if post_type == "post" else None

                if carousel_paths and len(carousel_paths) >= 2:
                    media_urls = []
                    for slide_path in carousel_paths:
                        slide_url = self.ig_client.upload_media_to_host(slide_path)
                        if not slide_url:
                            logger.warning(f"Carousel slaytı yüklenemedi: {slide_path}")
                            break
                        media_urls.append(slide_url)

                    if len(media_urls) < 2:
                        self._safe_update_schedule_status(item["id"], "failed")
                        self._notify_publish_failure(
                            content, post_type,
                            "Carousel slaytları hosting'e yüklenemedi")
                        continue

                    result = self.ig_client.publish_carousel(
                        image_urls=media_urls,
                        caption=content.get("caption", ""),
                        hashtags=content.get("hashtags", [])
                    )
                else:
                    # Medyayı hosting'e yükle
                    media_url = self.ig_client.upload_media_to_host(content["media_path"])
                    if not media_url:
                        logger.warning(f"Medya yüklenemedi: {content['media_path']}")
                        self._safe_update_schedule_status(item["id"], "failed")
                        self._notify_publish_failure(
                            content, post_type, "Medya hosting'e yüklenemedi")
                        continue

                    # Paylaşım türüne göre yayınla
                    result = {}
                    if post_type == "post":
                        result = self.ig_client.publish_post(
                            image_url=media_url,
                            caption=content.get("caption", ""),
                            hashtags=content.get("hashtags", [])
                        )
                    elif post_type == "story":
                        result = self.ig_client.publish_story(media_url=media_url)
                    elif post_type == "reels":
                        result = self.ig_client.publish_reels(
                            video_url=media_url,
                            caption=content.get("caption", ""),
                            hashtags=content.get("hashtags", [])
                        )

            except Exception as e:
                # Beklenmeyen istisna da SESSİZ kalmamalı. Örnek: Meta 200
                # döner ama gövde JSON değilse `response.json()` ValueError
                # fırlatır; bu `requests.RequestException` olmadığı için
                # `_post_container` içinde yakalanmaz ve buraya düşer.
                logger.error(f"Paylaşım hatası [schedule_id={item['id']}]: {e}")
                self._safe_update_schedule_status(item["id"], "failed")
                self._notify_publish_failure(
                    locals().get("content") or {},
                    item["post_type"], f"Beklenmeyen hata: {e}")
                continue

            # Sonucu kaydet. Instagram API çağrısı burada zaten TAMAMLANDI —
            # aşağısı sadece SONUCU veritabanına yazma adımı. Özellikle
            # başarılı bir yayının kaydı kaybolursa (gerçek olay: "database
            # is locked") gerçekten yayınlanmış bir gönderi "failed"
            # görünüyor ve bir sonraki döngüde TEKRAR paylaşılma riski
            # doğuyordu. Bu yüzden bu adım agresif şekilde yeniden denenir.
            if result.get("success"):
                recorded = self._record_publish_result(
                    item, post_type, status="published",
                    media_id=result.get("media_id"), permalink=result.get("permalink")
                )
                if recorded:
                    logger.info(f"✅ Paylaşıldı: [{post_type}] {content.get('news_title', '')[:50]}")
                else:
                    logger.critical(
                        f"KRİTİK: schedule_id={item['id']} content_id={item['content_id']} "
                        f"Instagram'a BAŞARIYLA yayınlandı (media_id={result.get('media_id')}) "
                        f"ama veritabanına yazılamadı — MANUEL KONTROL GEREKİYOR. "
                        f"Bu kayıt 'pending' kalabilir, bir sonraki kontrolde YENİDEN "
                        f"paylaşılmasını önlemek için elle düzeltilmeli."
                    )
            else:
                hata = result.get("error", "Bilinmeyen hata")
                self._record_publish_result(
                    item, post_type, status="failed", error_message=hata
                )
                logger.error(f"❌ Paylaşım başarısız: {hata}")
                # Başarısızlık SESSİZ kalmamalı. Kullanıcı Telegram'dan
                # "✅ Onayla"ya basıyor, işlem arka planda düşüyor ve hiçbir
                # geri bildirim almıyordu — gönderi Instagram'da olmadığı
                # halde onaylandığını sanıyor. 7 Ağustos 2026'da onaylanan
                # 4 içeriğin 2'si böyle kayboldu; 1 Ağustos'ta da aynısı
                # olmuş ve fark edilmesi altı gün almıştı.
                self._notify_publish_failure(content, post_type, hata)

                # Meta 15 Ağustos 2026'dan beri feed post yayınını API
                # üzerinden kalıcı olarak engelliyor (code 4 / subcode
                # 2207051 — bkz. proje hafızası: instagram_feed_publish_
                # blocked). 23 Ağustos'ta bu engel GENİŞLEDİ: artık container
                # oluşturmanın İLK adımında bile "API access blocked" (code
                # 200) hatası alınıyor ve story da bundan etkileniyor (23
                # Ağustos'tan beri tek bir story bile yayınlanamadı — eskiden
                # sadece post etkileniyordu). Reels ayrı bir elle-paylaşım
                # yoluna zaten sahip (bkz. send_reels_for_manual_publish),
                # bu yüzden yalnızca post ve story için.
                if post_type in ("post", "story"):
                    fallback_message_id = telegram_bot.send_manual_publish_fallback(content)
                    if fallback_message_id:
                        self.db.set_content_telegram_message(item["content_id"], fallback_message_id)

    def _notify_publish_failure(self, content: dict, post_type: str, hata: str) -> None:
        """Yayın başarısızlığını Telegram'a bildir (bildirim hatası yutulur)."""
        try:
            from config import TELEGRAM_CHAT_ID
            from src import telegram_bot
            if not TELEGRAM_CHAT_ID:
                return
            baslik = (content.get("summary_text")
                      or content.get("news_title") or "")[:70]
            telegram_bot._send_text(
                TELEGRAM_CHAT_ID,
                f"❌ Paylaşım başarısız\n\n"
                f"Tür: {post_type}\n"
                f"İçerik: {baslik}\n"
                f"Sebep: {hata[:200]}\n\n"
                f"İçerik 'onaylandı' durumunda kaldı, Instagram'a çıkmadı."
            )
        except Exception as e:
            logger.warning(f"Paylaşım hatası bildirimi gönderilemedi: {e}")

    def _safe_update_schedule_status(self, schedule_id: int, status: str, max_retries: int = 3):
        """update_schedule_status'u geçici 'database is locked' hatalarına karşı yeniden dener."""
        for attempt in range(max_retries):
            try:
                self.db.update_schedule_status(schedule_id, status)
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Zamanlama durumu güncellenemedi (deneme {attempt + 1}/{max_retries}): {e}")
                    time.sleep(2 * (attempt + 1))
                else:
                    logger.error(f"Zamanlama durumu güncellenemedi [schedule_id={schedule_id}]: {e}")
        return False

    def _record_publish_result(self, item, post_type, status, media_id=None,
                                permalink=None, error_message=None, max_retries: int = 5):
        """
        Yayın sonucunu (başarı/başarısız) veritabanına yazar; geçici kilit
        hatalarına karşı artan bekleme ile yeniden dener. Başarılı bir
        Instagram yayınının kaydı, tek seferlik bir yazma hatası yüzünden
        kaybolmamalı (bkz. add_publish_record'daki nested-connection kilit
        vakası — kaynak zaten düzeltildi, ama başka süreçlerden (telegram_bot,
        dashboard) gelebilecek eşzamanlı yazışlara karşı da dayanıklı olmalı).
        """
        for attempt in range(max_retries):
            try:
                self.db.update_schedule_status(item["id"], status)
                self.db.add_publish_record(
                    content_id=item["content_id"],
                    post_type=post_type,
                    status="success" if status == "published" else "failed",
                    instagram_media_id=media_id,
                    instagram_permalink=permalink,
                    error_message=error_message,
                )
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Yayın sonucu kaydedilemedi (deneme {attempt + 1}/{max_retries}): {e}")
                    time.sleep(2 * (attempt + 1))
                else:
                    logger.error(f"Yayın sonucu kaydedilemedi [schedule_id={item['id']}]: {e}")
        return False

    # =============================================
    # OTOMATİK ZAMANLAMA
    # =============================================

    def setup_schedule(self):
        """Günlük otomatik zamanlama kur."""
        logger.info("⏰ Zamanlama kuruluyor...")

        # Haber toplama — günde bir kez yeterli (RSS/API kaynakları zaten
        # dakikalar içinde tükeniyor, sık çekmek tekrar/kota israfı olurdu).
        schedule_lib.every().day.at(SCHEDULE["collect_news"]).do(
            self._safe_run, self.collect_news, "Haber Toplama"
        )

        # İçerik İşleME ve Medya Üretimi — günde ÜÇ kez (sabah/öğle/akşam),
        # bildirimle aynı ritimde. Eskiden ikisi de günde TEK sefer
        # (haber toplamadan +30/+60dk) çalışıyordu.
        #
        # Olay (10 Ağustos 2026): sabah 09:00'daki TEK denemede onay
        # kuyruğu tavanın üstündeydi (28 ≥ 16), üretim frenle durduruldu.
        # Kuyruk gün İÇİNDE (kullanıcının onay/red işlemleriyle) 11'e
        # düştü — yani öğleden sonra üretime yeniden başlanabilirdi — ama
        # bir sonraki deneme fırsatı YARIN 09:00'a kadar yoktu. Sonuç:
        # kullanıcı "bugün Telegram'da ve dashboard'da hiç yeni içerik
        # oluşmadı" dedi, ki tamamen doğruydu — fren kendi kendine
        # açılmıyor, sadece BİR SONRAKİ DENEMEDE fark ediliyor.
        #
        # Sabah slotu haber toplamayla senkron kalıyor (yeni haberi hemen
        # işlemek için); öğle/akşam slotları BİLDİRİM saatlerinden hemen
        # önce — üretilen içerik aynı turun bildirimiyle Telegram'a gitsin.
        # Sık çağrının maliyeti düşük: kuyruk zaten doluysa fren aynı
        # şekilde anında "duraklatıldı" deyip çıkıyor (bkz.
        # content_processor.backlog_is_full), boşuna iş yapılmıyor.
        process_slots = (
            self._add_minutes(SCHEDULE["collect_news"], 30),
            self._add_minutes(SCHEDULE["notify_noon"], -30),
            self._add_minutes(SCHEDULE["notify_evening"], -30),
        )
        media_slots = (
            self._add_minutes(SCHEDULE["collect_news"], 60),
            self._add_minutes(SCHEDULE["notify_noon"], -15),
            self._add_minutes(SCHEDULE["notify_evening"], -15),
        )
        for saat in process_slots:
            schedule_lib.every().day.at(saat).do(
                self._safe_run, self.process_content, "İçerik İşleme")
        for saat in media_slots:
            schedule_lib.every().day.at(saat).do(
                self._safe_run, self.generate_all_media, "Medya Üretimi")

        # APPROVAL_MODE'a göre: otomatik zamanlama ya da Telegram onay bildirimi.
        # Kullanıcı isteği: günde tek sefer yerine sabah/öğle/akşam 3 kez
        # kontrol edilsin — medyası ilk turda hazır olmayan ya da 20'lik
        # gönderim limitine takılan içerikler de aynı gün içinde ulaşsın.
        approval_job = self.auto_schedule_content if APPROVAL_MODE == "auto" else self.notify_pending_approvals
        approval_job_name = "Otomatik Zamanlama" if APPROVAL_MODE == "auto" else "Telegram Onay Bildirimi"
        for notify_time in (SCHEDULE["notify_morning"], SCHEDULE["notify_noon"], SCHEDULE["notify_evening"]):
            schedule_lib.every().day.at(notify_time).do(self._safe_run, approval_job, approval_job_name)

        # Cevapsız onay hatırlatması — SADECE Telegram onay akışında anlamlı
        # (auto modda zaten Telegram'a hiç onay isteği gitmiyor, hatırlatacak
        # bir şey yok). Sabah bildiriminden 30dk sonra, günde bir kez: amaç
        # kullanıcının gününe "az önce ne geldi" ile değil, "unuttuğun bir
        # şey var mı" ile de başlayabilmesi. Bkz. send_reminders.
        if APPROVAL_MODE != "auto":
            schedule_lib.every().day.at(
                self._add_minutes(SCHEDULE["notify_morning"], 30)
            ).do(self._safe_run, self.send_reminders, "Onay Hatırlatması")

        # Paylaşımlar — kullanıcı geri bildirimi: "ben onay verince otomatik
        # paylaş, aksi halde günde 2-3 içerik anca paylaşılır". Eskiden
        # publish_scheduled sadece günde 5 sabit saatte çalışıyordu; manuel
        # onay artık "hemen" (immediate_schedule_time) zamanlandığından, bu
        # kontrolün de sık çalışması gerekiyor — yoksa onaylanan içerik bir
        # sonraki sabit saate kadar (saatlerce) beklerdi. get_pending_scheduled
        # zaten sadece "scheduled_time <= şimdi" olanları getirdiğinden, sık
        # çalıştırmak hem manuel onayı hızlandırıyor hem de slot-tabanlı
        # (otomatik onay modu) içerikleri de olduğu gibi zamanında yakalıyor.
        schedule_lib.every(5).minutes.do(
            self._safe_run, self.publish_scheduled, "Paylaşım Kontrolü"
        )

        # Sağlık kontrolü — SABİT saatlerde, "6 saatte bir" değil.
        #
        # Gerçek olay: Telegram bağlantısı 2+ saat koptu ve bu ancak ertesi
        # gün loglara elle bakılınca fark edildi; o süre boyunca onay
        # butonları çalışmıyordu. Kontrol bunun için var.
        #
        # Ama `every(6).hours` ilk çalışmasını KURULUMDAN 6 saat sonrasına
        # yazıyor (schedule kütüphanesinin davranışı, doğrulandı) ve telafi
        # mekanizması bu işi kapsamıyor — `find_missed_jobs` yalnızca
        # `unit == "days"` ve `at_time` dolu işlere bakıyor. Yani her deploy
        # sayacı sıfırlıyordu ve deploy'lar 6 saatten sık olduğu sürece
        # sağlık kontrolü HİÇ çalışmıyordu. 8 deploy geçen bir günde
        # sistemin tek alarm mekanizması tam da en çok gerektiği anda
        # susuyordu.
        #
        # Sabit saatler iki sorunu birden çözüyor: yeniden başlatma sayacı
        # sıfırlamıyor ve kaçırılan tur telafi kapsamına giriyor.
        for saat in HEALTHCHECK_TIMES:
            schedule_lib.every().day.at(saat).do(
                self._safe_run, self.run_healthcheck, "Sağlık Kontrolü", saat
            )

        # Günlük derleme — akşama yakın, günün haberlerinin çoğu toplandıktan
        # sonra. Feed kotasını artırmadan kapsamı büyüten tek mekanizma.
        if ROUNDUP_ENABLED:
            schedule_lib.every().day.at(ROUNDUP_TIME).do(
                self._safe_run, self.create_daily_roundup, "Günlük Derleme"
            )

        # Günlük bakım: yedekleme → satır temizliği → VACUUM → disk retention.
        # Eskiden burada yalnızca `cleanup_old_data(30)` vardı; o da sadece
        # news_items SATIRLARINI siliyordu. Diskteki üretilmiş medyaya kimse
        # dokunmadığı için output/ 8 günde 4.3 GB'a çıkmıştı (2026-08-03
        # denetimi) ve veritabanının hiç yedeği yoktu. Bkz. src/maintenance.py.
        schedule_lib.every().day.at("02:00").do(
            self._safe_run, self.run_maintenance, "Günlük Bakım"
        )

        # Instagram token yenileme kontrolü
        schedule_lib.every().day.at("03:00").do(
            self._safe_run, self.token_manager.check_and_refresh_if_needed, "Token Yenileme"
        )

        # Performans (insights) senkronizasyonu
        schedule_lib.every().day.at("04:00").do(
            self._safe_run, self.sync_insights, "Insights Senkronizasyonu"
        )

        # Kayıt anahtarları işin kendi saatinden türetilsin (telafi için).
        self._tag_daily_jobs_with_slot()

        logger.info("✅ Zamanlama kuruldu:")
        for job in schedule_lib.get_jobs():
            logger.info(f"   ⏰ {job}")

    def run_scheduler(self):
        """Zamanlayıcıyı sürekli çalıştır."""
        self.setup_schedule()

        # Yeniden başlatma o günün işlerini düşürmesin. Telafi işleri döngüden
        # ÖNCE toplu değil, döngünün İÇİNDE birer birer çalıştırılıyor: her
        # telafi işinden sonra run_pending() tekrar çağrılıyor, böylece
        # "Paylaşım Kontrolü" gibi sık işler telafi sürerken de çalışabiliyor.
        # Toplu çalıştırma denendi ve ölçüldü: tek bir "İçerik İşleme" işi 5+
        # dakika sürdü ve o süre boyunca döngü hiç başlamadı.
        bekleyen_telafi = self.find_missed_jobs()
        if bekleyen_telafi:
            logger.info(f"⏪ {len(bekleyen_telafi)} kaçırılan iş sıraya alındı.")

        logger.info("🔄 Zamanlayıcı çalışıyor... (Durdurmak için Ctrl+C)")

        try:
            while True:
                schedule_lib.run_pending()

                if bekleyen_telafi:
                    func, task_name, slot = bekleyen_telafi.pop(0)
                    logger.info(f"⏪ Kaçırılan iş telafi ediliyor: {task_name} ({slot})")
                    self._safe_run(func, task_name, slot)
                    # Beklemeden döngü başına dön: sıradaki telafi işine
                    # geçmeden önce zamanı gelmiş normal işlere bakılsın.
                    continue

                time.sleep(30)  # Her 30 saniyede kontrol et
        except KeyboardInterrupt:
            logger.info("⏹️ Zamanlayıcı durduruldu.")

    # =============================================
    # YARDIMCI METOTLAR
    # =============================================

    def _safe_run(self, func, task_name: str = "", slot: str = ""):
        """Hata yakalayarak güvenli çalıştırma; başarılı çalışmayı kaydeder.

        Çalışma tarihinin kaydedilmesi telafi mekanizması için gerekli
        (bkz. `catch_up_missed_jobs`): süreç yeniden başladığında hangi günlük
        işin bugün gerçekten çalıştığını başka türlü bilemiyoruz.
        """
        try:
            logger.info(f"▶️ {task_name} başlıyor...")
            result = func()

            # ATLANAN iş "çalıştı" sayılmamalı. İş kilidi alınamadığında
            # `process_content`/`generate_all_media` istisna değil normal bir
            # sözlük döndürüyor; burada ayırt edilmezse `_mark_job_ran`
            # çalışır, `find_missed_jobs` de "bugün çalıştı" görüp atlar.
            # "İçerik İşleme" günde TEK slotta kayıtlı olduğu için sonuç,
            # o günün tek turunun sessizce kaybolması olurdu — kilidin
            # engellediği mükerrer üretimden daha kötü bir arıza.
            if isinstance(result, dict) and result.get("skipped_already_running"):
                logger.info(f"⏭️ {task_name} atlandı (kilit başkasında); "
                            f"çalıştı olarak İŞARETLENMEDİ.")
                return result

            logger.info(f"✅ {task_name} tamamlandı.")
            self._mark_job_ran(task_name, slot)
            return result
        except Exception as e:
            logger.error(f"❌ {task_name} hatası: {e}")
            return None

    def _tag_daily_jobs_with_slot(self):
        """Her günlük işin çalışma kaydını kendi saatiyle etiketle.

        Saat, işin KENDİSİNDEN (`job.at_time`) türetiliyor; kayıt yerlerine
        elle yazılmıyor. Sebep: elle yazılsaydı `.at("13:00")` değeri
        değiştiğinde etiket eskide kalır ve telafi mekanizması o işi sessizce
        "hiç çalışmamış" sanıp her açılışta tekrar çalıştırırdı. Türetmek bu
        ikisinin ayrışmasını imkânsız kılıyor.
        """
        import functools

        for job in schedule_lib.get_jobs():
            at_time = getattr(job, "at_time", None)
            if at_time is None or job.unit != "days":
                continue
            args = getattr(job.job_func, "args", ())
            if len(args) != 2:
                continue
            job.job_func = functools.partial(
                self._safe_run, args[0], args[1], at_time.strftime("%H:%M")
            )

    @staticmethod
    def _job_key(task_name: str, slot: str = "") -> str:
        """Bir işin 'bugün çalıştı' kaydının anahtarı.

        Saat dilimi (slot) anahtara giriyor çünkü bazı işler günde birden çok
        kez, farklı saatlerde kayıtlı (ör. 'Telegram Onay Bildirimi' 09:45,
        13:00 ve 19:00'da). Yalnızca ada göre kaydetseydik sabahki çalışma
        öğlen ve akşamki turları da 'çalıştı' saydırırdı.
        """
        return f"job_last_run:{task_name}@{slot}" if slot else f"job_last_run:{task_name}"

    def _mark_job_ran(self, task_name: str, slot: str = ""):
        try:
            self.db.set_setting(self._job_key(task_name, slot),
                                datetime.now().strftime("%Y-%m-%d"))
        except Exception as e:
            # Kayıt tutulamaması işi başarısız saymamalı.
            logger.warning(f"İş çalışma kaydı yazılamadı [{task_name}]: {e}")

    def catch_up_missed_jobs(self) -> list[str]:
        """Bugün saati geçtiği hâlde hiç çalışmamış günlük işleri şimdi çalıştır.

        Neden gerekli: `schedule` kütüphanesi işleri süreç belleğinde tutuyor.
        Servis yeniden başladığında zamanlama sıfırdan kuruluyor ve saati
        ÇOKTAN GEÇMİŞ günlük işler ertesi güne atılıyor — yani o gün hiç
        çalışmıyorlar.

        Ölçülen etki (4 Ağustos 2026): sekiz deploy sonrası "Medya Üretimi" ve
        "Günlük Bakım" o gün HİÇ çalışmadı, "Telegram Onay Bildirimi" üç yerine
        iki kez çalıştı. Kullanıcı bunu "Telegram'dan sadece 1 haber bildirimi
        geldi" olarak fark etti. Sistem kararlıydı (sıfır çökme) ama eksik
        teslim ediyordu.

        Telafi edilen iş adlarının listesini döndürür.
        """
        telafi = []
        for func, task_name, slot in self.find_missed_jobs():
            logger.info(f"⏪ Kaçırılan iş telafi ediliyor: {task_name} ({slot})")
            self._safe_run(func, task_name, slot)
            telafi.append(f"{task_name}@{slot}")

        if telafi:
            logger.info(f"⏪ {len(telafi)} kaçırılan iş telafi edildi.")
        return telafi

    def find_missed_jobs(self) -> list[tuple]:
        """Bugün saati geçtiği hâlde hiç çalışmamış günlük işleri bul.

        Tespit ile çalıştırma ayrı: `run_scheduler` bu listeyi ana döngüye
        SERPİŞTİREREK çalıştırıyor, hepsini peş peşe değil. Sebep ölçüldü —
        telafi işleri döngüden önce toplu çalıştırıldığında "İçerik İşleme"
        tek başına 5+ dakika sürdü (15 haber × Gemini çağrıları, aralarında
        kota koruması için 5sn bekleme) ve o süre boyunca 5 dakikada bir
        çalışması gereken "Paylaşım Kontrolü" hiç çalışmadı. Yani telafi
        mekanizması, çözmeye çalıştığı gecikmenin bir benzerini üretiyordu.
        """
        simdi = datetime.now()
        bugun = simdi.strftime("%Y-%m-%d")
        eksik = []

        for job in schedule_lib.get_jobs():
            at_time = getattr(job, "at_time", None)
            if at_time is None or job.unit != "days":
                continue

            args = getattr(job.job_func, "args", ())
            if len(args) < 2:
                continue
            func, task_name = args[0], args[1]
            slot = at_time.strftime("%H:%M")

            # Saati henüz gelmemişse normal zamanlayıcı çalıştıracak.
            if simdi.time() < at_time:
                continue
            if self.db.get_setting(self._job_key(task_name, slot)) == bugun:
                continue

            eksik.append((func, task_name, slot))

        return eksik

    @staticmethod
    def _add_minutes(time_str: str, minutes: int) -> str:
        """Saat string'ine dakika ekle."""
        hours, mins = map(int, time_str.split(":"))
        total_mins = hours * 60 + mins + minutes
        new_hours = (total_mins // 60) % 24
        new_mins = total_mins % 60
        return f"{new_hours:02d}:{new_mins:02d}"
