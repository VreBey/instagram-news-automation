"""
Veritabanı Yönetim Modülü
SQLite ile haber, içerik ve paylaşım verilerini yönetir.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_PATH, DATA_DIR, DEDUP_TITLE_SIMILARITY_THRESHOLD, DEDUP_WINDOW_HOURS,
    RELEVANCE_RECENCY_DECAY_PER_DAY,
    MAX_NEWS_PER_SOURCE_RATIO, MIN_NEWS_PER_SOURCE,
)
from src.relevance import calculate_relevance
from src.content_language import publication_from_url

logger = logging.getLogger(__name__)


# =============================================
# ZAMAN KURALI — bu dosyadaki tek doğru referans
#
# Bu şemada İKİ zaman evreni var:
#
#   YEREL (Python, TZ=Europe/Istanbul)   UTC (SQLite datetime('now'))
#   ─────────────────────────────────    ───────────────────────────
#   publish_history.published_at         news_items.collected_at
#   scheduled_posts.scheduled_time       processed_content.created_at
#
# `published_at` bilerek yerel yazılıyor (gerekçe `add_publish_record`
# içinde). Ama ona karşı yapılan karşılaştırmaların bir kısmı SQLite'ın
# `datetime('now')`/`DATE('now')` değerini kullanıyordu — o UTC. Servisler
# UTC+3'te çalıştığı için aradaki 3 saat gerçek hatalara yol açıyordu:
#
#   * `/huni` raporundaki "bugün yayınlanan" sayısı, yerel 00:00–03:00
#     arasında YANLIŞ GÜNÜ ölçüyordu (rapor gerçek veriden geliyor ama
#     etiketiyle uyuşmuyor — bu projede kabul edilemez bir sınıf).
#   * Insights penceresi 24 saat yerine fiilen 27 saat açık kalıyor ve
#     son 3 saatte garantili başarısız API çağrısı üretiyordu.
#
# Kural: `published_at` / `scheduled_time` ile karşılaştırırken ZAMANI
# PYTHON'DAN PARAMETRE OLARAK GEÇİR. `collected_at` / `created_at` için
# SQLite'ın kendi `now`'ı doğru; ikisi de UTC.
# =============================================

def _local_now(offset_days: float = 0, offset_hours: float = 0) -> str:
    """Yerel saatle "YYYY-MM-DD HH:MM:SS" — yerel yazılan sütunlarla karşılaştırmak için."""
    an = datetime.now() + timedelta(days=offset_days, hours=offset_hours)
    return an.strftime("%Y-%m-%d %H:%M:%S")


def _local_today(offset_days: float = 0) -> str:
    """Yerel saatle "YYYY-MM-DD"."""
    return (datetime.now() + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _apply_source_diversity(rows: list[dict], limit: int) -> list[dict]:
    """Öncelik sırasını koruyarak tek kaynağın partiyi ele geçirmesini engelle.

    Neden gerekli: seçim tazelikten puana çevrildikten sonra ölçüldü — bir
    turluk ilk 30 haberin 16'sı TEK kaynaktan geliyordu, kuyrukta 33 farklı
    kaynak varken ilk 30'da yalnızca 9'u temsil ediliyordu. Puan sıralaması
    açlık problemini çözmemiş, başka bir kaynağa taşımıştı.

    Sınırın asıl gerekçesi puanın kaynak düzeyinde onayı öngörmemesi:
    Heavy.com ortalama 0.767 puanla %0 onay alıyor, GlobeNewswire 0.683 ile
    yine %0. Bu kaynaklar puanı "kazanıyor" ama yayınlanabilir içerik
    üretmiyor; sınırsız bırakılsa slotları doldurup gerçek haberi dışarıda
    bırakırlardı.

    Tavan doldurulamazsa (ör. kuyrukta gerçekten tek kaynak var) kalan
    slotlar ikinci turda sınır gözetilmeden doldurulur — çeşitlilik uğruna
    partiyi eksik döndürmek, işi boşa harcamak olurdu.
    """
    if limit <= 0 or not rows:
        return []

    per_source_cap = max(MIN_NEWS_PER_SOURCE, int(limit * MAX_NEWS_PER_SOURCE_RATIO))

    selected: list[int] = []
    counts: dict = {}
    overflow: list[int] = []

    for index, row in enumerate(rows):
        source = row.get("source_name") or "?"
        if len(selected) < limit and counts.get(source, 0) < per_source_cap:
            selected.append(index)
            counts[source] = counts.get(source, 0) + 1
        else:
            overflow.append(index)

    # Sınır yüzünden parti dolmadıysa, elenenlerden öncelik sırasıyla tamamla.
    if len(selected) < limit:
        selected.extend(overflow[: limit - len(selected)])

    # Öncelik sırasına geri dön: doldurma adımı seçilenleri sona eklediği için
    # sıra bozulmuş olabilir. `rows` zaten önceliğe göre sıralı geldiğinden
    # indekse göre sıralamak orijinal sırayı geri verir.
    return [rows[i] for i in sorted(selected)]


class Database:
    """SQLite veritabanı yöneticisi."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        # data dizinini oluştur
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """
        Thread-safe bağlantı yönetimi.

        timeout=30: Bu proje birden fazla bağımsız süreç (dashboard, telegram_bot,
        scheduler/--publish, mcp_server) aynı SQLite dosyasını eşzamanlı kullanıyor.
        Python'ın varsayılan 5 saniyelik kilit bekleme süresi, telegram_bot'un
        sürekli long-polling ile veritabanını sık sorgulaması altında yetersiz
        kalıp "database is locked" hatasına yol açtı — gerçek bir üretim
        vakasında bu, başarıyla yayınlanmış içeriklerin durumunun veritabanına
        hiç yazılamamasına neden oldu. 30 saniye, bu düşük-frekanslı yazma
        yükü için güvenli bir bekleme payı sağlıyor.
        """
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Veritabanı hatası: {e}")
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Veritabanı tablolarını oluştur."""
        with self._get_connection() as conn:
            conn.executescript("""
                -- Ham haberler tablosu
                CREATE TABLE IF NOT EXISTS news_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    url TEXT UNIQUE NOT NULL,
                    source_name TEXT,
                    source_url TEXT,
                    category TEXT NOT NULL CHECK(category IN ('ai', 'gaming')),
                    language TEXT DEFAULT 'en',
                    image_url TEXT,
                    published_at TEXT,
                    collected_at TEXT DEFAULT (datetime('now')),
                    relevance_score REAL DEFAULT 0.0,
                    is_processed INTEGER DEFAULT 0,
                    is_used INTEGER DEFAULT 0
                );

                -- İşlenmiş içerikler tablosu
                CREATE TABLE IF NOT EXISTS processed_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    news_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL CHECK(content_type IN ('post', 'story', 'reels')),
                    caption TEXT,
                    hashtags TEXT,  -- JSON array
                    summary_text TEXT,
                    reels_script TEXT,  -- JSON
                    media_path TEXT,
                    thumbnail_path TEXT,
                    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'approved', 'rejected', 'published', 'failed')),
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (news_id) REFERENCES news_items(id) ON DELETE CASCADE
                );

                -- Paylaşım geçmişi tablosu
                CREATE TABLE IF NOT EXISTS publish_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id INTEGER NOT NULL,
                    platform TEXT DEFAULT 'instagram',
                    post_type TEXT NOT NULL CHECK(post_type IN ('post', 'story', 'reels')),
                    instagram_media_id TEXT,
                    instagram_permalink TEXT,
                    published_at TEXT DEFAULT (datetime('now')),
                    status TEXT DEFAULT 'success' CHECK(status IN ('success', 'failed', 'pending')),
                    error_message TEXT,
                    FOREIGN KEY (content_id) REFERENCES processed_content(id) ON DELETE CASCADE
                );

                -- Zamanlama tablosu
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id INTEGER NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    post_type TEXT NOT NULL CHECK(post_type IN ('post', 'story', 'reels')),
                    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'published', 'cancelled', 'failed')),
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (content_id) REFERENCES processed_content(id) ON DELETE CASCADE
                );

                -- İndeksler
                CREATE INDEX IF NOT EXISTS idx_news_category ON news_items(category);
                CREATE INDEX IF NOT EXISTS idx_news_collected ON news_items(collected_at);
                CREATE INDEX IF NOT EXISTS idx_news_used ON news_items(is_used);
                CREATE INDEX IF NOT EXISTS idx_content_status ON processed_content(status);
                CREATE INDEX IF NOT EXISTS idx_content_type ON processed_content(content_type);
                CREATE INDEX IF NOT EXISTS idx_schedule_time ON scheduled_posts(scheduled_time);
                CREATE INDEX IF NOT EXISTS idx_schedule_status ON scheduled_posts(status);
            """)
            logger.info("Veritabanı tabloları başarıyla oluşturuldu.")
        self._migrate_schema()

    def _migrate_schema(self):
        """
        Var olan veritabanlarına yeni kolonları eklemek için defansif migrasyon.
        SQLite'ta 'ADD COLUMN IF NOT EXISTS' olmadığından her ALTER TABLE
        ayrı ayrı denenir ve zaten mevcutsa sessizce atlanır.
        """
        migrations = [
            ("news_items", "mention_count", "INTEGER DEFAULT 1"),
            # Tazelik nedeniyle işlenmeden emekliye ayrılan haberin zamanı.
            # Kendi kolonu olmasının sebebi: bunlar da is_processed=1 /
            # relevance_score=0 ile işaretleniyor, yani AYIRT EDİCİ bir iz
            # olmazsa gerçekten "0 puan almış" haberlerle karışırlar. 2026-08-03
            # denetiminde tam bu karışıklık yaşandı — 31 Temmuz kurulum günündeki
            # bozuk sıfırlar, puanlama yanlılığı sanıldı.
            ("news_items", "expired_at", "TEXT"),
            ("scheduled_posts", "source", "TEXT DEFAULT 'manual'"),
            ("processed_content", "telegram_message_id", "INTEGER"),
            ("processed_content", "manual_image_prompt_message_id", "INTEGER"),
            ("processed_content", "manual_image_path", "TEXT"),
            ("processed_content", "manual_video_prompt_message_id", "INTEGER"),
            ("processed_content", "carousel_paths", "TEXT"),
            ("processed_content", "list_items", "TEXT"),
            # Günlük derlemeye dahil edilmiş taslak. Ayrı kolon olmasının iki
            # sebebi var: (1) `status` CHECK kısıtlı, yeni değer eklenemez;
            # (2) 'rejected' işaretlemek puanlama ölçümünün YER GERÇEĞİNİ
            # bozardı — kullanıcı o içeriği reddetmedi, derlemeye girdiği için
            # tekil yayınlanmadı. Bkz. scripts/measure_relevance.py.
            ("processed_content", "used_in_roundup", "INTEGER DEFAULT 0"),
            # Görselin arka planı hangi kaynaktan geldi: manual / article_image
            # / og_image / game_cover / stock_photo / gradient.
            # "Gerçek görüntü kullanmıyor" şikayeti geldiğinde bunu ölçecek
            # hiçbir veri yoktu; zincir seçtiği kaynağı kaydetmiyordu.
            ("processed_content", "background_source", "TEXT"),
            # Bayatladığı için kuyruktan düşürülen taslağın damgası. `status`
            # 'rejected' oluyor (CHECK yeni değere izin vermiyor) ama bunlar
            # KULLANICININ reddettikleriyle karışmamalı: biri içerik kalitesi
            # sinyali, diğeri yalnızca kapasite sinyali.
            ("processed_content", "expired_at", "TEXT"),
            # NVIDIA embedding vektörü (JSON dizi), tekilleştirmede ikinci
            # basamak için önbelleklenmiş. SequenceMatcher'ın kaçırdığı
            # paraphrase'leri yakalamak için var (bkz. find_similar_recent).
            # NULL olması normal: eski satırlar hiç embed edilmedi, yeni bir
            # aday sadece EKLENDİĞİNDE hesaplanıyor (add_news'e geçirilirse).
            ("news_items", "embedding", "TEXT"),
            # Bir onay isteğinin Telegram'a EN SON ne zaman gönderildiği
            # (ilk gönderim ya da hatırlatma — ikisi de bunu günceller).
            # NULL: hiç gönderilmemiş VEYA bu migrasyondan önce gönderilmiş
            # eski satır — get_stale_pending_approvals ikisini de created_at
            # ile telafi eder. Bkz. get_stale_pending_approvals.
            ("processed_content", "last_notified_at", "TEXT"),
        ]
        with self._get_connection() as conn:
            for table, column, definition in migrations:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    logger.info(f"Migrasyon uygulandı: {table}.{column}")
                except sqlite3.OperationalError:
                    # Kolon zaten mevcut
                    pass

            # Basit key-value ayar deposu (ör. yenilenen Instagram token'ı)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)

            # Medya performans anlık görüntüleri (append-only — trend takibi için)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publish_history_id INTEGER NOT NULL,
                    impressions INTEGER,
                    reach INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    saves INTEGER,
                    shares INTEGER,
                    engagement_rate REAL,
                    fetched_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (publish_history_id) REFERENCES publish_history(id) ON DELETE CASCADE
                )
            """)

            # Harici araştırma bulguları (ör. Gemini Spark'ın MCP üzerinden gönderdiği)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS external_research (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL DEFAULT 'gemini_spark',
                    title TEXT NOT NULL,
                    summary TEXT,
                    content TEXT,
                    topic TEXT,
                    source_url TEXT,
                    received_at TEXT DEFAULT (datetime('now')),
                    promoted_news_id INTEGER,
                    FOREIGN KEY (promoted_news_id) REFERENCES news_items(id) ON DELETE SET NULL
                )
            """)

    # =============================================
    # HABER İŞLEMLERİ
    # =============================================

    def add_news(self, title: str, url: str, category: str,
                 description: str = None, source_name: str = None,
                 source_url: str = None, language: str = "en",
                 image_url: str = None, published_at: str = None,
                 embedding: list | None = None) -> int | None:
        """Yeni haber ekle. Zaten varsa None döndürür.

        Önem puanı BURADA, toplama anında hesaplanır. Eskiden yalnızca haber
        işlenirken hesaplanıyordu — yani seçimden sonra — ve seçim tazeliğe
        göre yapıldığı için puanın hangi haberin işleneceğine hiç etkisi
        yoktu. Ölçüm sonucu: yüksek değerli haberin %34'ü hiç işlenmeden
        atılıyordu. Puanlama saf Python, API çağırmıyor; burada hesaplamanın
        maliyeti yok. Bkz. src/relevance.py.

        `embedding`: çağıran taraf `find_similar_recent` içinde tekilleştirme
        için ZATEN bir embedding hesapladıysa (bkz. news_collector._is_duplicate),
        burada AYNI vektör önbelleğe yazılır — ikinci bir NVIDIA çağrısı
        gerekmez. Verilmezse satır embedsiz kalır (sonraki bir adayın
        tekilleştirme kontrolünde bu satırla karşılaştırma yapılamaz, o
        kadar — pipeline'ı bloklamaz).
        """
        score = calculate_relevance({
            "title": title, "description": description,
            "image_url": image_url, "mention_count": 1,
        })
        embedding_json = json.dumps(embedding) if embedding else None
        with self._get_connection() as conn:
            try:
                cursor = conn.execute("""
                    INSERT INTO news_items
                    (title, description, url, source_name, source_url, category, language, image_url, published_at, relevance_score, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (title, description, url, source_name, source_url,
                      category, language, image_url, published_at, score,
                      embedding_json))
                news_id = cursor.lastrowid
                logger.info(f"Yeni haber eklendi: [{category}] {title[:60]}...")
                return news_id
            except sqlite3.IntegrityError:
                logger.debug(f"Haber zaten mevcut: {url}")
                return None

    def set_background_source(self, content_id: int, source: str):
        """Görselin arka planının hangi kaynaktan geldiğini kaydet."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE processed_content SET background_source = ? WHERE id = ?",
                (source, content_id),
            )

    def get_background_source_stats(self, days: int = 7) -> dict:
        """Son N günde arka plan kaynaklarının dağılımı.

        `gradient` ve `stock_photo` "gerçek görsel bulunamadı" anlamına gelir;
        diğerleri habere ait gerçek bir görseldir. Oran düşerse görsel zinciri
        (og:image kazıma, RSS görselleri, Steam kapakları) bozulmuş demektir.
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT COALESCE(background_source, 'kayitsiz') AS kaynak,
                       COUNT(*) AS n
                FROM processed_content
                WHERE media_path IS NOT NULL
                  AND created_at >= datetime('now', ?)
                GROUP BY kaynak
                ORDER BY n DESC
            """, (f"-{days} days",)).fetchall()
            return {r["kaynak"]: r["n"] for r in rows}

    def get_news_processed_today_count(self) -> int:
        """Bugün için içerik üretilmiş DISTINCT haber sayısı.

        `process_all_news`'in günlük bütçesini hesaplamak için. `news_items`
        üzerinde "ne zaman işlendi" bilgisi tutulmadığından, o haber için
        bugün üretilmiş içerik satırlarından türetiliyor.

        Emekliye ayrılan haberler sayılmaz: onlar için içerik üretilmedi,
        yalnızca kuyruktan düşürüldüler.
        """
        with self._get_connection() as conn:
            return conn.execute("""
                SELECT COUNT(DISTINCT news_id) FROM processed_content
                WHERE DATE(created_at) = DATE('now')
            """).fetchone()[0]

    def get_unprocessed_news(self, category: str = None, limit: int = 20) -> list[dict]:
        """İşlenmemiş haberleri ÖNEM sırasıyla getir.

        Eskiden `ORDER BY collected_at DESC` idi — yani en yeniler. Toplama
        işlemeden hızlı olduğu için (günde ~75 haber toplanıyor, ~30'u
        işlenebiliyor) bu, "en son gelen kazanır" demekti ve haberin değeri
        seçime hiç girmiyordu. Ölçülen sonuç: emekliye ayrılan haberlerin
        ortalama puanı (0.728) yayınlananlardan (0.671) yüksekti.

        Simülasyon (aynı kapasite, 839 gerçek haber): yüksek değerli haberin
        yakalanma oranı tazelik sıralamasıyla %16, puan sıralamasıyla %56.

        Tazelik tamamen atılmıyor, sönüm olarak korunuyor: eşit puanlı iki
        haberden yeni olan öne geçer ve eskiyen haber gün başına
        RELEVANCE_RECENCY_DECAY_PER_DAY kadar öncelik kaybeder. Böylece bayat
        ama yüksek puanlı bir haber kuyruğu sonsuza kadar tıkamaz.

        Ayrıca tek kaynağın partiyi ele geçirmesi engellenir — gerekçe için
        bkz. `_apply_source_diversity`.
        """
        with self._get_connection() as conn:
            query = """
                SELECT *,
                       relevance_score
                       - (? * (julianday('now') - julianday(collected_at))) AS priority
                FROM news_items
                WHERE is_processed = 0
            """
            params = [RELEVANCE_RECENCY_DECAY_PER_DAY]
            if category:
                query += " AND category = ?"
                params.append(category)
            query += " ORDER BY priority DESC, collected_at DESC LIMIT ?"
            # Çeşitlilik sınırı bazı adayları eleyeceği için havuzu geniş
            # tutuyoruz; aksi halde sınır uygulandıktan sonra limitten az
            # haber dönerdi.
            params.append(limit * 5)
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]

        return _apply_source_diversity(rows, limit)

    def get_published_for_reels(self, category: str = None, limit: int = 5,
                                 days: int = 7) -> list[dict]:
        """Reels senaryosu için GERÇEKTEN YAYINLANMIŞ içerikleri getir.

        İki sorunu birden çözüyor (5 Ağustos 2026 kullanıcı bildirimi):

        1. **Tutarsızlık.** Eskiden `get_unused_news` kullanılıyordu — yani
           reels, hesapta HİÇ paylaşılmamış haberleri anlatıyordu. İzleyici
           videoda gördüğü haberi profilde bulamıyordu.

        2. **İngilizce sızıntısı.** `news_items.title` ham İngilizce kaynak
           başlık. Burada onun yerine `processed_content.summary_text`
           (üretilmiş Türkçe özet) `title` olarak döndürülüyor, böylece
           senaryo, slayt yazıları ve caption tek elden Türkçe oluyor.

        `title` alanının Türkçe özetle doldurulması bilinçli: çağıran kod
        (senaryo üretimi, slaytlar, caption) zaten `title` bekliyor, böylece
        tek bir yerde düzeltmek yetiyor.
        """
        with self._get_connection() as conn:
            query = """
                SELECT DISTINCT
                       ni.id, ni.category, ni.source_name, ni.image_url,
                       ni.relevance_score,
                       -- Görsel zincirinin TAMAMI reels'te de çalışabilsin:
                       -- `news_url` og:image basamağı, `news_title` (HAM
                       -- İngilizce başlık) Steam kapağı araması için gerekli.
                       -- İkisi de eskiden döndürülmüyordu, bu yüzden reels
                       -- slaytları beslemede görsel yoksa doğrudan jenerik
                       -- stok fotoğrafa düşüyordu.
                       ni.url AS news_url,
                       ni.title AS news_title,
                       pc.summary_text AS title,
                       MAX(ph.published_at) AS published_at
                FROM publish_history ph
                JOIN processed_content pc ON ph.content_id = pc.id
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE ph.status = 'success'
                  -- published_at YEREL yazılıyor; eşiği de Python'dan
                  -- yerel olarak geçiyoruz (bkz. dosya başındaki zaman kuralı).
                  AND ph.published_at >= ?
                  AND pc.summary_text IS NOT NULL
                  AND TRIM(pc.summary_text) != ''
                  -- Bir reels'te ANLATILMIŞ haber tekrar seçilmemeli.
                  -- create_reels_content zaten her haberi mark_news_used ile
                  -- işaretliyordu ama BURADA okunmuyordu: sonuç, her günün
                  -- reels'inin aynı yayınlanmış gönderileri yeniden
                  -- anlatması oldu (7 günde 32 reels taslağı, 23'ü elle
                  -- reddedildi; is_used=1 olan 115 haber vardı ve hiçbiri
                  -- bir şeyi engellemiyordu).
                  AND ni.is_used = 0
            """
            params = [_local_now(offset_days=-days)]

            # BEKLEYEN REELS'İN HABERLERİ DE DIŞLANIR.
            #
            # `is_used` bayrağı tek başına yetmiyor: işaretleme bilinçli
            # olarak ÜRETİM anından TESLİM anına taşındı (yayınlanmayan
            # taslaklar haber yakmasın diye, bkz. create_reels_content).
            # Ama yukarıdaki `is_used = 0` filtresi hâlâ üretimde
            # işaretlendiğini varsayıyordu. İki düzeltme birbiriyle
            # çelişince, onay bekleyen bir taslağın haberleri ertesi gün
            # yeniden seçilebilir hale geldi.
            #
            # Ölçüm (8 Ağustos 2026): bildirim penceresindeki 9 reels
            # taslağı yalnızca 4 FARKLI haber kümesi anlatıyordu; iki grup
            # birebir aynıydı (biri 4, diğeri 3 kopya).
            bekleyen = self._pending_reels_news_ids(conn)
            if bekleyen:
                query += (" AND ni.id NOT IN ("
                          + ",".join("?" * len(bekleyen)) + ")")
                params.extend(bekleyen)

            if category:
                query += " AND ni.category = ?"
                params.append(category)
            # Aynı haber hem post hem story olarak yayınlanmış olabilir;
            # reels'te iki kez anlatılmasın diye habere göre grupla.
            query += """
                GROUP BY ni.id
                ORDER BY published_at DESC
                LIMIT ?
            """
            params.append(limit)
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    @staticmethod
    def _pending_reels_news_ids(conn) -> list[int]:
        """Henüz teslim edilmemiş reels taslaklarının anlattığı haber ID'leri.

        Senaryo bir JSON metni olarak duruyor; `news_ids` alanı orada.
        Ayrıştırma Python'da yapılıyor: SQLite'ın JSON eklentisine bağımlı
        olmamak için (dağıtımlar arasında değişebiliyor) ve bozuk/eski bir
        senaryonun sorguyu düşürmemesi için.

        Alanı taşımayan ESKİ senaryolar buraya katkı veremez; onlar için
        `scripts/backfill_reels_chain.py` var.

        ONAYLI ama MEDYASIZ içerik sayılmaz. Böyle bir kayıt yayınlanamaz,
        yani onaylı durumda süresiz kalır; sayılsaydı anlattığı haberleri
        havuzdan KALICI olarak düşürürdü. Üretimde tam olarak böyle bir
        kayıt bulundu (id=467, 6 günlük, medyasız). Çözümü onun durumunu
        değiştirmek DEĞİL — kullanıcının onayını sistem geri almaz — bu
        sorguda teslim edilemeyeceğini görmek.
        """
        idler: set[int] = set()
        rows = conn.execute(
            """SELECT reels_script FROM processed_content
               WHERE content_type = 'reels'
                 AND reels_script IS NOT NULL
                 AND (status = 'draft'
                      OR (status = 'approved' AND media_path IS NOT NULL))"""
        ).fetchall()
        for r in rows:
            try:
                senaryo = json.loads(r[0])
            except (TypeError, ValueError):
                continue
            for hid in (senaryo.get("news_ids") or []):
                if isinstance(hid, int):
                    idler.add(hid)
        return sorted(idler)

    def get_unused_news(self, category: str = None, limit: int = 10) -> list[dict]:
        """Henüz paylaşılmamış haberleri getir."""
        with self._get_connection() as conn:
            query = """
                SELECT * FROM news_items 
                WHERE is_used = 0 AND is_processed = 1
            """
            params = []
            if category:
                query += " AND category = ?"
                params.append(category)
            query += " ORDER BY relevance_score DESC, collected_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def mark_news_processed(self, news_id: int, relevance_score: float = 0.0):
        """Haberi işlenmiş olarak işaretle."""
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE news_items SET is_processed = 1, relevance_score = ?
                WHERE id = ?
            """, (relevance_score, news_id))

    def mark_news_used(self, news_id: int):
        """Haberi kullanılmış olarak işaretle."""
        with self._get_connection() as conn:
            conn.execute("UPDATE news_items SET is_used = 1 WHERE id = ?", (news_id,))

    def find_similar_recent(self, title: str, category: str,
                             window_hours: int, threshold: float,
                             try_embedding: bool = True
                             ) -> tuple[int | None, list | None]:
        """Son `window_hours` içinde aynı kategoride benzer başlıklı haber var mı kontrol et.

        (eşleşen_id, embedding) döner. `embedding`, `title` için hesaplanmış
        vektördür — eşleşme bulunamadıysa ve `title` yeni bir satır olarak
        eklenecekse, bu vektör `add_news(embedding=...)`'e geçirilip ikinci
        bir NVIDIA çağrısından kaçınılabilir. Eşleşme bulunduysa (satır
        eklenmeyecek) None'dır — hesaplanmasına gerek yok.

        İKİ BASAMAK:
        1. SequenceMatcher (ücretsiz, anında) — neredeyse birebir aynı
           başlıkları yakalar. Bu basamak tek başına yeterliyse (eşleşme
           bulunduysa) NVIDIA'ya hiç gidilmez.
        2. Embedding (yalnızca 1. basamak boş dönerse) — aynı olayın farklı
           cümlelerle yazılmış hâllerini yakalar. Ölçüm (10 Ağustos 2026):
           SequenceMatcher paraphrase çiftlerinde 0.40-0.49 skorluyordu
           (eşiğin 0.82'nin çok altında, KAÇIRILIRDI), embedding aynı
           çiftlerde 0.71-0.84 verdi. Bkz. config.DEDUP_EMBEDDING_SIMILARITY_THRESHOLD.

        NVIDIA API'si yanıt vermezse (kapalı/limitli/anahtarsız) bu basamak
        sessizce atlanır — tekilleştirme 1. basamağa döner, pipeline durmaz.
        """
        from src.dedup import title_similarity

        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT id, title, embedding FROM news_items
                WHERE category = ? AND collected_at >= datetime('now', ?)
            """, (category, f"-{window_hours} hours")).fetchall()

        for row in rows:
            if title_similarity(title, row["title"]) >= threshold:
                return row["id"], None

        if not try_embedding:
            return None, None

        from src.nvidia_embed import embed_text, cosine_similarity
        from config import DEDUP_EMBEDDING_SIMILARITY_THRESHOLD

        yeni_vektor = embed_text(title)
        if yeni_vektor is None:
            return None, None

        for row in rows:
            if not row["embedding"]:
                continue
            try:
                var_olan_vektor = json.loads(row["embedding"])
            except (TypeError, ValueError):
                continue
            if cosine_similarity(yeni_vektor, var_olan_vektor) >= DEDUP_EMBEDDING_SIMILARITY_THRESHOLD:
                return row["id"], None

        return None, yeni_vektor

    def increment_mention_count(self, news_id: int):
        """Aynı haberin başka bir kaynakta da bulunduğunu işaretle.

        Puan da YENİDEN HESAPLANIR: mention_count ölçülmüş bir önem sinyali
        (d=+0.685) ve artık haberin işlenip işlenmeyeceğini belirleyen puana
        giriyor. Yalnızca sayacı artırıp puanı olduğu gibi bırakmak, çok
        kaynakta doğrulanmış bir haberi ilk görüldüğü andaki düşük puanıyla
        sıralamada bırakırdı — yani sinyal toplanır ama hiç kullanılmazdı.

        Zaten işlenmiş haberin puanına dokunulmaz: o puan artık üretilmiş
        içeriğin kaydı, sıralama girdisi değil.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT title, description, image_url, mention_count, is_processed "
                "FROM news_items WHERE id = ?",
                (news_id,)
            ).fetchone()
            if not row:
                return

            yeni_sayi = (row["mention_count"] or 1) + 1
            if row["is_processed"]:
                conn.execute(
                    "UPDATE news_items SET mention_count = ? WHERE id = ?",
                    (yeni_sayi, news_id)
                )
                return

            yeni_puan = calculate_relevance({
                "title": row["title"],
                "description": row["description"],
                "image_url": row["image_url"],
                "mention_count": yeni_sayi,
            })
            conn.execute(
                "UPDATE news_items SET mention_count = ?, relevance_score = ? WHERE id = ?",
                (yeni_sayi, yeni_puan, news_id)
            )

    # =============================================
    # İÇERİK İŞLEMLERİ
    # =============================================

    def add_content(self, news_id: int, content_type: str,
                    caption: str = None, hashtags: list = None,
                    summary_text: str = None, reels_script: dict = None,
                    media_path: str = None, thumbnail_path: str = None,
                    list_items: dict = None) -> int:
        """
        İşlenmiş içerik ekle. list_items: haber birden fazla isimlendirilmiş
        öge içeren bir LİSTEYSE (ör. "Ağustos'un en iyi 5 oyunu"), Gemini'nin
        tespit ettiği {"cover": str, "items": [{"name","detail"}, ...]}
        yapısı — generate_all_media() bunu görünce tekil görsel yerine
        kaydırmalı (carousel) görsel üretir (bkz. image_generator.py).
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO processed_content
                (news_id, content_type, caption, hashtags, summary_text, reels_script, media_path, thumbnail_path, list_items)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                news_id, content_type, caption,
                json.dumps(hashtags) if hashtags else None,
                summary_text,
                json.dumps(reels_script) if reels_script else None,
                media_path, thumbnail_path,
                json.dumps(list_items) if list_items else None
            ))
            content_id = cursor.lastrowid
            logger.info(f"İçerik oluşturuldu: [{content_type}] ID={content_id}")
            return content_id

    def get_roundup_candidates(self, limit: int = 6) -> list[dict]:
        """Günlük derlemeye girebilecek taslakları önem sırasıyla getir.

        Derleme HAM haberden değil, zaten üretilmiş taslaklardan kurulur:
        Türkçe özet (`summary_text`) hazır olduğu için ek Gemini çağrısı
        gerekmez ve birikmiş taslaklar (ölçümde 255 adet) değerlendirilmiş
        olur — üretilip hiç yayınlanmayan içerik saf israftı.

        Yalnızca 'post' taslakları alınır: hikaye metinleri feed gönderisi
        için fazla kısa ve farklı bir tonda yazılıyor.
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT pc.*, ni.title AS news_title, ni.category, ni.source_name,
                       ni.url AS news_url, ni.relevance_score, ni.image_url
                FROM processed_content pc
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE pc.status = 'draft'
                  AND pc.content_type = 'post'
                  AND COALESCE(pc.used_in_roundup, 0) = 0
                  AND pc.summary_text IS NOT NULL AND TRIM(pc.summary_text) != ''
                  -- Derlemenin KENDİSİ aday olamaz. Derleme de content_type
                  -- 'post' olarak kaydediliyor ve 'draft' kalıyor, dolayısıyla
                  -- filtrelenmezse bir sonraki derleme öncekini madde olarak
                  -- içine alıyor: 4 Ağustos 2026'da üretilen derlemenin 4.
                  -- maddesi "Günün 6 Haberi" çıktı. carousel_paths dolu olan
                  -- tek içerik türü derlemedir.
                  AND pc.carousel_paths IS NULL
                ORDER BY ni.relevance_score DESC, pc.created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def mark_used_in_roundup(self, content_ids: list[int]) -> int:
        """Derlemeye giren taslakları işaretle (tekil yayınlanmasınlar diye)."""
        if not content_ids:
            return 0
        placeholders = ",".join("?" * len(content_ids))
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE processed_content SET used_in_roundup = 1 "
                f"WHERE id IN ({placeholders})",
                content_ids,
            )
            return cursor.rowcount

    def get_draft_content(self, content_type: str = None, limit: int = 20,
                           order_by_relevance: bool = False,
                           needs_media: bool = False,
                           needs_delivery: bool = False,
                           has_media: bool = False,
                           min_relevance: float | None = None) -> list[dict]:
        """Taslak içerikleri getir.

        `needs_media=True` ise yalnızca medyası HENÜZ ÜRETİLMEMİŞ taslaklar
        döner. Bu eleme SQL'de yapılmalı, çağıran tarafta değil: medya turu
        `limit` kadar taslak çekip sonra medyalıları atlarsa, pencere zaten
        işi bitmiş taslaklarla dolar ve gerçekten iş bekleyenler hiç sıraya
        gelmez. Canlı ölçüm (7 Ağustos 2026): 42'lik pencerenin 27'si zaten
        medyalıydı — medyasız 163 taslağın 148'i pencereye hiç girmiyordu.

        `needs_delivery=True` ise yalnızca Telegram'a HENÜZ GÖNDERİLMEMİŞ
        reels taslakları döner (`manual_video_prompt_message_id IS NULL`).

        Bu ikincisi de AYNI SEBEPLE SQL'de: 8 Ağustos 2026'da elemeyi
        Python'da yaptım ve tam olarak yukarıdaki hatayı tekrarladım.
        `limit=3` ve `relevance_score DESC` sıralamasıyla sorgu her seferinde
        AYNI ilk üçü döndürüyordu; onlar teslim edilince Python'daki eleme
        üçünü de düşürüyor ve dördüncü reels pencereye HİÇ giremiyordu.
        Tur "0 reels" diyordu, hiçbir hata da yoktu.
        """
        with self._get_connection() as conn:
            query = """
                SELECT pc.*, ni.title as news_title, ni.category, ni.source_name,
                       ni.url as news_url, ni.relevance_score, ni.mention_count,
                       ni.image_url, ni.description
                FROM processed_content pc
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE pc.status = 'draft'
                  AND COALESCE(pc.used_in_roundup, 0) = 0
            """
            params = []
            if needs_media:
                query += " AND pc.media_path IS NULL"
            if needs_delivery:
                query += " AND pc.manual_video_prompt_message_id IS NULL"
            # `has_media` ve `min_relevance` de AYNI SEBEPLE SQL'de:
            # `auto_schedule_content` bunları çağıran tarafta eliyordu, yani
            # `limit` kadar taslak çekip sonra medyasızları ve düşük puanlıları
            # atıyordu. Pencere uygun olmayanlarla dolunca gerçekten uygun
            # içerik hiç sıraya gelmiyordu.
            if has_media:
                query += " AND pc.media_path IS NOT NULL"
            if min_relevance is not None:
                query += " AND COALESCE(ni.relevance_score, 0) >= ?"
                params.append(min_relevance)
            if content_type:
                query += " AND pc.content_type = ?"
                params.append(content_type)
            if order_by_relevance:
                query += " ORDER BY ni.relevance_score DESC, pc.created_at DESC LIMIT ?"
            else:
                query += " ORDER BY pc.created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                if d.get("hashtags"):
                    d["hashtags"] = json.loads(d["hashtags"])
                if d.get("reels_script"):
                    d["reels_script"] = json.loads(d["reels_script"])
                if d.get("carousel_paths"):
                    d["carousel_paths"] = json.loads(d["carousel_paths"])
                if d.get("list_items"):
                    d["list_items"] = json.loads(d["list_items"])
                results.append(d)
            return results

    def update_content_carousel(self, content_id: int, carousel_paths: list[str]):
        """
        Bir feed gönderisini çok slaytlı (kaydırmalı/carousel) yapar. İlk
        eleman kapak slaydı olarak media_path'e de yazılır — böylece Telegram
        onay önizlemesi ve tekil-görsel varsayan eski kod yolları değişmeden
        çalışmaya devam eder; publish_scheduled() carousel_paths doluysa
        publish_carousel'ı kullanır (bkz. scheduler.py).
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE processed_content SET carousel_paths = ?, media_path = ? WHERE id = ?",
                (json.dumps(carousel_paths), carousel_paths[0], content_id)
            )

    def get_content_pending_telegram_notification(self, limit: int = 20,
                                                   max_age_days: int | None = None) -> list[dict]:
        """
        Medyası hazır ama henüz Telegram'a bildirim gönderilmemiş taslakları
        getirir.

        ÖNEMLİ: Eskiden çağıran taraf (notify_pending_approvals) bu sorguyu
        her content_type için AYRI AYRI çağırıp önce TÜM post'ları, sonra TÜM
        story'leri gönderiyordu — aynı habere ait post ve story Telegram'da
        birbirinden kopuk, araya başka haberlerin içeriği girmiş şekilde
        geliyordu (gerçek kullanıcı şikayeti: "birini verip araya başka
        birşey verip karıştırmamalı"). Artık TEK bir sorguda, önce habere
        göre (relevance_score DESC, sonra news_id ile grupla), HER haberin
        kendi içinde de post → story → reels sırasıyla döner — böylece aynı
        haberin post'u ve story'si art arda, yan yana gelir.
        """
        # Tazelik penceresi: kotalar dolduğu için bildirilememiş taslaklar
        # kuyrukta birikip günler sonra, haber değeri kalmamışken
        # gönderiliyordu (kullanıcı şikayeti: "eski içeriklerden gönderdi").
        #
        # ÖLÇÜ `pc.created_at` — İÇERİĞİN yaşı. Eskiden `ni.collected_at`
        # kullanılıyordu, yani HABERİN toplanma tarihi. Gönderi ve hikâye
        # için ikisi neredeyse aynı (içerik haberden hemen sonra üretiliyor),
        # ama REELS bir DERLEME: `news_id` alanı yalnızca ilk habere işaret
        # ediyor ve o haber günler önce toplanmış oluyor.
        #
        # Ölçüm (8 Ağustos 2026): bildirim kuyruğunda bekleyen 4 reels'in
        # 2'si bu yüzden elenmişti — içerikler 1 günlük, ama ilk haberleri
        # 4 ve 5 günlük. Yani yeni üretilmiş bir reels "bayat" sayılıyordu.
        # Kullanıcının "hiç bir şey paylaşmadım" demesinin sebeplerinden
        # biri buydu: reels Telegram'a çoğu zaman hiç ulaşmıyordu.
        #
        # Kuralın amacı zaten "kuyrukta bayatlamış İÇERİK gönderme" idi;
        # `created_at` tam olarak onu ölçüyor.
        age_clause = ""
        params: list = []
        if max_age_days is not None:
            age_clause = "AND pc.created_at >= datetime('now', ?)"
            params.append(f"-{max_age_days} days")
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(f"""
                SELECT pc.*, ni.title as news_title, ni.category, ni.source_name,
                       ni.url as news_url, ni.relevance_score, ni.image_url, ni.description
                FROM processed_content pc
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE pc.status = 'draft' AND pc.media_path IS NOT NULL
                      AND pc.telegram_message_id IS NULL
                      AND COALESCE(pc.used_in_roundup, 0) = 0
                      {age_clause}
                ORDER BY ni.relevance_score DESC, pc.news_id ASC,
                         CASE pc.content_type
                             WHEN 'post' THEN 0
                             WHEN 'story' THEN 1
                             WHEN 'reels' THEN 2
                             ELSE 3
                         END
                LIMIT ?
            """, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                if d.get("carousel_paths"):
                    d["carousel_paths"] = json.loads(d["carousel_paths"])
                results.append(d)
            return results

    def set_content_telegram_message(self, content_id: int, message_id: int):
        """Bir içerik için gönderilen Telegram onay mesajının ID'sini kaydet.

        last_notified_at da burada güncellenir — hem ilk gönderimde hem
        hatırlatmada (bkz. get_stale_pending_approvals) tek çağrı noktası
        bu fonksiyon olduğundan, "en son ne zaman bildirildi" hesabı için
        ayrı bir yazma noktasına gerek yok.
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE processed_content SET telegram_message_id = ?, "
                "last_notified_at = datetime('now') WHERE id = ?",
                (message_id, content_id)
            )

    def get_stale_pending_approvals(self, hours: int, limit: int = 10) -> list[dict]:
        """Zaten Telegram'a gönderilmiş ama en az `hours` saattir cevapsız
        kalan onay isteklerini getirir (hatırlatma bildirimi için).

        get_content_pending_telegram_notification'dan farkı: o SADECE hiç
        gönderilmemiş (telegram_message_id IS NULL) içerikleri bulur. Bu ise
        tam tersini — ZATEN gönderilmiş ama kullanıcının Telegram akışında
        kaybolmuş/unutulmuş olabilecekleri arar (gerçek olay: 13 Ağustos
        2026, bkz. TELEGRAM_REMINDER_AFTER_HOURS docstring'i).

        COALESCE(last_notified_at, created_at): migrasyondan önce gönderilmiş
        satırların last_notified_at'i NULL'dır; NULL bir karşılaştırmada hep
        false/NULL döneceğinden bu satırlar created_at'e düşülerek yine de
        hatırlatma adayı olabiliyor, sonsuza dek atlanmıyor.
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT pc.*, ni.title as news_title, ni.category, ni.source_name,
                       ni.url as news_url, ni.relevance_score, ni.image_url, ni.description
                FROM processed_content pc
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE pc.status = 'draft' AND pc.media_path IS NOT NULL
                      AND pc.telegram_message_id IS NOT NULL
                      AND COALESCE(pc.used_in_roundup, 0) = 0
                      AND COALESCE(pc.last_notified_at, pc.created_at) <= datetime('now', ?)
                ORDER BY COALESCE(pc.last_notified_at, pc.created_at) ASC
                LIMIT ?
            """, (f"-{hours} hours", limit)).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                if d.get("carousel_paths"):
                    d["carousel_paths"] = json.loads(d["carousel_paths"])
                results.append(d)
            return results

    def set_manual_image_prompt(self, content_id: int, message_id: int):
        """'🎨 Farklı Görsel İste' ile gönderilen Gemini prompt mesajının ID'sini kaydet
        (kullanıcının foto yanıtını eşleştirmek için)."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE processed_content SET manual_image_prompt_message_id = ? WHERE id = ?",
                (message_id, content_id)
            )

    def get_content_by_manual_prompt_message(self, message_id: int) -> dict | None:
        """Belirli bir Gemini prompt mesajına karşılık gelen içeriği bul
        (kullanıcının bu mesaja yanıt olarak gönderdiği fotoğrafı eşleştirmek için)."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT pc.*, ni.title as news_title, ni.category, ni.source_name, ni.url as news_url,
                       ni.image_url, ni.description
                FROM processed_content pc
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE pc.manual_image_prompt_message_id = ?
            """, (message_id,)).fetchone()
            if row:
                d = dict(row)
                if d.get("hashtags"):
                    d["hashtags"] = json.loads(d["hashtags"])
                if d.get("reels_script"):
                    d["reels_script"] = json.loads(d["reels_script"])
                return d
            return None

    def set_manual_image_path(self, content_id: int, path: str):
        """Kullanıcının Gemini'den üretip gönderdiği görselin yerel yolunu kaydet."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE processed_content SET manual_image_path = ? WHERE id = ?",
                (path, content_id)
            )

    def set_manual_video_prompt(self, content_id: int, message_id: int):
        """Reels videosunun Telegram'a GÖNDERİLDİĞİNİ işaretle.

        Kolonun adı eski anlamından kalma: bir zamanlar kullanıcıya "bu
        senaryoyu Gemini/Veo'da videoya çevir" promptu gönderiliyor ve bu
        alan o mesajın ID'sini tutuyordu. O akış terk edildi (20 reels
        taslağı üretildi, 0 yayınlandı — her seferinde manuel iş istiyordu);
        artık video otomatik üretilip Telegram'a DOSYA olarak gidiyor.

        Bugünkü anlamı tek satırlık: "bu reels kullanıcıya ulaştı".
        `generate_all_media` bunu okuyup aynı videoyu tekrar göndermiyor
        (bkz. scheduler.py). Adı değiştirilmedi çünkü kolon adı değiştirmek
        migrasyon gerektiriyor ve kazanç yalnızca isimsel."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE processed_content SET manual_video_prompt_message_id = ? WHERE id = ?",
                (message_id, content_id)
            )

    def get_content_by_manual_video_prompt_message(self, message_id: int) -> dict | None:
        """Belirli bir reels video-üretim prompt mesajına karşılık gelen içeriği
        bul (kullanıcının bu mesaja yanıt olarak gönderdiği videoyu eşleştirmek için)."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT pc.*, ni.title as news_title, ni.category, ni.source_name, ni.url as news_url,
                       ni.image_url, ni.description
                FROM processed_content pc
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE pc.manual_video_prompt_message_id = ?
            """, (message_id,)).fetchone()
            if row:
                d = dict(row)
                if d.get("hashtags"):
                    d["hashtags"] = json.loads(d["hashtags"])
                if d.get("reels_script"):
                    d["reels_script"] = json.loads(d["reels_script"])
                return d
            return None

    def update_content_status(self, content_id: int, status: str):
        """İçerik durumunu güncelle."""
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE processed_content 
                SET status = ?, updated_at = datetime('now')
                WHERE id = ?
            """, (status, content_id))
            logger.info(f"İçerik durumu güncellendi: ID={content_id} → {status}")

    def update_content_media(self, content_id: int, media_path: str, thumbnail_path: str = None):
        """
        İçerik medya yolunu güncelle. carousel_paths de temizlenir — tekil
        bir görsel elle/manuel olarak set edildiğinde (ör. Telegram'dan
        "🎨 Farklı Görsel İste"), eski carousel slaytları media_path ile
        tutarsız kalıp publish_scheduled()'ın hâlâ eski (artık kapağı
        değişmiş) carousel'i yayınlamasına yol açmasın.
        """
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE processed_content
                SET media_path = ?, thumbnail_path = ?, carousel_paths = NULL, updated_at = datetime('now')
                WHERE id = ?
            """, (media_path, thumbnail_path, content_id))

    def get_content_by_id(self, content_id: int) -> dict | None:
        """ID ile içerik getir."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT pc.*, ni.title as news_title, ni.category, ni.source_name, ni.url as news_url,
                       ni.image_url, ni.description
                FROM processed_content pc
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE pc.id = ?
            """, (content_id,)).fetchone()
            if row:
                d = dict(row)
                if d.get("hashtags"):
                    d["hashtags"] = json.loads(d["hashtags"])
                if d.get("reels_script"):
                    d["reels_script"] = json.loads(d["reels_script"])
                if d.get("carousel_paths"):
                    d["carousel_paths"] = json.loads(d["carousel_paths"])
                if d.get("list_items"):
                    d["list_items"] = json.loads(d["list_items"])
                return d
            return None

    # =============================================
    # PAYLAŞIM GEÇMİŞİ
    # =============================================

    def add_publish_record(self, content_id: int, post_type: str,
                           status: str = "success",
                           instagram_media_id: str = None,
                           instagram_permalink: str = None,
                           error_message: str = None) -> int:
        """
        Paylaşım kaydı ekle.

        ÖNEMLİ: update_content_status çağrısı BİLEREK bu with bloğunun
        DIŞINDA yapılıyor. Gerçek üretim vakası: burada iç içe (nested) ikinci
        bir sqlite3 bağlantısı açılıp, dıştaki bağlantı henüz commit
        edilmeden (INSERT'ten sonra transaction hâlâ açıkken) aynı dosyaya
        yazmaya çalışıyordu. Aynı süreç/iş parçacığı içinde olsa bile SQLite
        bunları ayrı bağlantı olarak görüyor — dıştaki bağlantı commit
        olmadan kilidi bırakmıyor, içteki bağlantı da busy_timeout süresince
        (30 sn) bu kilidi bekleyip sonunda "database is locked" ile
        patlıyordu. Sonuç: gerçekten yayınlanmış bir Instagram gönderisi
        veritabanına hiç "published" olarak yazılamıyor, publish_scheduled()
        bunu yanlışlıkla "failed" işaretliyordu. Çözüm: INSERT'in bağlantısı
        önce commit edilip kapatılıyor, ikinci güncelleme (update_content_status)
        kendi ayrı/sıralı bağlantısıyla ondan SONRA çalıştırılıyor.
        """
        # published_at BİLEREK Python'un yerel saatiyle yazılıyor, sütun
        # varsayılanı (datetime('now')) kullanılmıyor: SQLite'ın datetime('now')
        # değeri HER ZAMAN UTC'dir, oysa zamanlama ve günlük sayım mantığının
        # tamamı Python'un yerel saatini kullanıyor (bkz. get_today_publish_count,
        # get_pending_scheduled). Sunucu UTC'de çalışırken bu ikisi tesadüfen
        # aynıydı; servisler Europe/Istanbul'a alınınca 3 saat ayrışıp gece
        # yayınlanan gönderilerin günlük limite yanlış günde sayılmasına yol
        # açardı.
        published_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO publish_history
                (content_id, post_type, instagram_media_id, instagram_permalink,
                 status, error_message, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (content_id, post_type, instagram_media_id,
                  instagram_permalink, status, error_message, published_at))
            record_id = cursor.lastrowid
        if status == "success":
            self.update_content_status(content_id, "published")
        return record_id

    def mark_manually_published(self, content_id: int, post_type: str) -> None:
        """Meta API'nin engellediği bir POST'u kullanıcı Instagram'dan ELLE
        paylaştığında çağrılır (bkz. telegram_bot.send_manual_publish_fallback).

        Neden var: 15 Ağustos 2026'dan beri Meta feed post yayınını API
        üzerinden code 4 / subcode 2207051 ile reddediyor (proje hafızası:
        instagram_feed_publish_blocked). Otomatik yeniden deneme yok —
        içerik kullanıcı elle paylaşana kadar 'approved' durumunda sonsuza
        dek asılı kalırdı, dashboard/publish_history bunu hiç görmezdi.

        scheduled_posts güncellemesi BİLEREK add_publish_record'dan ÖNCE ve
        kendi bağlantısıyla yapılıyor (nested-connection kilit dersi için
        bkz. add_publish_record docstring'i).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM scheduled_posts WHERE content_id = ? "
                "ORDER BY id DESC LIMIT 1", (content_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE scheduled_posts SET status = 'published' WHERE id = ?",
                    (row["id"],)
                )
        self.add_publish_record(
            content_id, post_type, status="success",
            error_message="Elle paylaşıldı (otomatik yayın Meta tarafından engellendi)"
        )

    def get_today_publish_count(self) -> dict:
        """Bugün yapılan paylaşım sayıları."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT post_type, COUNT(*) as count
                FROM publish_history
                WHERE DATE(published_at) = ? AND status = 'success'
                GROUP BY post_type
            """, (today,)).fetchall()
            return {row["post_type"]: row["count"] for row in rows}

    def get_publish_history(self, limit: int = 50) -> list[dict]:
        """Paylaşım geçmişini getir."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT ph.*, pc.caption, pc.content_type, pc.media_path,
                       ni.title as news_title, ni.category
                FROM publish_history ph
                JOIN processed_content pc ON ph.content_id = pc.id
                JOIN news_items ni ON pc.news_id = ni.id
                ORDER BY ph.published_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def get_daily_publish_counts(self, days: int = 7) -> list[dict]:
        """
        Son N günün her biri için başarılı paylaşım sayısını döndürür (Dashboard'daki
        yayın trendi grafiği için). Veri olmayan günler için de 0 ile dolu bir satır
        döner — grafik boşluksuz, her zaman `days` uzunluğunda bir dizi bekleyebilsin.
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT DATE(published_at) as day, COUNT(*) as c
                FROM publish_history
                WHERE status = 'success' AND published_at >= ?
                GROUP BY DATE(published_at)
            """, (_local_today(offset_days=-(days - 1)),)).fetchall()
            counts_by_day = {row["day"]: row["c"] for row in rows}

        result = []
        for offset in range(days - 1, -1, -1):
            day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            result.append({"day": day, "count": counts_by_day.get(day, 0)})
        return result

    # =============================================
    # ZAMANLAMA İŞLEMLERİ
    # =============================================

    def schedule_post(self, content_id: int, scheduled_time: str, post_type: str,
                       source: str = "manual") -> int:
        """Gönderi zamanla."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO scheduled_posts (content_id, scheduled_time, post_type, source)
                VALUES (?, ?, ?, ?)
            """, (content_id, scheduled_time, post_type, source))
            return cursor.lastrowid

    def get_scheduled_count_today(self, post_type: str) -> int:
        """Bugün için zaten zamanlanmış (pending veya published) gönderi sayısı."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.get_scheduled_count_for_date(post_type, today)

    def get_scheduled_times_for_date(self, post_type: str, date_str: str) -> set:
        """
        Verilen tarih (YYYY-MM-DD) için zaten zamanlanmış (pending veya
        published) TAM saat-dakika değerlerinin kümesi — compute_next_
        schedule_time'ın "bu spesifik slot dolu mu" diye kontrol edebilmesi
        için. Sadece SAYI yeterli değil: bir günün ilk slotu (ör. sabah 10:00)
        saati geçtiği için atlanıp o gün için ikinci slota (gece 21:00)
        yazılmışsa, sonraki çağrının "1 tane var, o zaman 2. slotu kullan"
        diye aynı (zaten dolu) slotu tekrar seçmesi gerekiyordu — gerçek olay.
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT scheduled_time FROM scheduled_posts
                WHERE post_type = ? AND DATE(scheduled_time) = ?
                      AND status IN ('pending', 'published')
            """, (post_type, date_str)).fetchall()
            return {row["scheduled_time"] for row in rows}

    def get_scheduled_count_for_date(self, post_type: str, date_str: str) -> int:
        """Verilen tarih (YYYY-MM-DD) için zaten zamanlanmış (pending veya
        published) gönderi sayısı."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as c FROM scheduled_posts
                WHERE post_type = ? AND DATE(scheduled_time) = ?
                      AND status IN ('pending', 'published')
            """, (post_type, date_str)).fetchone()
            return row["c"]

    def get_sibling_post_status(self, news_id: int) -> str | None:
        """
        Aynı habere ait 'post' türündeki içeriğin durumunu döndürür (yoksa
        None). Hikaye CTA'sının "detaylar profilde" gibi bir ifade
        kullanabilmesi için, aynı haberin feed gönderisinin GERÇEKTEN
        yayınlanmış olması gerekir — aksi halde takipçi profilde olmayan bir
        şeye yönlendirilmiş, yanlış bilgilendirilmiş olur. Bu kontrol
        publish_scheduled() tarafından, hikaye yayınlanmadan hemen önce
        (en güncel gerçek durumu yakalamak için) çağrılır.
        """
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT status FROM processed_content
                WHERE news_id = ? AND content_type = 'post'
                ORDER BY id DESC LIMIT 1
            """, (news_id,)).fetchone()
            return row["status"] if row else None

    def get_pending_scheduled(self) -> list[dict]:
        """Bekleyen zamanlanmış gönderileri getir."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT sp.*, pc.caption, pc.media_path, pc.content_type,
                       ni.title as news_title, ni.category
                FROM scheduled_posts sp
                JOIN processed_content pc ON sp.content_id = pc.id
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE sp.status = 'pending' AND sp.scheduled_time <= ?
                ORDER BY sp.scheduled_time ASC
            """, (now,)).fetchall()
            return [dict(row) for row in rows]

    def update_schedule_status(self, schedule_id: int, status: str):
        """Zamanlama durumunu güncelle."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE scheduled_posts SET status = ? WHERE id = ?",
                (status, schedule_id)
            )

    # =============================================
    # İSTATİSTİKLER
    # =============================================

    def get_stats(self) -> dict:
        """Genel istatistikleri getir."""
        with self._get_connection() as conn:
            stats = {}
            # Toplam haberler
            row = conn.execute("SELECT COUNT(*) as c FROM news_items").fetchone()
            stats["total_news"] = row["c"]

            # Bugünkü haberler.
            #
            # `collected_at` UTC (SQLite datetime('now')), o yüzden gün de
            # SQLite'ın kendi DATE('now')'ı ile alınıyor — bkz. dosya
            # başındaki ZAMAN KURALI. Python'ın YEREL tarihiyle
            # karşılaştırmak UTC+3'te gecenin ilk 3 saatinde YANLIŞ GÜNÜ
            # saydırır.
            #
            # Ölçüm (8 Ağustos 2026): şu anda fark 0 — haber toplama sabah
            # sabit bir saatte çalıştığı için hiçbir kayıt gün sınırına
            # denk gelmiyor (1631 haberin 0'ı UTC 21:00-24:00 arasında).
            # Yani hata UYKUDA: toplama saati değişirse ya da gece elle bir
            # tur çalıştırılırsa panel sessizce yanlış günü gösterir.
            row = conn.execute(
                "SELECT COUNT(*) as c FROM news_items "
                "WHERE DATE(collected_at) = DATE('now')"
            ).fetchone()
            stats["today_news"] = row["c"]

            # İşlenmemiş haberler
            row = conn.execute(
                "SELECT COUNT(*) as c FROM news_items WHERE is_processed = 0"
            ).fetchone()
            stats["unprocessed_news"] = row["c"]

            # Taslak içerikler
            row = conn.execute(
                "SELECT COUNT(*) as c FROM processed_content WHERE status = 'draft'"
            ).fetchone()
            stats["draft_content"] = row["c"]

            # Bugün paylaşılanlar.
            #
            # Burada YEREL tarih DOĞRU: `published_at` bilerek yerel
            # yazılıyor (gerekçesi `add_publish_record` içinde). Yani bu iki
            # sayaç bilerek farklı gün kaynakları kullanıyor — üstteki UTC,
            # bu yerel. Bkz. ZAMAN KURALI.
            row = conn.execute(
                "SELECT COUNT(*) as c FROM publish_history "
                "WHERE DATE(published_at) = ? AND status = 'success'",
                (_local_today(),)
            ).fetchone()
            stats["today_published"] = row["c"]

            # Toplam paylaşım
            row = conn.execute(
                "SELECT COUNT(*) as c FROM publish_history WHERE status = 'success'"
            ).fetchone()
            stats["total_published"] = row["c"]

            # Kategori kırılımı (haber havuzu) — dashboard'da AI/Gaming
            # dengesini görebilmek için
            stats["news_by_category"] = {
                row["category"]: row["c"] for row in conn.execute(
                    "SELECT category, COUNT(*) as c FROM news_items GROUP BY category"
                )
            }

            # İçerik tipi kırılımı (taslaklar) — post/story/reels dengesi
            stats["draft_by_type"] = {
                row["content_type"]: row["c"] for row in conn.execute(
                    "SELECT content_type, COUNT(*) as c FROM processed_content "
                    "WHERE status = 'draft' GROUP BY content_type"
                )
            }

            # NOT: burada "video bekleyen reels" diye bir sayaç vardı ve
            # panelde KPI olarak gösteriliyordu. Koşulu
            # (`media_path IS NULL AND manual_video_prompt_message_id IS NOT NULL`)
            # mevcut akışta HİÇ sağlanamıyor: generate_all_media önce
            # update_content_media ile media_path'i yazıyor, o alanı ancak
            # ondan sonra set ediyor. Yani panelde kalıcı bir "0" duruyordu —
            # var olmayan bir durumu ima eden bir sayı. Sayaç, ait olduğu
            # terk edilmiş akışla (kullanıcının Gemini web'de video üretip
            # geri göndermesi) birlikte kaldırıldı.

            return stats

    # =============================================
    # UYGULAMA AYARLARI (key-value)
    # =============================================

    def get_setting(self, key: str, default: str = None) -> str | None:
        """Kalıcı bir ayarı oku (ör. yenilenmiş Instagram token'ı)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        """Kalıcı bir ayarı yaz/güncelle."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
            """, (key, value))

    # =============================================
    # MEDYA PERFORMANSI (INSIGHTS)
    # =============================================

    def add_media_insight(self, publish_history_id: int, impressions: int = None,
                          reach: int = None, likes: int = None, comments: int = None,
                          saves: int = None, shares: int = None,
                          engagement_rate: float = None) -> int:
        """Bir yayının performans anlık görüntüsünü kaydet (append-only)."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO media_insights
                (publish_history_id, impressions, reach, likes, comments, saves, shares, engagement_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (publish_history_id, impressions, reach, likes, comments, saves, shares, engagement_rate))
            return cursor.lastrowid

    def get_latest_insights(self, limit: int = 50) -> list[dict]:
        """Her yayın için en güncel performans kaydını getir."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT mi.*, ph.post_type, ph.published_at, pc.content_type,
                       ni.title as news_title, ni.category
                FROM media_insights mi
                JOIN publish_history ph ON mi.publish_history_id = ph.id
                JOIN processed_content pc ON ph.content_id = pc.id
                JOIN news_items ni ON pc.news_id = ni.id
                WHERE mi.id IN (
                    SELECT MAX(id) FROM media_insights GROUP BY publish_history_id
                )
                ORDER BY mi.fetched_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def get_publish_history_for_insights(self, days: int = 14) -> list[dict]:
        """Son N gün içinde başarıyla yayınlanmış, insights senkronize edilecek kayıtlar.

        Hikayeler 24 saatten eskiyse HARİÇ tutulur: Instagram hikayeleri 24
        saat sonra kayboluyor ve medya nesnesi API'de çözülmez hale geliyor
        (`(#100) Tried accessing nonexisting field (insights)`). Bunları
        sorgulamak her turda garantili başarısız çağrı ve gürültülü log
        demekti — 2026-08-04 denetiminde günlük 58 başarısız isteğin bir kısmı
        buydu.
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT id, instagram_media_id, post_type
                FROM publish_history
                WHERE status = 'success' AND instagram_media_id IS NOT NULL
                      AND published_at >= ?
                      AND (post_type != 'story' OR published_at >= ?)
            """, (_local_now(offset_days=-days),
                  _local_now(offset_hours=-24))).fetchall()
            return [dict(row) for row in rows]

    # =============================================
    # HARİCİ ARAŞTIRMA (ör. Gemini Spark / MCP)
    # =============================================

    def add_external_research(self, source: str, title: str, summary: str = None,
                               content: str = None, topic: str = None,
                               source_url: str = None) -> int:
        """MCP üzerinden gelen bir araştırma bulgusunu kaydet."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO external_research
                (source, title, summary, content, topic, source_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (source, title, summary, content, topic, source_url))
            research_id = cursor.lastrowid
            logger.info(f"Araştırma bulgusu kaydedildi: [{source}] {title[:60]}...")
            return research_id

    def get_external_research(self, limit: int = 50) -> list[dict]:
        """En son araştırma bulgularını getir."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM external_research
                ORDER BY received_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def promote_research_to_news(self, research_id: int, category: str) -> int | None:
        """
        Bir araştırma bulgusunu news_items'a taşı (Instagram içerik pipeline'ına
        girebilmesi için). Zaten taşınmışsa None döner (idempotent).

        Otonom haber toplama (RSS/NewsAPI/Currents, bkz. news_collector.py'deki
        _is_duplicate) ile Spark'ın bulduğu araştırma AYNI gerçek olayı
        kapsıyor olabilir — bu yüzden burada da aynı başlık-benzerliği
        tekilleştirme kontrolü uygulanır. Bir eşleşme bulunursa yeni bir
        news_items satırı oluşturulmaz; bulgu, zaten var olan habere
        bağlanır (mention_count artırılır) ve o haberin ID'si döner.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM external_research WHERE id = ?", (research_id,)
            ).fetchone()
            if not row or row["promoted_news_id"] is not None:
                return None

            research = dict(row)

        duplicate_id, embedding = self.find_similar_recent(
            research["title"], category, DEDUP_WINDOW_HOURS, DEDUP_TITLE_SIMILARITY_THRESHOLD
        )
        if duplicate_id is not None:
            self.increment_mention_count(duplicate_id)
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE external_research SET promoted_news_id = ? WHERE id = ?",
                    (duplicate_id, research_id)
                )
            logger.info(
                f"Araştırma bulgusu zaten toplanmış bir haberle eşleşti "
                f"[research_id={research_id}] → news_id={duplicate_id}, yeni kayıt oluşturulmadı."
            )
            return duplicate_id

        # Kaynak adı, bulguyu BULAN araçtan değil haberin GERÇEK yayınından
        # türetilir. Eskiden "Claude Research" / "Gemini Spark" yazılıyordu ve
        # bu iki açıdan yanlıştı:
        #   1. Görselin üzerine "📰 Kaynak: Claude Research" basılıyordu —
        #      Claude bir yayın organı değil, haberi bulan araç. Okuyucuya
        #      yanlış bir kaynak gösteriyordu.
        #   2. Gerçek yayının hakkı yeniyordu. Bulguların source_url'i zaten
        #      dolu: screenrant.com, pushsquare.com, massivelyop.com gibi
        #      gerçek oyun yayınları.
        news_id = self.add_news(
            title=research["title"],
            url=research.get("source_url") or f"internal://external_research/{research_id}",
            category=category,
            description=research.get("summary") or research.get("content"),
            source_name=publication_from_url(research.get("source_url")),
            embedding=embedding,
        )
        if news_id is None:
            return None

        with self._get_connection() as conn:
            conn.execute(
                "UPDATE external_research SET promoted_news_id = ? WHERE id = ?",
                (news_id, research_id)
            )
        return news_id

    def cleanup_old_data(self, days: int = 30) -> int:
        """Eski, kullanılmış haber satırlarını sil — AMA yayın geçmişini asla.

        `PRAGMA foreign_keys` bu projede AÇIK (bkz. `_get_connection`), yani
        buradaki DELETE cascade ile yayılıyor:

            news_items → processed_content → publish_history → media_insights

        `is_used = 1` bayrağını koyan TEK yer `mark_news_used` ve o da
        yalnızca `create_reels_content`'ten çağrılıyor; `get_published_for_reels`
        ise tanım gereği yalnızca GERÇEKTEN YAYINLANMIŞ haberleri döndürüyor.
        Yani `is_used = 1` satırları büyük ölçüde yayın geçmişi taşıyan
        satırlar. 7 Ağustos 2026 ölçümü: 76 `publish_history` kaydının
        **56'sı (%74)** bu koşula giriyordu ve 30 günü doldurduklarında tek
        bir temizlikle, 184 `media_insights` kaydıyla birlikte silineceklerdi.

        Bu, ölçümün kendisini yok etmek olurdu — "önce ölç, sonra düzelt"
        yaklaşımının dayandığı tek veri kaynağı bu tablolar. Retention'ın işi
        çöpü toplamak, neyin yayınlandığının kaydını silmek değil.

        Bu yüzden yayın geçmişi olan haberler korunuyor. Silinen satır
        sayısını döndürür.
        """
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM news_items
                WHERE is_used = 1
                  AND DATE(collected_at) < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM processed_content pc
                      JOIN publish_history ph ON ph.content_id = pc.id
                      WHERE pc.news_id = news_items.id
                  )
            """, (cutoff,))
            silinen = cursor.rowcount

        logger.info(
            f"{days} günden eski {silinen} haber satırı temizlendi "
            f"(yayın geçmişi olanlar korundu)."
        )
        return silinen

    def expire_stale_unprocessed_news(self, days: int = 2) -> int:
        """Belirtilen günden eski, hiç işlenmemiş haberleri kuyruktan düşür.

        Neden gerekli: toplama işlemeden çok daha hızlı. `process_all_news`
        GÜNLÜK bir bütçeyle çalışıyor (~15 haber, bkz. content_processor)
        ama günde ~150 haber toplanıyor. Aradaki fark kuyrukta birikiyor —
        2026-08-03 ölçümünde 290 haberlik, günde ~45 büyüyen kalıcı bir
        yığın oluşmuştu.

        NOT: bu docstring eskiden "tur başına ~15" ve "`collected_at DESC`
        ile en yeniden başlıyor" diyordu; ikisi de artık yanlış. Bütçe
        günlük hale geldi ve seçim `priority DESC` (puan − tazelik sönümü)
        ile yapılıyor (bkz. `get_unprocessed_news`). Eski metin README'ye
        de kopyalanmıştı; ikisi birbirini "doğruluyor" göründüğü için
        yanlışlık uzun süre fark edilmedi.

        Bu haberler silinmiyor, `is_processed=1` + `expired_at` ile
        işaretleniyor: silmek tekilleştirme geçmişini bozardı
        (`mention_count` / dedup penceresi aynı tabloya bakıyor).

        `expired_at`'in ayrı bir kolon olması bilinçli — bunlar
        `relevance_score=0` ile duruyor ve iz bırakılmazsa gerçekten düşük
        puan almış haberlerle karışırlar.

        Emekliye ayrılan haber sayısını döndürür.
        """
        # `collected_at` SQLite'ın datetime('now')'ı ile yazılıyor: UTC.
        # Eşik de UTC'den hesaplanmalı. Python'ın datetime.now()'ı YEREL
        # (TZ=Europe/Istanbul) — eşiği 3 saat ileri kaydırıyor ve haberler
        # 3 saat ERKEN emekliye ayrılıyordu. Bkz. dosya başındaki ZAMAN KURALI.
        with self._get_connection() as conn:
            cursor = conn.execute(
                """UPDATE news_items
                   SET is_processed = 1, expired_at = datetime('now')
                   WHERE is_processed = 0 AND expired_at IS NULL
                     AND collected_at < datetime('now', ?)""",
                (f"-{days} days",),
            )
            count = cursor.rowcount

        if count:
            logger.info(
                f"⌛ {count} işlenmemiş haber {days} günden eski olduğu için "
                f"kuyruktan düşürüldü."
            )
        return count

    def expire_stale_drafts(self, days: int = 3) -> int:
        """Onay bekleyen ama bayatlamış taslakları kuyruktan düşür.

        Haberin kendisi emekliye ayrılıyordu ama TASLAK hiç ayrılmıyordu.
        Sonuç (7 Ağustos 2026 ölçümü): 466 onay bekleyen taslak, en eskisi
        1 Ağustos'tan kalma. Kuyruk günde ~22 büyüyordu çünkü üretim
        (günde ~30 içerik) hem Telegram bildirim hızının (8) hem de yayın
        kapasitesinin (8) çok üstündeydi.

        Altı günlük bir oyun haberini yayınlamanın zaten değeri yok; bu
        yüzden bayat taslak "reddedildi" sayılıyor. Ayrı bir `expired_at`
        damgası konuyor: bunlar SENİN reddettiklerinle karışmasın, çünkü
        ikisi çok farklı sinyaller — biri içerik kalitesi hakkında, diğeri
        yalnızca kapasite hakkında.

        ONAYLANMIŞ içeriğe DOKUNULMAZ, medyası olmasa bile. Kullanıcı
        onayladıysa karar kullanıcınındır; bunu sistemin geri alması yanlış
        olur (bu kural bir testle kilitli).

        Takılı kalmış onaylı bir reels'in haber havuzunu kilitlemesi ayrı
        bir sorundu ve orada, `_pending_reels_news_ids` içinde çözüldü —
        durumunu değiştirerek değil, teslim edilemeyeceğini görerek.

        Düşürülen taslak sayısını döndürür.
        """
        # `created_at` UTC (SQLite datetime('now')). Eşik de UTC olmalı —
        # Python'ın YEREL datetime.now()'ı ile hesaplanınca eşik 3 saat ileri
        # kayıyor ve taslaklar 3 saat ERKEN düşüyordu. Bkz. ZAMAN KURALI.
        with self._get_connection() as conn:
            cursor = conn.execute(
                """UPDATE processed_content
                   SET status = 'rejected', expired_at = datetime('now'),
                       updated_at = datetime('now')
                   WHERE status = 'draft' AND expired_at IS NULL
                     AND created_at < datetime('now', ?)""",
                (f"-{days} days",),
            )
            count = cursor.rowcount

        if count:
            logger.info(
                f"⌛ {count} taslak {days} günden eski olduğu için kuyruktan "
                f"düşürüldü (bayat haber yayınlanmaz)."
            )
        return count

    def get_draft_backlog_count(self) -> int:
        """Onay bekleyen taslak sayısı — üretim frenini besleyen ölçü."""
        with self._get_connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM processed_content WHERE status = 'draft'"
            ).fetchone()[0]

    def vacuum(self):
        """Silinen satırların kapladığı yeri işletim sistemine geri ver.

        SQLite, DELETE sonrası dosyayı küçültmez — boşalan sayfaları yeniden
        kullanmak üzere dosyanın içinde tutar. `cleanup_old_data` aylardır
        satır silmesine rağmen `news.db` hiç küçülmüyordu.

        Kendi bağlantısını açar: VACUUM bir transaction içinde çalışamaz,
        `_get_connection` ise çıkışta commit eden bir bağlam yöneticisi.
        `isolation_level=None` ile Python'ın örtük transaction yönetimi
        devre dışı bırakılıyor.
        """
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("VACUUM")
            logger.info("🗜️ Veritabanı sıkıştırıldı (VACUUM).")
        finally:
            conn.close()
