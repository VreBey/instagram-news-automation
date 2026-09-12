"""src.healthcheck birim testleri — gerçek Telegram çağrısı yapılmaz."""

from datetime import datetime, timedelta

import pytest

from src.healthcheck import check_and_alert, run_health_checks


@pytest.fixture(autouse=True)
def _quiet_environment_checks(monkeypatch, tmp_path):
    """Ortama bağlı iki kontrolü varsayılan olarak 'sorunsuz' yap.

    Disk doluluğu ve yedek yaşı gerçek makineye bakar; testte olduğu gibi
    bırakılırsa geliştiricinin diski %80 doluyken ilgisiz testler kırmızıya
    döner. Bu iki kontrolün KENDİ testleri aşağıda ayrıca var ve orada
    bilinçli olarak geri açılıyor.
    """
    monkeypatch.setattr("src.maintenance.check_disk_space", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.maintenance.get_latest_backup_age_hours", lambda *a, **k: 1.0
    )


def _seed_fresh_news(db):
    news_id = db.add_news(title="Taze", url="https://example.com/f", category="ai")
    db.mark_news_processed(news_id, relevance_score=0.8)
    return news_id


def _seed_recent_publish(db):
    """SAĞLIKLI sistem tanımının parçası: hesapta bir şey yayınlanıyor olmalı.

    Sağlık kontrolü artık "N gündür hiç paylaşım yok" durumunu da bildiriyor.
    Diğer maddelerin hepsi boru hattının PARÇALARINA bakıyordu; yayın tamamen
    dursa bütün kontroller yeşil kalıyordu — her parça sağlıklı, ürün yok.
    """
    news_id = db.add_news(title="Yayınlanan", url="https://example.com/pub",
                          category="ai")
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text="ö")
    db.add_publish_record(content_id=cid, post_type="post", status="success",
                          instagram_media_id="m-saglikli")
    return news_id


def test_reports_problem_when_no_news_at_all(tmp_db):
    assert any("hiç haber yok" in p for p in run_health_checks(tmp_db))


def test_reports_stale_feed(tmp_db):
    _seed_fresh_news(tmp_db)
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE news_items SET collected_at = datetime('now', '-72 hours')")
    assert any("saattir yeni haber" in p for p in run_health_checks(tmp_db))


def test_healthy_system_reports_no_problems(tmp_db):
    _seed_fresh_news(tmp_db)
    _seed_recent_publish(tmp_db)
    assert run_health_checks(tmp_db) == []


def test_reports_recent_publish_failures(tmp_db):
    news_id = _seed_fresh_news(tmp_db)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.add_publish_record(content_id=content_id, post_type="post", status="failed")
    assert any("başarısız" in p for p in run_health_checks(tmp_db))


def test_alert_is_not_repeated_while_state_unchanged(tmp_db, mocker, monkeypatch):
    """
    Aynı sorun her turda tekrar tekrar mesaj atmamalı — yalnızca durum
    DEĞİŞTİĞİNDE bildirilmeli, aksi halde uyarılar gürültüye dönüşür.
    """
    monkeypatch.setattr("config.TELEGRAM_CHAT_ID", "123", raising=False)
    send = mocker.patch("src.telegram_bot._send_text", return_value=1)

    first = check_and_alert(tmp_db)
    second = check_and_alert(tmp_db)

    assert first["notified"] is True
    assert second["notified"] is False
    assert send.call_count == 1


# --- Disk ve yedek kontrolleri (2026-08-03 denetiminde eklendi) ---

def test_reports_full_disk(tmp_db, monkeypatch):
    _seed_fresh_news(tmp_db)
    monkeypatch.setattr(
        "src.maintenance.check_disk_space", lambda *a, **k: "Disk %92 dolu (3.1 GB boş kaldı)."
    )
    assert any("Disk %92" in p for p in run_health_checks(tmp_db))


def test_reports_missing_backup(tmp_db, monkeypatch):
    """Yedeğin hiç olmaması sessiz ama en pahalı arıza — mutlaka raporlanmalı."""
    _seed_fresh_news(tmp_db)
    monkeypatch.setattr("src.maintenance.get_latest_backup_age_hours", lambda *a, **k: None)
    assert any("hiç yedeği yok" in p for p in run_health_checks(tmp_db))


def test_reports_stale_backup(tmp_db, monkeypatch):
    """Bir kez alınıp sonra durmuş yedekleme, 'korunuyoruz' yanılsaması yaratır."""
    _seed_fresh_news(tmp_db)
    monkeypatch.setattr("src.maintenance.get_latest_backup_age_hours", lambda *a, **k: 96.0)
    assert any("96 saatlik" in p for p in run_health_checks(tmp_db))


def test_recent_backup_is_not_reported(tmp_db, monkeypatch):
    _seed_fresh_news(tmp_db)
    _seed_recent_publish(tmp_db)
    monkeypatch.setattr("src.maintenance.get_latest_backup_age_hours", lambda *a, **k: 12.0)
    assert run_health_checks(tmp_db) == []
