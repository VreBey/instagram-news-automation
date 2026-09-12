"""
Instagram AI & Gaming News Otomasyon Sistemi
Ana Giriş Noktası

Kullanım:
    python main.py --collect        Haberleri topla
    python main.py --process        İçerikleri işle
    python main.py --generate       Medya oluştur
    python main.py --publish        Zamanlanmış gönderileri paylaş
    python main.py --pipeline       Tam pipeline (topla + işle + üret)
    python main.py --run            Otomatik zamanlayıcıyı başlat
    python main.py --dashboard      Web dashboard başlat
    python main.py --stats          İstatistikleri göster
    python main.py --test           Test görseli oluştur
    python main.py --mcp-server     MCP sunucusunu başlat (Gemini Spark köprüsü)
    python main.py --telegram-bot   Telegram onay botunu başlat
    python main.py --maintenance    Günlük bakımı elle çalıştır
    python main.py --roundup        Günlük derleme üret
    python main.py --alert SERVIS   Servis arızasını Telegram'a bildir
                                    (systemd OnFailure= tarafından çağrılır)
"""

import argparse
import logging
import logging.handlers
import sys
import os
from pathlib import Path
from datetime import datetime

# Windows konsolunda UTF-8 desteği
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Proje kök dizinini path'e ekle
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    LOG_FILE, LOG_LEVEL, LOG_FORMAT, LOG_DATE_FORMAT, LOGS_DIR,
    LOG_MAX_BYTES, LOG_BACKUP_COUNT
)


def setup_logging():
    """Loglama yapılandırması."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL))

    # Konsol handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # Dosya handler (rotating — tek dosyanın sonsuza kadar büyümesini önler)
    file_handler = logging.handlers.RotatingFileHandler(
        str(LOG_FILE), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    file_handler.setFormatter(file_format)
    root_logger.addHandler(file_handler)

    # Sır maskeleme — handler'lar kurulduktan SONRA eklenmeli. Bu satır olmadan
    # `requests` istisnalarının içindeki URL'ler (Telegram token'ı, API
    # anahtarları) düz metin olarak app.log'a yazılıyordu; 2026-08-03
    # denetiminde canlı token loglarda bulundu. Bkz. src/log_redaction.py.
    from src.log_redaction import install as install_log_redaction
    install_log_redaction(root_logger)


def print_banner():
    """Baslik banner'i yazdir."""
    banner = """
============================================================
     Instagram AI & Gaming News Otomasyon
    
     Gunluk AI ve Oyun haberlerini otomatik paylasir
     Feed Gonderi - Hikaye - Reels
============================================================
    """
    print(banner)


def cmd_collect():
    """Haber toplama komutu."""
    from src.scheduler import Scheduler
    scheduler = Scheduler()
    stats = scheduler.collect_news()
    print(f"\n📊 Sonuç: {stats['total']} yeni haber toplandı")


def cmd_process():
    """İçerik işleme komutu."""
    from src.scheduler import Scheduler
    scheduler = Scheduler()
    stats = scheduler.process_content()
    print(f"\n📊 Sonuç: {stats['processed']} haber işlendi → "
          f"{stats['posts']} gönderi, {stats['stories']} hikaye")


def cmd_generate():
    """Medya üretimi komutu."""
    from src.scheduler import Scheduler
    scheduler = Scheduler()
    stats = scheduler.generate_all_media()
    print(f"\n📊 Sonuç: {stats['total']} medya üretildi "
          f"({stats['posts']} post, {stats['stories']} story, {stats['reels']} reels)")


def cmd_publish():
    """Paylaşım komutu."""
    from src.scheduler import Scheduler
    scheduler = Scheduler()
    scheduler.publish_scheduled()


def cmd_pipeline():
    """Tam pipeline komutu."""
    from src.scheduler import Scheduler
    scheduler = Scheduler()
    result = scheduler.run_full_pipeline()
    
    if "error" in result:
        print(f"\n❌ Pipeline hatası: {result['error']}")
    else:
        print(f"\n✅ Pipeline tamamlandı ({result['elapsed_seconds']:.1f}s)")


def cmd_run():
    """Otomatik zamanlayıcı komutu."""
    from src.scheduler import Scheduler
    scheduler = Scheduler()
    scheduler.run_scheduler()


