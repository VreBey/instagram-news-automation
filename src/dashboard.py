"""
Web Dashboard Modülü
Flask tabanlı içerik yönetim ve önizleme paneli.
"""

import json
import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    TEMPLATES_DIR, POSTS_OUTPUT_DIR, STORIES_OUTPUT_DIR,
    REELS_OUTPUT_DIR, DAILY_POST_LIMIT, DAILY_STORY_LIMIT,
    DAILY_REELS_LIMIT, AUTO_PUBLISH_THRESHOLD,
    DASHBOARD_USERNAME, DASHBOARD_PASSWORD, DASHBOARD_SECRET_KEY,
    DASHBOARD_MAX_LOGIN_ATTEMPTS, DASHBOARD_LOGIN_LOCKOUT_SECONDS,
    RESEARCH_API_TOKEN, APPROVAL_MODE, INSTAGRAM_USERNAME, BRAND_NAME
)
from src.database import Database
from src.token_manager import TokenManager
from src.image_generator import ImageGenerator
from src.story_generator import StoryGenerator
from src.image_prompts import build_image_prompt
from src.manual_image import apply_manual_image
from src.schedule_utils import immediate_schedule_time
from src.instagram_client import InstagramClient

logger = logging.getLogger(__name__)

# Basit bellek-içi brute-force koruması: {ip: [deneme_zamanı, ...]}. Süreç
# yeniden başlayınca sıfırlanır — tek kullanıcılı bu araç için yeterli,
# ayrı bir depolama katmanı gerektirmez.
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    attempts = _login_attempts[client_ip]
    attempts[:] = [t for t in attempts if now - t < DASHBOARD_LOGIN_LOCKOUT_SECONDS]
    return len(attempts) >= DASHBOARD_MAX_LOGIN_ATTEMPTS


def _record_failed_login(client_ip: str):
    _login_attempts[client_ip].append(time.time())


