"""
Dashboard (Flask) birim testleri. Gerçek sunucu başlatılmaz — Flask'ın
test_client()'ı kullanılır. Gerçek görsel render/ağ çağrısı tetiklenmemesi
için ImageGenerator/StoryGenerator çağrıları mock'lanır.
"""

import io
import json
import re
from collections import defaultdict

import src.dashboard as dashboard_module
from src.dashboard import create_app


def _make_client(tmp_db, monkeypatch, password=""):
    monkeypatch.setattr(dashboard_module, "DASHBOARD_USERNAME", "admin")
    monkeypatch.setattr(dashboard_module, "DASHBOARD_PASSWORD", password)
    # Her test kendi temiz rate-limit durumuyla başlasın — modül seviyesindeki
    # _login_attempts dict'i testler arasında sızmasın diye.
    monkeypatch.setattr(dashboard_module, "_login_attempts", defaultdict(list))
    # ÖNEMLİ: index() rotası artık InstagramClient.get_rate_limit_status()'u
    # çağırıyor. Gerçek bir .env yapılandırması varsa (bu geliştiricinin
    # makinesinde olduğu gibi) bu, testler sırasında GERÇEK bir Instagram API
    # isteği yapardı (bkz. gerçek olay — test süresi aniden ~3sn'den ~9sn'ye
    # çıkmıştı). Varsayılan olarak None döndürerek tamamen izole ediliyor;
    # rate-limit'e özel testler bunu kendi mocker'larıyla ezebilir.
    monkeypatch.setattr(
        "src.instagram_client.InstagramClient.get_rate_limit_status",
        lambda self: None,
    )
    app = create_app(db=tmp_db)
    app.config["TESTING"] = True
    return app.test_client()


def _make_draft_content(db, content_type="post"):
    news_id = db.add_news(title="Test Haberi", url=f"https://example.com/{content_type}", category="ai")
    db.mark_news_processed(news_id, relevance_score=0.9)
    return db.add_content(news_id=news_id, content_type=content_type, caption="test")


def test_no_password_configured_allows_access_without_login(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="")
    response = client.get("/")
    assert response.status_code == 200


def test_password_configured_redirects_to_login(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="secret123")
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_with_correct_credentials_grants_access(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="secret123")
    response = client.post(
        "/login", data={"username": "admin", "password": "secret123"}, follow_redirects=True
    )
    assert response.status_code == 200

    # Girişten sonra ana sayfa artık yönlendirmeden erişilebilir olmalı
    response = client.get("/")
    assert response.status_code == 200


def test_login_with_wrong_password_shows_error(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="secret123")
    response = client.post("/login", data={"username": "admin", "password": "yanlis"})
    assert response.status_code == 200
    assert "hatalı".encode("utf-8") in response.data or b"hatal" in response.data

    # Giriş başarısız olduğundan ana sayfaya erişim hâlâ engellenmeli
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302


def test_login_rate_limited_after_max_attempts(tmp_db, monkeypatch):
    monkeypatch.setattr(dashboard_module, "DASHBOARD_MAX_LOGIN_ATTEMPTS", 3)
    client = _make_client(tmp_db, monkeypatch, password="secret123")

    for _ in range(3):
        client.post("/login", data={"username": "admin", "password": "yanlis"})

    # 3 başarısız denemeden sonra DOĞRU şifreyle bile giremiyor olmalı
    response = client.post("/login", data={"username": "admin", "password": "secret123"})
    assert b"fazla" in response.data or b"deneme" in response.data

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302  # hâlâ giriş yapılamadı


def test_image_prompt_returns_prompt_for_post(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="")
    content_id = _make_draft_content(tmp_db, content_type="post")

    response = client.get(f"/api/content/{content_id}/image_prompt")
    data = response.get_json()

    assert response.status_code == 200
    assert data["success"] is True
    assert "Test Haberi" in data["prompt"]


def test_image_prompt_rejects_reels(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="")
    content_id = _make_draft_content(tmp_db, content_type="reels")

    response = client.get(f"/api/content/{content_id}/image_prompt")

    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_image_prompt_unknown_content_returns_404(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="")
    response = client.get("/api/content/99999/image_prompt")
    assert response.status_code == 404