def cmd_mcp_server():
    """MCP sunucusu komutu — Gemini Spark / harici araştırma köprüsü."""
    from src.mcp_server import run_server
    from config import MCP_SERVER_HOST, MCP_SERVER_PORT, MCP_PATH

    print(f"\n🔌 MCP sunucusu başlatılıyor: http://{MCP_SERVER_HOST}:{MCP_SERVER_PORT}{MCP_PATH}")
    print("💡 Bu sunucu sadece yerelde dinler. Gemini'nin bağlanabilmesi için")
    print("   Cloudflare Tunnel (veya benzeri) ile dışa açmanız gerekir (bkz. README.md).\n")
    run_server()


def cmd_telegram_bot():
    """Telegram onay botu komutu."""
    from src.telegram_bot import run_bot_polling
    run_bot_polling()


def cmd_maintenance():
    """Günlük bakımı elle çalıştır (normalde zamanlayıcı her gece 02:00'de yapar).

    Deploy'dan hemen sonra ilk yedeği almak için kullanışlı: aksi halde
    sağlık kontrolü ertesi 02:00'ye kadar "veritabanının hiç yedeği yok"
    uyarısı üretir.
    """
    from src.database import Database
    from src.maintenance import run_daily_maintenance

    summary = run_daily_maintenance(Database())

    print("\n🧰 Bakım özeti")
    print(f"   Yedek        : {summary.get('backup') or 'ALINAMADI'}")
    print(f"   Silinen yedek: {summary.get('backup_pruned', 0)}")
    print(f"   Emekli haber : {summary.get('expired_news', 0)} (bayat, işlenmemiş)")
    print(f"   Emekli taslak: {summary.get('expired_drafts', 0)} (onay kuyruğunda bayatladı)")
    print(f"   Silinen satır: {summary.get('rows_cleaned', 0)} (yayın geçmişi olanlar korunur)")
    print(f"   VACUUM       : {'evet' if summary.get('vacuumed') else 'hayır'}")
    print(f"   Silinen medya: {summary.get('media_removed', 0)} dosya")
    print(f"   Silinen önbellek: {summary.get('cache_removed', 0)} dosya")
    print(f"   Kazanılan alan  : {summary.get('freed_mb', 0):.0f} MB")
    if summary.get("disk_warning"):
        print(f"   ⚠️  {summary['disk_warning']}")


def cmd_alert(service_name: str):
    """Bir systemd servisi `failed` duruma düştüğünde Telegram'a haber ver.

    systemd birimlerindeki `OnFailure=instagram-alert@%n.service` tarafından
    çağrılır. Neden gerekli: birimlerde `Restart=always` + `RestartSec=10`
    vardı ama `StartLimitBurst` ayarı yoktu; systemd'nin varsayılanı 10
    saniyede 5 başlatma olduğu için `RestartSec=10` bu pencereye asla
    sığmıyor — yani hız sınırı hiç tetiklenmiyor ve servis HİÇBİR ZAMAN
    `failed` durumuna geçmiyordu. Açılışta patlayan bir servis sonsuza
    kadar 10 saniyede bir yeniden başlıyor, `systemctl is-active` çoğu
    zaman `activating` ya da `active` gösteriyor ve bunu fark etmenin tek
    yolu elle `journalctl` bakmak oluyordu.

    Bu komut, sistemin başka hiçbir yerinin göremediği "süreç hiç ayağa
    kalkamıyor" durumunu görünür kılar — sağlık kontrolü de o servisin
    içinde çalıştığı için o durumda zaten susmuş oluyor.
    """
    from config import TELEGRAM_CHAT_ID
    from src import telegram_bot

    mesaj = (f"🔴 Servis arızası: {service_name}\n\n"
             f"Servis `failed` durumuna düştü ve kendiliğinden toparlamadı.\n"
             f"Sunucuda inceleyin:\n"
             f"  journalctl -u {service_name} -n 50 --no-pager")
    if not TELEGRAM_CHAT_ID:
        print(mesaj)
        return
    try:
        telegram_bot._send_text(TELEGRAM_CHAT_ID, mesaj)
        print(f"Arıza bildirildi: {service_name}")
    except Exception as e:
        # Bildirim gönderilemese bile çıkış kodu 0: bu birim yalnızca
        # haber vermek için var, kendisi arıza üretmemeli.
        print(f"Arıza bildirimi gönderilemedi ({service_name}): {e}")


