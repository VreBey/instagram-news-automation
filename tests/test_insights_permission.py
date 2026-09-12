"""Insights izin hatasının doğru ele alınması.

Denetim bulgusu (2026-08-04): 58 yayının 58'inde insights isteği başarısızdı
ve `media_insights` tablosu tamamen boştu — yani hangi içeriğin gerçekten
erişim aldığı bilinmiyordu. Sebep metrik adı DEĞİL, token'da
`instagram_manage_insights` izninin hiç olmamasıydı (Meta hata kodu #10).

Sistem bunu gizliyordu: her medya için ayrı bir WARNING basıp "0/58" diyor,
hiçbir uyarı üretmiyordu. Bu testler üç şeyi kilitler — hata tanınsın,
tur bir kez kesilsin, ve durum sağlık kontrolüne düşsün.
"""

import pytest

from src.instagram_client import InsightsPermissionError


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def client(monkeypatch):
    from src.instagram_client import InstagramClient
    c = InstagramClient.__new__(InstagramClient)
    c.graph_url = "https://graph.facebook.com/v21.0"
    c.access_token = "test-token"
    return c


def _yanit_ver(client, response):
    class _Session:
        def get(self, *a, **k):
            return response
    client.session = _Session()


# =============================================
# Hatanın tanınması
# =============================================

def test_permission_error_is_raised(client):
    _yanit_ver(client, _Response(400, {"error": {
        "code": 10,
        "message": "(#10) Application does not have permission for this action",
    }}))
    with pytest.raises(InsightsPermissionError):
        client.get_media_insights("123", post_type="post")


def test_other_400_is_not_permission_error(client):
    """Süresi dolmuş hikaye (#100) izin sorunu değil — turu kesmemeli."""
    _yanit_ver(client, _Response(400, {"error": {
        "code": 100, "message": "Tried accessing nonexisting field (insights)",
    }}))
    assert client.get_media_insights("123", post_type="story") is None


def test_not_enough_viewers_is_not_permission_error(client):
    """
    Meta (#10) kodunu BİRDEN FAZLA durum için kullanıyor. "Not enough
    viewers" o medyaya özel bir gizlilik eşiği — izin sorunu değil.

    Yalnızca koda bakmak ikisini karıştırıyordu ve tek bir düşük erişimli
    gönderi TÜM senkronizasyonu durduruyordu (5 Ağustos 2026). 2 takipçili
    bir hesapta gönderilerin çoğu bu hatayı alacağı için insights pratikte
    tamamen kullanılamaz hale gelmişti.
    """
    _yanit_ver(client, _Response(400, {"error": {
        "code": 10,
        "message": "(#10) Not enough viewers for the media to show insights",
    }}))
    assert client.get_media_insights("123") is None


def test_low_reach_media_does_not_stop_the_run(scheduler, tmp_db):
    """Bir medyanın metriği yoksa diğerleri toplanmaya devam etmeli."""
    _seed_published(tmp_db, 3)
    cagri = []

    class _IG:
        def is_configured(self): return True
        def get_media_insights(self, mid, post_type="post"):
            cagri.append(mid)
            # İlki "yeterli izleyici yok" -> None; diğerleri veri döner.
            if len(cagri) == 1:
                return None
            return {"reach": 12, "likes": 1, "comments": 0}

    scheduler.ig_client = _IG()
    sonuc = scheduler.sync_insights()

    assert len(cagri) == 3, "dusuk erisimli medya turu kesmemeli"
    assert sonuc["synced"] == 2
    assert not sonuc.get("permission_error")


def test_successful_response_is_parsed(client):
    _yanit_ver(client, _Response(200, {"data": [
        {"name": "reach", "values": [{"value": 1200}]},
        {"name": "likes", "values": [{"value": 45}]},
    ]}))
    assert client.get_media_insights("123") == {"reach": 1200, "likes": 45}