def create_app(db: Database = None) -> Flask:
    """Flask uygulamasını oluştur ve yapılandır."""

    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=None
    )
    # DASHBOARD_SECRET_KEY .env'de kalıcı olarak ayarlanmışsa oturumlar
    # yeniden başlatmalarda düşmez; ayarlanmamışsa (yerel kullanım) süreç
    # başına rastgele üretilir.
    app.secret_key = DASHBOARD_SECRET_KEY or os.urandom(24)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    @app.context_processor
    def _marka():
        """
        Instagram önizleme kartındaki hesap adı ve avatar baş harfleri.

        Context processor olarak veriliyor çünkü index.html birden fazla
        route'tan render ediliyor (ana sayfa, /research, ...) — tek tek
        parametre geçilseydi yeni bir route eklendiğinde sessizce unutulurdu.

        Marka adı koda gömülü DEĞİL: depoyu kuran herkes kendi hesabını
        görmeli. Boşsa yer tutucu gösterilir, önizleme yine çalışır.
        """
        kullanici = INSTAGRAM_USERNAME or "hesabiniz"
        parcalar = [p for p in kullanici.replace("_", " ").replace(".", " ").split() if p]
        bas_harf = "".join(p[0] for p in parcalar[:2]).upper() or kullanici[:2].upper()
        return {
            "ig_username": kullanici,
            "ig_initials": bas_harf,
            "brand_name": BRAND_NAME,
        }

    db = db or Database()
    token_manager = TokenManager(db=db)
    img_gen = ImageGenerator(db=db)
    story_gen = StoryGenerator(db=db)
    ig_client = InstagramClient(db=db)

    @app.template_filter("basename")
    def basename_filter(path):
        """Mutlak medya yolundan sadece dosya adını döndürür (/media/<filename> route'u için)."""
        return Path(path).name if path else None

    # =============================================
    # GİRİŞ (opsiyonel şifre koruması)
    # =============================================

    @app.before_request
    def require_login():
        # DASHBOARD_PASSWORD boşsa (yerel kullanım) giriş zorunlu değildir —
        # mevcut "kimlik bilgisi yoksa nazikçe atla" deseniyle tutarlı.
        if not DASHBOARD_PASSWORD:
            return None
        # api_research_submit kasıtlı olarak oturum girişinden muaf —
        # tarayıcıdan değil, harici bir ajandan (Claude Code Routine)
        # çağrılıyor ve kendi ayrı RESEARCH_API_TOKEN'ıyla korunuyor.
        if request.endpoint in ("login", "static", "api_research_submit"):
            return None
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            client_ip = request.remote_addr or "unknown"
            if _is_rate_limited(client_ip):
                minutes = DASHBOARD_LOGIN_LOCKOUT_SECONDS // 60
                error = f"Çok fazla başarısız deneme. Lütfen {minutes} dakika sonra tekrar deneyin."
            else:
                username = request.form.get("username", "")
                password = request.form.get("password", "")
                if (secrets.compare_digest(username, DASHBOARD_USERNAME)
                        and secrets.compare_digest(password, DASHBOARD_PASSWORD)):
                    session["logged_in"] = True
                    _login_attempts.pop(client_ip, None)
                    return redirect(url_for("index"))
                _record_failed_login(client_ip)
                error = "Kullanıcı adı veya şifre hatalı."
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.pop("logged_in", None)
        return redirect(url_for("login"))

    # =============================================
    # ANA SAYFALAR
    # =============================================

    @app.route("/")
    def index():
        """Ana sayfa — dashboard."""
        stats = db.get_stats()
        today_counts = db.get_today_publish_count()
        recent_history = db.get_publish_history(limit=10)
        daily_publish_trend = db.get_daily_publish_counts(days=7)

        # Backlog #2 (codebase-analysis denetimi): get_rate_limit_status()
        # daha önce hiçbir yere bağlı değildi. Hata durumunda (API'ye
        # ulaşılamazsa, yapılandırılmamışsa) None döner — şablon bu durumda
        # kartı tamamen gizler, "0/0" gibi yanıltıcı bir değer göstermez.
        try:
            rate_limit_status = ig_client.get_rate_limit_status()
        except Exception:
            logger.exception("Rate limit durumu alınamadı")
            rate_limit_status = None

        return render_template(
            "index.html",
            stats=stats,
            today_counts=today_counts,
            limits={
                "post": DAILY_POST_LIMIT,
                "story": DAILY_STORY_LIMIT,
                "reels": DAILY_REELS_LIMIT
            },
            recent_history=recent_history,
            daily_publish_trend=daily_publish_trend,
            token_status=token_manager.get_expiry_status(),
            rate_limit_status=rate_limit_status,
            now=datetime.now()
        )

    @app.route("/research")
    def research_list():
        """Harici araştırma bulguları (ör. Gemini Spark)."""
        findings = db.get_external_research(limit=100)

        return render_template(
            "index.html",
            page="research", findings=findings,
            stats=db.get_stats(),
            today_counts=db.get_today_publish_count(),
            limits={"post": DAILY_POST_LIMIT, "story": DAILY_STORY_LIMIT, "reels": DAILY_REELS_LIMIT},
            now=datetime.now()
        )

    @app.route("/api/research/<int:research_id>/promote", methods=["POST"])
    def api_promote_research(research_id):
        """Bir araştırma bulgusunu news_items'a aktar (Instagram pipeline'ına girsin)."""
        data = request.get_json(silent=True) or {}
        category = data.get("category", "ai")
        if category not in ("ai", "gaming"):
            return jsonify({"success": False, "error": "category 'ai' veya 'gaming' olmalı"})

        news_id = db.promote_research_to_news(research_id, category)
        if news_id is None:
            return jsonify({"success": False, "error": "Zaten aktarılmış veya bulgu bulunamadı"})
        return jsonify({"success": True, "news_id": news_id})

    @app.route("/api/research/submit", methods=["POST"])
    def api_research_submit():
        """
        Harici bir araştırma ajanının (ör. zamanlanmış bir Claude Code Routine)
        yeni bir oyun/AI haberi bulgusu ekleyip doğrudan pipeline'a aktarması
        için sınırlı yetkili uç nokta. claude.ai "Routines" özel MCP
        connector'ları desteklemediği için (bkz. gerçek deneme — mevcut
        Gemini Spark MCP köprüsü rutin sisteminde hiç görünmedi) bu REST
        endpoint'i kullanılıyor. Dashboard'un oturum girişinden kasıtlı
        olarak muaf (bkz. require_login) — ayrı bir Bearer token ile korunur,
        bulgu ekleme dışında hiçbir yetkisi yoktur.
        """
        if not RESEARCH_API_TOKEN:
            return jsonify({"error": "RESEARCH_API_TOKEN yapılandırılmamış"}), 503

        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        if not secrets.compare_digest(token, RESEARCH_API_TOKEN):
            return jsonify({"error": "unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        summary = (data.get("summary") or "").strip()
        if not title or not summary:
            return jsonify({"error": "title ve summary zorunlu"}), 400

        category = data.get("category", "gaming")
        if category not in ("gaming", "ai"):
            category = "gaming"

        research_id = db.add_external_research(
            source="claude_research", title=title, summary=summary,
            topic=data.get("topic") or category,
            source_url=data.get("source_url") or None,
        )
        news_id = db.promote_research_to_news(research_id, category=category)
        logger.info(f"📥 Harici araştırma bulgusu eklendi: [{category}] {title[:60]} → news_id={news_id}")
        return jsonify({"success": True, "research_id": research_id, "news_id": news_id})

    @app.route("/insights")
    def insights():
        """Yayın performansı (impressions/reach/likes vb.)."""
        latest_insights = db.get_latest_insights(limit=50)

        return render_template(
            "index.html",
            page="insights", latest_insights=latest_insights,
            stats=db.get_stats(),
            today_counts=db.get_today_publish_count(),
            limits={"post": DAILY_POST_LIMIT, "story": DAILY_STORY_LIMIT, "reels": DAILY_REELS_LIMIT},
            now=datetime.now()
        )

    @app.route("/news")
    def news_list():
        """Haber listesi."""
        category = request.args.get("category")
        status = request.args.get("status", "all")
        
        if status == "unprocessed":
            items = db.get_unprocessed_news(category=category, limit=50)
        elif status == "unused":
            items = db.get_unused_news(category=category, limit=50)
        else:
            with db._get_connection() as conn:
                query = "SELECT * FROM news_items"
                params = []
                if category:
                    query += " WHERE category = ?"
                    params.append(category)
                query += " ORDER BY collected_at DESC LIMIT 100"
                rows = conn.execute(query, params).fetchall()
                items = [dict(r) for r in rows]
        
        return render_template("index.html", 
                             page="news", items=items,
                             category=category, status=status,
                             stats=db.get_stats(),
                             today_counts=db.get_today_publish_count(),
                             limits={"post": DAILY_POST_LIMIT, "story": DAILY_STORY_LIMIT, "reels": DAILY_REELS_LIMIT},
                             now=datetime.now())

    @app.route("/content")
    def content_list():
        """İçerik listesi (taslaklar, yayınlananlar)."""
        content_type = request.args.get("type")
        
        drafts = db.get_draft_content(content_type=content_type, limit=50, order_by_relevance=True)

        return render_template("index.html",
                             page="content", drafts=drafts,
                             content_type=content_type,
                             auto_publish_threshold=AUTO_PUBLISH_THRESHOLD,
                             approval_mode=APPROVAL_MODE,
                             stats=db.get_stats(),
                             today_counts=db.get_today_publish_count(),
                             limits={"post": DAILY_POST_LIMIT, "story": DAILY_STORY_LIMIT, "reels": DAILY_REELS_LIMIT},
                             now=datetime.now())

    @app.route("/history")
    def publish_history():
        """Paylaşım geçmişi."""
        history = db.get_publish_history(limit=100)
        
        return render_template("index.html",
                             page="history", history=history,
                             stats=db.get_stats(),
                             today_counts=db.get_today_publish_count(),
                             limits={"post": DAILY_POST_LIMIT, "story": DAILY_STORY_LIMIT, "reels": DAILY_REELS_LIMIT},
                             now=datetime.now())

    # =============================================
    # API ENDPOINTS
    # =============================================

    @app.route("/api/stats")
    def api_stats():
        """İstatistik API."""
        return jsonify(db.get_stats())

    @app.route("/api/content/<int:content_id>/approve", methods=["POST"])
    def api_approve_content(content_id):
        """
        İçeriği onayla VE gerçekten zamanla — sadece durum değiştirmek
        yeterli değil, aksi halde publish_scheduled() bu içeriği hiçbir
        zaman bulup paylaşamaz (Telegram onayındaki AYNI mantık).
        """
        content = db.get_content_by_id(content_id)
        if not content:
            return jsonify({"success": False, "error": "İçerik bulunamadı"}), 404

        scheduled_time = immediate_schedule_time()
        db.schedule_post(content_id, scheduled_time, content["content_type"], source="dashboard")
        db.update_content_status(content_id, "approved")
        return jsonify({
            "success": True,
            "message": f"İçerik onaylandı — birkaç dakika içinde paylaşılacak"
        })

    @app.route("/api/content/<int:content_id>/reject", methods=["POST"])
    def api_reject_content(content_id):
        """İçeriği reddet."""
        db.update_content_status(content_id, "rejected")
        return jsonify({"success": True, "message": "İçerik reddedildi"})

    @app.route("/api/content/<int:content_id>/schedule", methods=["POST"])
    def api_schedule_content(content_id):
        """İçeriği zamanla."""
        data = request.get_json()
        scheduled_time = data.get("scheduled_time")
        post_type = data.get("post_type", "post")

        if not scheduled_time:
            return jsonify({"success": False, "error": "Zaman belirtilmedi"})

        db.schedule_post(content_id, scheduled_time, post_type)
        db.update_content_status(content_id, "approved")
        return jsonify({"success": True, "message": "İçerik zamanlandı"})

    @app.route("/api/content/<int:content_id>/image_prompt")
    def api_image_prompt(content_id):
        """Bu içerik için Gemini'de kullanılacak görsel prompt'unu üret."""
        content = db.get_content_by_id(content_id)
        if not content:
            return jsonify({"success": False, "error": "İçerik bulunamadı"}), 404
        # Reels tek bir video dosyası (birden fazla segment içerir) —
        # Telegram'daki aynı kısıtlama, per-segment karşılığı yok.
        if content["content_type"] == "reels":
            return jsonify({"success": False, "error": "Reels için bu özellik desteklenmiyor"}), 400
        return jsonify({"success": True, "prompt": build_image_prompt(content)})

    @app.route("/api/content/<int:content_id>/upload_image", methods=["POST"])
    def api_upload_image(content_id):
        """
        Kullanıcının Gemini'den üretip yüklediği görseli içeriğe uygula.

        Reels için desteklenmiyor — arayüzdeki buton reels için zaten
        gizleniyordu ama bu uç nokta kendi başına aynı kısıtlamayı
        uygulamıyordu (bkz. hemen üstteki api_image_prompt). Doğrudan bir
        isteğe (ör. eski bir sekme/istek) izin verilseydi, reels'in video
        media_path'i yanlışlıkla tekil bir POST görseliyle değiştirilirdi.
        """
        content = db.get_content_by_id(content_id)
        if not content:
            return jsonify({"success": False, "error": "İçerik bulunamadı"}), 404
        if content["content_type"] == "reels":
            return jsonify({"success": False, "error": "Reels için bu özellik desteklenmiyor"}), 400

        uploaded = request.files.get("image")
        if not uploaded or not uploaded.filename:
            return jsonify({"success": False, "error": "Görsel dosyası gönderilmedi"}), 400

        image_bytes = uploaded.read()
        try:
            from PIL import Image
            import io
            probe = Image.open(io.BytesIO(image_bytes))
            probe.load()
        except Exception:
            return jsonify({"success": False, "error": "Geçersiz görsel dosyası"}), 400

        new_path = apply_manual_image(content_id, image_bytes, db, img_gen, story_gen)
        if not new_path:
            return jsonify({"success": False, "error": "İçerik yeniden oluşturulamadı"}), 500

        return jsonify({"success": True, "media_path": new_path})

    @app.route("/api/run/collect", methods=["POST"])
    def api_run_collect():
        """Haber toplama tetikle."""
        try:
            from src.scheduler import Scheduler
            scheduler = Scheduler()
            stats = scheduler.collect_news()
            return jsonify({"success": True, "stats": stats})
        except Exception as e:
            logger.exception("api/run/collect başarısız")
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/run/process", methods=["POST"])
    def api_run_process():
        """İçerik işleme tetikle."""
        try:
            from src.scheduler import Scheduler
            scheduler = Scheduler()
            stats = scheduler.process_content()
            return jsonify({"success": True, "stats": stats})
        except Exception as e:
            logger.exception("api/run/process başarısız")
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/run/generate", methods=["POST"])
    def api_run_generate():
        """Medya üretimi tetikle."""
        try:
            from src.scheduler import Scheduler
            scheduler = Scheduler()
            stats = scheduler.generate_all_media()
            return jsonify({"success": True, "stats": stats})
        except Exception as e:
            logger.exception("api/run/generate başarısız")
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/run/auto_schedule", methods=["POST"])
    def api_run_auto_schedule():
        """Eşiği geçen içerikleri otomatik zamanla."""
        try:
            from src.scheduler import Scheduler
            scheduler = Scheduler()
            stats = scheduler.auto_schedule_content()
            return jsonify({"success": True, "stats": stats})
        except Exception as e:
            logger.exception("api/run/auto_schedule başarısız")
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/run/sync_insights", methods=["POST"])
    def api_run_sync_insights():
        """Yayın performans metriklerini senkronize et."""
        try:
            from src.scheduler import Scheduler
            scheduler = Scheduler()
            stats = scheduler.sync_insights()
            return jsonify({"success": True, "stats": stats})
        except Exception as e:
            logger.exception("api/run/sync_insights başarısız")
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/run/pipeline", methods=["POST"])
    def api_run_pipeline():
        """Tam pipeline tetikle."""
        try:
            from src.scheduler import Scheduler
            scheduler = Scheduler()
            result = scheduler.run_full_pipeline()
            return jsonify({"success": True, "result": result})
        except Exception as e:
            logger.exception("api/run/pipeline başarısız")
            return jsonify({"success": False, "error": str(e)})

    # =============================================
    # MEDYA SERVİSİ
    # =============================================

    @app.route("/media/<path:filename>")
    def serve_media(filename):
        """Medya dosyalarını sun."""
        # Güvenlik: filename içindeki "../" (URL-encode edilmiş haliyle bile,
        # ör. "..%2f") ile output dizininin dışına çıkılabiliyordu — path
        # traversal. Artık sonuç yolu gerçekten (resolve edilmiş) hedef
        # dizinin İÇİNDE mi diye açıkça doğrulanıyor.
        for output_dir in [POSTS_OUTPUT_DIR, STORIES_OUTPUT_DIR, REELS_OUTPUT_DIR]:
            try:
                output_dir_resolved = output_dir.resolve()
                filepath = (output_dir / filename).resolve()
            except (OSError, ValueError):
                continue
            if not filepath.is_relative_to(output_dir_resolved):
                continue
            if filepath.exists() and filepath.is_file():
                return send_file(str(filepath))

        return "Dosya bulunamadı", 404

    return app