def cmd_roundup():
    """Günlük derlemeyi elle üret (normalde zamanlayıcı akşam yapar)."""
    from src.scheduler import Scheduler

    stats = Scheduler().create_daily_roundup()
    if stats["created"]:
        print(f"\n🗞️ Derleme üretildi: {stats['item_count']} haber.")
        print("   Telegram'a onay için bildirilecek; otomatik yayınlanmaz.")
    else:
        print("\nℹ️ Derleme üretilmedi (yeterli taslak yok ya da kapalı).")


def cmd_dashboard():
    """Dashboard komutu."""
    from src.dashboard import create_app
    from config import DASHBOARD_PORT, DASHBOARD_HOST, DASHBOARD_DEBUG, BRAND_NAME
    
    app = create_app()
    print(f"\n🌐 Dashboard başlatılıyor: http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")

    if DASHBOARD_DEBUG:
        # Hata ayıklama modu yalnızca yerel geliştirme için — otomatik
        # yeniden yükleme ve ayrıntılı hata sayfası Flask'ın kendi
        # sunucusunu gerektirir.
        app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=True)
        return

    # Üretimde Flask'ın geliştirme sunucusu KULLANILMAZ. Kendi logu bunu
    # açıkça uyarıyordu ("This is a development server. Do not use it in a
    # production deployment."): tek iş parçacıklı, yük altında güvenilir
    # değil ve üretim için desteklenmiyor. Waitress saf Python, bağımlılığı
    # az ve hem Linux hem Windows'ta çalışıyor (gunicorn Windows'ta çalışmaz
    # — geliştirme bu projede Windows'ta yapılıyor).
    try:
        from waitress import serve
    except ImportError:
        print("⚠️ waitress kurulu değil, Flask geliştirme sunucusuna düşülüyor. "
              "Üretim için: pip install waitress")
        app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False)
        return

    serve(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, threads=4,
          ident=BRAND_NAME)


def cmd_stats():
    """İstatistik komutu."""
    from src.database import Database
    db = Database()
    stats = db.get_stats()
    
    print("\n📊 Genel İstatistikler")
    print("=" * 40)
    print(f"  📰 Toplam haber     : {stats['total_news']}")
    print(f"  📅 Bugünkü haber    : {stats['today_news']}")
    print(f"  ⏳ İşlenmemiş       : {stats['unprocessed_news']}")
    print(f"  📝 Taslak içerik    : {stats['draft_content']}")
    print(f"  ✅ Bugün paylaşılan : {stats['today_published']}")
    print(f"  📤 Toplam paylaşım  : {stats['total_published']}")
    print("=" * 40)

    # Bugünkü paylaşım limitleri
    today_counts = db.get_today_publish_count()
    from config import DAILY_POST_LIMIT, DAILY_STORY_LIMIT, DAILY_REELS_LIMIT
    print(f"\n📤 Bugünkü Paylaşım Limitleri:")
    print(f"  Feed  : {today_counts.get('post', 0)}/{DAILY_POST_LIMIT}")
    print(f"  Story : {today_counts.get('story', 0)}/{DAILY_STORY_LIMIT}")
    print(f"  Reels : {today_counts.get('reels', 0)}/{DAILY_REELS_LIMIT}")