def test_deprecated_impressions_metric_is_not_requested(client):
    """
    `impressions` Meta tarafından media insights'tan kaldırıldı; istenmeye
    devam etmek gereksiz 400 riski.
    """
    istekler = []

    class _Session:
        def get(self, url, params=None, **k):
            istekler.append(params["metric"])
            return _Response(200, {"data": []})

    client.session = _Session()
    for tip in ("post", "story"):
        client.get_media_insights("123", post_type=tip)
    assert all("impressions" not in m for m in istekler)


# =============================================
# Turun kesilmesi ve durum kaydı
# =============================================

@pytest.fixture
def scheduler(tmp_db):
    from src.scheduler import Scheduler
    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    return s


def _seed_published(db, n, post_type="post"):
    from datetime import datetime
    for i in range(n):
        news_id = db.add_news(title=f"H{i}", url=f"https://example.com/{post_type}{i}",
                              category="ai")
        cid = db.add_content(news_id=news_id, content_type=post_type, caption="c")
        db.add_publish_record(content_id=cid, post_type=post_type, status="success",
                              instagram_media_id=f"media{post_type}{i}")


def test_sync_stops_after_first_permission_error(scheduler, tmp_db):
    """58 medya için 58 kez denemek anlamsız ve asıl sebebi gizliyor."""
    _seed_published(tmp_db, 5)
    cagri = []

    class _IG:
        def is_configured(self): return True
        def get_media_insights(self, mid, post_type="post"):
            cagri.append(mid)
            raise InsightsPermissionError("(#10) no permission")

    scheduler.ig_client = _IG()
    sonuc = scheduler.sync_insights()

    assert sonuc["permission_error"] is True
    assert sonuc["synced"] == 0
    assert len(cagri) == 1, "izin hatasindan sonra denemeye devam edilmemeli"


def test_permission_status_is_recorded(scheduler, tmp_db):
    _seed_published(tmp_db, 2)

    class _IG:
        def is_configured(self): return True
        def get_media_insights(self, mid, post_type="post"):
            raise InsightsPermissionError("x")

    scheduler.ig_client = _IG()
    scheduler.sync_insights()
    assert tmp_db.get_setting("insights_permission_status") == "missing"


def test_status_clears_after_successful_sync(scheduler, tmp_db):
    """Düzelen bir durum bildirilmeye devam etmemeli."""
    _seed_published(tmp_db, 2)
    tmp_db.set_setting("insights_permission_status", "missing")

    class _IG:
        def is_configured(self): return True
        def get_media_insights(self, mid, post_type="post"):
            return {"reach": 100, "likes": 5, "comments": 1}

    scheduler.ig_client = _IG()
    scheduler.sync_insights()
    assert tmp_db.get_setting("insights_permission_status") == "ok"


# =============================================
# Bayat hikayelerin dışlanması
# =============================================

def test_old_stories_are_excluded(tmp_db):
    """
    Hikayeler 24 saat sonra kayboluyor; medya nesnesi API'de çözülmüyor.
    Bunları sorgulamak her turda garantili başarısız istek demekti.
    """
    _seed_published(tmp_db, 1, post_type="story")
    _seed_published(tmp_db, 1, post_type="post")
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE publish_history SET published_at = datetime('now','-3 days')")

    kayitlar = tmp_db.get_publish_history_for_insights(days=14)
    assert [k["post_type"] for k in kayitlar] == ["post"]


def test_fresh_stories_are_included(tmp_db):
    _seed_published(tmp_db, 1, post_type="story")
    kayitlar = tmp_db.get_publish_history_for_insights(days=14)
    assert len(kayitlar) == 1


# =============================================
# Sağlık kontrolü
# =============================================

def test_healthcheck_reports_missing_permission(tmp_db):
    from src.healthcheck import run_health_checks
    tmp_db.add_news(title="Taze", url="https://example.com/f", category="ai")
    tmp_db.mark_news_processed(1, relevance_score=0.8)
    tmp_db.set_setting("insights_permission_status", "missing")

    sorunlar = run_health_checks(tmp_db)
    assert any("instagram_manage_insights" in s for s in sorunlar)