def _fake_image_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), (10, 20, 30)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def test_upload_image_applies_manual_image(tmp_db, monkeypatch, mocker):
    client = _make_client(tmp_db, monkeypatch, password="")
    content_id = _make_draft_content(tmp_db, content_type="post")

    apply_mock = mocker.patch(
        "src.dashboard.apply_manual_image", return_value="/fake/post.png"
    )

    response = client.post(
        f"/api/content/{content_id}/upload_image",
        data={"image": (_fake_image_bytes(), "test.jpg")},
        content_type="multipart/form-data",
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["success"] is True
    assert data["media_path"] == "/fake/post.png"
    apply_mock.assert_called_once()
    assert apply_mock.call_args[0][0] == content_id


def test_upload_image_rejects_invalid_file(tmp_db, monkeypatch, mocker):
    client = _make_client(tmp_db, monkeypatch, password="")
    content_id = _make_draft_content(tmp_db, content_type="post")
    apply_mock = mocker.patch("src.dashboard.apply_manual_image")

    response = client.post(
        f"/api/content/{content_id}/upload_image",
        data={"image": (io.BytesIO(b"not-an-image"), "test.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    apply_mock.assert_not_called()


def test_upload_image_missing_file_returns_400(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="")
    content_id = _make_draft_content(tmp_db, content_type="post")

    response = client.post(
        f"/api/content/{content_id}/upload_image",
        data={},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400


def test_upload_image_rejects_reels(tmp_db, monkeypatch, mocker):
    """
    Kod denetimi bulgusu: /api/content/<id>/image_prompt reels'i reddediyordu
    ama /api/content/<id>/upload_image aynı kısıtlamayı uygulamıyordu.
    Arayüzdeki buton reels için gizli olsa da, uç noktanın kendisi bunu
    zorunlu kılmalı — aksi halde reels'in video media_path'i yanlışlıkla
    tekil bir POST görseliyle değiştirilebilirdi.
    """
    client = _make_client(tmp_db, monkeypatch, password="")
    content_id = _make_draft_content(tmp_db, content_type="reels")
    apply_mock = mocker.patch("src.dashboard.apply_manual_image")

    response = client.post(
        f"/api/content/{content_id}/upload_image",
        data={"image": (_fake_image_bytes(), "test.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["success"] is False
    apply_mock.assert_not_called()


def test_research_submit_requires_correct_bearer_token(tmp_db, monkeypatch):
    monkeypatch.setattr(dashboard_module, "RESEARCH_API_TOKEN", "secret-token-123")
    client = _make_client(tmp_db, monkeypatch, password="")

    response = client.post(
        "/api/research/submit",
        json={"title": "Yeni bir oyun", "summary": "detaylı özet"},
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401


def test_research_submit_creates_and_promotes_finding(tmp_db, monkeypatch):
    """
    Gerçek olay: claude.ai "Routines" özel MCP connector'ları desteklemedi
    (Gemini Spark köprüsü rutin sisteminde hiç görünmedi) — bu yüzden harici
    araştırma ajanları için ayrı, token korumalı bir REST uç noktası eklendi.
    """
    monkeypatch.setattr(dashboard_module, "RESEARCH_API_TOKEN", "secret-token-123")
    client = _make_client(tmp_db, monkeypatch, password="")

    response = client.post(
        "/api/research/submit",
        json={
            "title": "Corsair Cove Turns City Building Into a Pirate Adventure",
            "summary": "Detaylı, somut bilgiler içeren özet metni burada.",
            "source_url": "https://store.steampowered.com/app/1368140/Corsair_Cove/",
            "category": "gaming",
        },
        headers={"Authorization": "Bearer secret-token-123"},
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["success"] is True
    assert data["news_id"] is not None

    unprocessed = tmp_db.get_unprocessed_news(category="gaming")
    assert any(n["id"] == data["news_id"] for n in unprocessed)


def test_research_submit_rejects_missing_fields(tmp_db, monkeypatch):
    monkeypatch.setattr(dashboard_module, "RESEARCH_API_TOKEN", "secret-token-123")
    client = _make_client(tmp_db, monkeypatch, password="")

    response = client.post(
        "/api/research/submit",
        json={"title": "Sadece başlık var"},
        headers={"Authorization": "Bearer secret-token-123"},
    )

    assert response.status_code == 400


def test_research_submit_bypasses_dashboard_session_login(tmp_db, monkeypatch):
    """
    api_research_submit, oturum tabanlı Dashboard girişinden (DASHBOARD_
    PASSWORD ayarlı olsa bile) kasıtlı olarak muaf olmalı — harici bir
    ajandan çağrılıyor, tarayıcıdan değil.
    """
    monkeypatch.setattr(dashboard_module, "RESEARCH_API_TOKEN", "secret-token-123")
    client = _make_client(tmp_db, monkeypatch, password="dashboard-password")

    response = client.post(
        "/api/research/submit",
        json={"title": "Başlık", "summary": "Özet"},
        headers={"Authorization": "Bearer secret-token-123"},
    )

    assert response.status_code == 200


def test_research_submit_returns_503_when_token_not_configured(tmp_db, monkeypatch):
    monkeypatch.setattr(dashboard_module, "RESEARCH_API_TOKEN", "")
    client = _make_client(tmp_db, monkeypatch, password="")

    response = client.post(
        "/api/research/submit",
        json={"title": "Başlık", "summary": "Özet"},
        headers={"Authorization": "Bearer anything"},
    )

    assert response.status_code == 503


def test_index_renders_mobile_nav_and_categorized_stats(tmp_db, monkeypatch):
    client = _make_client(tmp_db, monkeypatch, password="")
    _make_draft_content(tmp_db, content_type="post")

    response = client.get("/")
    html = response.data.decode("utf-8")

    # Mobil hamburger menü — sidebar önceden telefonda tamamen gizleniyordu
    assert "toggleSidebar" in html
    assert 'id="sidebar"' in html
    # Kategorize istatistik bölümleri
    assert "Haber Havuzu" in html
    assert "İçerik Durumu" in html


def test_content_page_renders_full_caption_and_hashtag_preview(tmp_db, monkeypatch):
    """
    Kullanıcı geri bildirimi: Dashboard'da içeriğin Instagram'a gidecek
    gerçek caption metnini ve etiketlerini görebileceği bir yer yoktu.
    Önizleme verisi (caption, hashtags, carousel_paths) her satırın altına
    gömülü <script type="application/json"> etiketiyle sayfaya geçmeli ve
    şablon bu yeni alanlarla hatasız render olmalı.
    """
    client = _make_client(tmp_db, monkeypatch, password="")
    news_id = tmp_db.add_news(title="Test Haberi Carousel", url="https://example.com/carousel-preview", category="gaming")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(
        news_id=news_id, content_type="post",
        caption="Bu tam caption metni burada görünmeli.",
        hashtags=["oyun", "gaming"],
    )
    tmp_db.update_content_carousel(content_id, ["/fake/slide1.png", "/fake/slide2.png"])

    response = client.get("/content")
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert f'id="content-data-{content_id}"' in html
    assert "openContentPreview(" in html
    assert f'id="btn-preview-{content_id}"' in html

    # Caption/hashtag/carousel verisi tam ve doğru olarak JSON'a gömülmüş mü —
    # tojson filtresi Türkçe karakterleri \uXXXX olarak kaçışladığından
    # (Flask varsayılanı) ham metin araması yerine JSON'u ayrıştırıp kontrol
    # ediyoruz.
    match = re.search(
        rf'<script type="application/json" id="content-data-{content_id}">(.*?)</script>',
        html, re.DOTALL
    )
    assert match is not None
    data = json.loads(match.group(1))
    assert data["caption"] == "Bu tam caption metni burada görünmeli."
    assert data["hashtags"] == ["oyun", "gaming"]
    assert data["carousel_paths"] == ["/fake/slide1.png", "/fake/slide2.png"]


def test_research_page_renders_source_badges_and_summary(tmp_db, monkeypatch):
    """
    Gerçek olay: sayfa her zaman "Spark Araştırmaları" diye sabit
    etiketliydi ve kaynak sütunu ham "claude_research"/"gemini_spark" string'ini
    gösteriyordu, özet metni hiç görünmüyordu. Artık kaynak dostça bir
    rozetle gösterilmeli ve özetin en azından bir kısmı görünmeli.
    """
    client = _make_client(tmp_db, monkeypatch, password="")
    tmp_db.add_external_research(
        source="claude_research", title="Claude'un bulduğu haber",
        summary="Bu araştırmanın tam özet metni burada yer alıyor.",
        topic="gaming",
    )

    response = client.get("/research")
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "Araştırma Bulguları" in html
    assert "Spark Araştırmaları" not in html
    assert "Claude Research" in html
    assert "Bu araştırmanın tam özet metni burada yer alıyor." in html


def test_content_page_shows_manual_approval_notice_when_not_auto_mode(tmp_db, monkeypatch):
    """
    Gerçek olay: sayfa APPROVAL_MODE ne olursa olsun her zaman "skoru X üzeri
    içerikler otomatik zamanlanır" diyordu — sistem "telegram" (manuel onay)
    modundayken bile. Bu, sistemin gerçekte nasıl çalıştığını yanlış
    anlatan, eski sistemden kalma bir metindi.
    """
    monkeypatch.setattr(dashboard_module, "APPROVAL_MODE", "telegram")
    client = _make_client(tmp_db, monkeypatch, password="")
    _make_draft_content(tmp_db, content_type="post")

    response = client.get("/content")
    html = response.data.decode("utf-8")

    assert "manuel onay modunda" in html
    assert "otomatik zamanlanır" not in html


def test_content_page_shows_auto_publish_notice_in_auto_mode(tmp_db, monkeypatch):
    monkeypatch.setattr(dashboard_module, "APPROVAL_MODE", "auto")
    client = _make_client(tmp_db, monkeypatch, password="")
    _make_draft_content(tmp_db, content_type="post")

    response = client.get("/content")
    html = response.data.decode("utf-8")

    assert "otomatik zamanlanır" in html
    assert "manuel onay modunda" not in html


def test_index_renders_publish_trend_chart(tmp_db, monkeypatch):
    """
    Kullanıcı geri bildirimi: dashboard "hala çok basit" — sadece statik
    sayı kartları vardı, gerçek bir veri grafiği yoktu. Ana sayfa artık son
    7 günün paylaşım sayısını gösteren bir trend grafiği içermeli.
    """
    client = _make_client(tmp_db, monkeypatch, password="")
    news_id = tmp_db.add_news(title="Trend Haberi", url="https://example.com/trend-chart", category="ai")
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.add_publish_record(content_id=content_id, post_type="post", status="success")

    response = client.get("/")
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "trend-chart-card" in html
    assert "Son 7 gün paylaşım trendi" in html


def test_index_shows_instagram_quota_card_when_available(tmp_db, monkeypatch):
    """
    Backlog #2 (codebase-analysis denetimi): instagram_client.py'deki
    get_rate_limit_status() daha önce hiçbir yere bağlı değildi. Ana sayfa
    artık Instagram'ın günlük yayınlama kotasını gösteren bir kart içermeli.
    """
    client = _make_client(tmp_db, monkeypatch, password="")
    # _make_client kendi varsayılan (None döndüren) mock'unu uyguladıktan
    # SONRA ezilmeli — aksi halde monkeypatch "son yazan kazanır" kuralı
    # gereği _make_client'ın içindeki patch bunun üzerine yazardı.
    monkeypatch.setattr(
        "src.instagram_client.InstagramClient.get_rate_limit_status",
        lambda self: {"used": 20, "total": 25, "remaining": 5},
    )

    response = client.get("/")
    html = response.data.decode("utf-8")

    assert "Instagram Günlük Kota" in html
    assert "5/25" in html


def test_index_hides_instagram_quota_card_when_unavailable(tmp_db, monkeypatch):
    """API'ye ulaşılamıyorsa (None dönerse) kart hiç gösterilmemeli — "0/0"
    gibi yanıltıcı bir değer yerine sessizce gizlenmeli."""
    client = _make_client(tmp_db, monkeypatch, password="")  # varsayılan mock None döner

    response = client.get("/")
    html = response.data.decode("utf-8")

    assert "Instagram Günlük Kota" not in html