def cmd_test():
    """Test komutu — örnek görsel ve video oluştur."""
    from src.image_generator import ImageGenerator
    from src.story_generator import StoryGenerator
    from src.video_generator import VideoGenerator
    from src.database import Database

    db = Database()

    print("\n🧪 Test modu — Örnek içerikler oluşturuluyor...\n")

    # Test verileri
    test_ai = {
        "category": "ai",
        "summary_text": "ÖRNEK BAŞLIK: Yapay Zeka Şablonunu Denemek İçin Üretilmiş Test Metnidir",
        "news_title": "Örnek Yapay Zeka Başlığı (test)",
        "source_name": "Örnek Kaynak"
    }
    test_gaming = {
        "category": "gaming",
        "summary_text": "ÖRNEK BAŞLIK: Oyun Şablonunu Denemek İçin Üretilmiş Test Metnidir",
        "news_title": "Örnek Oyun Başlığı (test)",
        "source_name": "Örnek Kaynak"
    }

    # Feed görseli
    img_gen = ImageGenerator(db=db)
    print("📸 Feed görseli oluşturuluyor...")
    path = img_gen.generate_post_image(news_data=test_ai)
    if path:
        print(f"   ✅ {path}")

    path = img_gen.generate_post_image(news_data=test_gaming)
    if path:
        print(f"   ✅ {path}")

    # Hikaye görseli
    story_gen = StoryGenerator(db=db)
    print("\n📱 Hikaye görseli oluşturuluyor...")
    path = story_gen.generate_story_image(news_data=test_ai)
    if path:
        print(f"   ✅ {path}")

    # Hikaye serisi
    print("\n📱 Hikaye serisi oluşturuluyor...")
    paths = story_gen.generate_daily_summary_stories([test_ai, test_gaming])
    for p in paths:
        print(f"   ✅ {p}")

    # Reels videosu
    video_gen = VideoGenerator(db=db)
    print("\n🎬 Reels videosu oluşturuluyor...")
    test_script = {
        "intro": "Örnek video — test verisi",
        "segments": [
            "Bu bir örnek segmenttir. Satır kaydırmasını ve segment süresini denemek için kullanılır.",
            "İkinci örnek segment. Gerçek bir haber içermez, yalnızca görsel düzeni doğrular.",
        ],
        "outro": "Bu bir örnek çıktıdır, yayınlamayın",
        "news_titles": [
            "Örnek Başlık Bir",
            "Örnek Başlık İki"
        ],
        "category": "ai"
    }
    path = video_gen.generate_reels(reels_data=test_script)
    if path:
        print(f"   ✅ {path}")
    else:
        print("   ⚠️ Video oluşturulamadı (moviepy yüklü mü?)")

    print("\n✅ Test tamamlandı! output/ dizinini kontrol edin.")


def main():
    """Ana giriş noktası."""
    setup_logging()
    print_banner()

    parser = argparse.ArgumentParser(
        description="Instagram AI & Gaming News Otomasyon Sistemi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Örnekler:
  python main.py --collect     Haberleri topla
  python main.py --pipeline    Tam pipeline çalıştır
  python main.py --test        Test görselleri oluştur
  python main.py --dashboard   Web dashboard başlat
  python main.py --run         Otomatik zamanlayıcı
        """
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--collect", action="store_true", help="Haberleri topla")
    group.add_argument("--process", action="store_true", help="İçerikleri işle")
    group.add_argument("--generate", action="store_true", help="Medya oluştur")
    group.add_argument("--publish", action="store_true", help="Zamanlanmış gönderileri paylaş")
    group.add_argument("--pipeline", action="store_true", help="Tam pipeline çalıştır")
    group.add_argument("--run", action="store_true", help="Otomatik zamanlayıcı başlat")
    group.add_argument("--dashboard", action="store_true", help="Web dashboard başlat")
    group.add_argument("--stats", action="store_true", help="İstatistikleri göster")
    group.add_argument("--test", action="store_true", help="Test görseli oluştur")
    group.add_argument("--mcp-server", action="store_true",
                       help="MCP sunucusunu başlat (Gemini Spark köprüsü)")
    group.add_argument("--telegram-bot", action="store_true",
                       help="Telegram onay botunu başlat")
    group.add_argument("--maintenance", action="store_true",
                       help="Günlük bakımı elle çalıştır (yedek + temizlik + VACUUM + disk)")
    group.add_argument("--roundup", action="store_true",
                       help="Günlük derleme (kaydırmalı özet gönderi) üret")
    group.add_argument("--alert", metavar="SERVIS",
                       help="Servis arızasını Telegram'a bildir "
                            "(systemd OnFailure= tarafından çağrılır)")

    args = parser.parse_args()

    commands = {
        "collect": cmd_collect,
        "process": cmd_process,
        "generate": cmd_generate,
        "publish": cmd_publish,
        "pipeline": cmd_pipeline,
        "run": cmd_run,
        "dashboard": cmd_dashboard,
        "stats": cmd_stats,
        "test": cmd_test,
        "mcp_server": cmd_mcp_server,
        "telegram_bot": cmd_telegram_bot,
        "maintenance": cmd_maintenance,
        "roundup": cmd_roundup,
    }

    # --alert deger alan tek komut; sozluk tabanli dagitim bayrak bekliyor.
    if args.alert:
        cmd_alert(args.alert)
        return

    for cmd_name, cmd_func in commands.items():
        if getattr(args, cmd_name, False):
            cmd_func()
            break


if __name__ == "__main__":
    main()
