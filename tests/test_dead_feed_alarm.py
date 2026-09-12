"""Ölü RSS kaynağı sessiz kalmamalı.

Bir feed patladığında yalnızca `logger.warning` yazılıyor, döngü devam
ediyor ve HANGİ kaynağın öldüğü hiçbir yere taşınmıyordu. Sağlık kontrolü
ise `MAX(collected_at)` bakıyor — yani TÜM kaynaklar arasındaki en yeniye.
Tek bir feed çalıştığı sürece kontrol yeşil kalır; kaynakların yarısı
aylarca ölü olabilir ve hiçbir şey söylemez.

Bu sessiz daralmanın bedeli dolaylı ama gerçek: seçim havuzu küçüldükçe
puanlama daha kötü adaylar arasından seçmek zorunda kalır.
"""

import pytest


def _sahte_feed_sonuclari(collector, sonuclar):
    collector.last_feed_results = sonuclar


def test_failed_feeds_are_reported_per_source(tmp_db, monkeypatch):
    from src.news_collector import NewsCollector

    c = NewsCollector(db=tmp_db)
    monkeypatch.setattr("src.news_collector.RSS_FEEDS", {
        "ai": [{"url": "https://olu.example/rss", "name": "OluKaynak"},
               {"url": "https://saglam.example/rss", "name": "SaglamKaynak"}],
    })
    monkeypatch.setattr("src.news_collector.time.sleep", lambda s: None)

    def _sahte(feed_url, source_name, category, language="en"):
        if source_name == "OluKaynak":
            raise RuntimeError("404 Not Found")
        return 3

    monkeypatch.setattr(c, "_parse_rss_feed", _sahte)

    toplam = c._collect_from_rss()

    assert toplam == 3
    assert c.last_feed_results["OluKaynak"]["error"] is not None
    assert c.last_feed_results["SaglamKaynak"]["count"] == 3


def test_collect_all_surfaces_failed_feed_names(tmp_db, monkeypatch):
    from src.news_collector import NewsCollector

    c = NewsCollector(db=tmp_db)
    monkeypatch.setattr(c, "_collect_from_rss",
                        lambda: _sahte_feed_sonuclari(c, {
                            "A": {"count": 0, "error": "timeout"},
                            "B": {"count": 5, "error": None},
                        }) or 5)
    monkeypatch.setattr("src.news_collector.NEWS_API_KEY", "", raising=False)
    monkeypatch.setattr("src.news_collector.CURRENTS_API_KEY", "", raising=False)

    stats = c.collect_all()

    assert stats["failed_feeds"] == ["A"]


def test_scheduler_records_failed_feeds(tmp_db):
    from src.scheduler import Scheduler

    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    s.collector = type("C", (), {
        "collect_all": lambda self: {"total": 5, "failed_feeds": ["A", "B"]}
    })()

    s.collect_news()

    assert tmp_db.get_setting("last_failed_feeds") == "A|B"


def test_healthcheck_reports_dead_feeds(tmp_db):
    from src.healthcheck import run_health_checks

    tmp_db.add_news(title="taze", url="https://example.com/1", category="ai")
    tmp_db.set_setting("last_failed_feeds", "Gematsu|Sportskeeda|MMORPG")

    sorunlar = run_health_checks(tmp_db)

    assert any("3 RSS kaynağı" in s for s in sorunlar), sorunlar
    assert any("Gematsu" in s for s in sorunlar)


def test_healthy_feeds_produce_no_alarm(tmp_db):
    from src.healthcheck import run_health_checks

    tmp_db.add_news(title="taze", url="https://example.com/2", category="ai")
    tmp_db.set_setting("last_failed_feeds", "")

    assert not any("RSS kaynağı" in s for s in run_health_checks(tmp_db))


def test_recording_failure_does_not_break_collection(tmp_db, monkeypatch):
    """Kayıt yazılamazsa toplama yine de sonucunu döndürmeli."""
    from src.scheduler import Scheduler

    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    s.collector = type("C", (), {
        "collect_all": lambda self: {"total": 7, "failed_feeds": []}
    })()
    monkeypatch.setattr(tmp_db, "set_setting",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    assert s.collect_news()["total"] == 7


# =============================================
# "Hiç yayın çıkmıyor" alarmı
#
# Sağlık kontrolünün diğer maddeleri boru hattının PARÇALARINA bakıyor:
# haber geliyor mu, kuyruk tıkalı mı, token geçerli mi, disk dolu mu.
# Hiçbiri "peki bu hesapta bir şey yayınlanıyor mu?" diye sormuyordu.
# Yayın tamamen dursa bütün kontroller yeşil kalıyordu — dosyanın kendi
# felsefesinin ("çalışıyor GÖRÜNMESİ değil, işini YAPIYOR olması") kör
# noktası: her parça sağlıklı, ürün yok.
# =============================================

def _taze_haber(db):
    db.add_news(title="taze", url="https://example.com/taze", category="ai")


def _yayin(db, gun_once=0):
    from datetime import datetime, timedelta
    news_id = db.add_news(title="x", url=f"https://example.com/y{gun_once}",
                          category="ai")
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text="ö")
    ph = db.add_publish_record(content_id=cid, post_type="post",
                               status="success", instagram_media_id="m1")
    an = (datetime.now() - timedelta(days=gun_once)).strftime("%Y-%m-%d %H:%M:%S")
    with db._get_connection() as conn:
        conn.execute("UPDATE publish_history SET published_at=? WHERE id=?", (an, ph))


def test_silence_is_reported(tmp_db):
    from src.healthcheck import run_health_checks

    _taze_haber(tmp_db)
    _yayin(tmp_db, gun_once=9)   # son yayın 9 gün önce

    sorunlar = run_health_checks(tmp_db)
    assert any("hiç paylaşım yapılmadı" in s for s in sorunlar), sorunlar


def test_recent_publish_produces_no_alarm(tmp_db):
    from src.healthcheck import run_health_checks

    _taze_haber(tmp_db)
    _yayin(tmp_db, gun_once=0)

    assert not any("hiç paylaşım" in s for s in run_health_checks(tmp_db))


def test_message_distinguishes_waiting_content_from_empty_queue(tmp_db):
    """
    "Onay bekleyen içerik var ama yayın yok" ile "üretim de durmuş" farklı
    arızalar; mesaj hangisi olduğunu söylemeli.
    """
    from src.healthcheck import run_health_checks

    _taze_haber(tmp_db)
    _yayin(tmp_db, gun_once=9)
    news_id = tmp_db.add_news(title="hazır", url="https://example.com/h",
                              category="ai")
    cid = tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                             summary_text="ö")
    tmp_db.update_content_media(cid, "/tmp/x.png")

    sorunlar = run_health_checks(tmp_db)
    assert any("onay bekliyor" in s for s in sorunlar), sorunlar
