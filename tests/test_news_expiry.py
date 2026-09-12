"""Bayat işlenmemiş haberin kuyruktan düşürülmesi.

Neden var (2026-08-03 ölçümü): 839 haberin 290'ı (%35) hiç işlenmemişti ve
birikme günde ~45 büyüyordu. `get_unprocessed_news` `collected_at DESC` ile
sıralıyor ve `process_all_news` tur başına ~15 haber alıyor — yani en eski
işlenmemiş haberler yeni gelenlerin altında kalıp asla sıraya gelmiyor.
"""

from datetime import datetime, timedelta


def _add_news_aged(db, title, days_old, url=None):
    """Belirtilen gün kadar eski toplanmış bir haber ekle."""
    news_id = db.add_news(
        title=title, url=url or f"https://example.com/{title}", category="ai"
    )
    stamp = (datetime.now() - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE news_items SET collected_at = ? WHERE id = ?", (stamp, news_id)
        )
    return news_id


def test_stale_unprocessed_news_leaves_the_queue(tmp_db):
    _add_news_aged(tmp_db, "bayat", days_old=5)
    assert len(tmp_db.get_unprocessed_news(limit=10)) == 1

    assert tmp_db.expire_stale_unprocessed_news(days=2) == 1
    assert tmp_db.get_unprocessed_news(limit=10) == []


def test_fresh_news_is_untouched(tmp_db):
    """Eşiğin altındaki haber kuyrukta kalmalı — asıl iş bu."""
    _add_news_aged(tmp_db, "taze", days_old=1)

    assert tmp_db.expire_stale_unprocessed_news(days=2) == 0
    assert len(tmp_db.get_unprocessed_news(limit=10)) == 1


def test_already_processed_news_is_not_touched(tmp_db):
    """Gerçekten işlenmiş haber emekli sayılmamalı, expired_at almamalı."""
    news_id = _add_news_aged(tmp_db, "islenmis", days_old=5)
    tmp_db.mark_news_processed(news_id, relevance_score=0.8)

    assert tmp_db.expire_stale_unprocessed_news(days=2) == 0
    with tmp_db._get_connection() as conn:
        row = conn.execute(
            "SELECT relevance_score, expired_at FROM news_items WHERE id = ?", (news_id,)
        ).fetchone()
    assert row["expired_at"] is None
    assert row["relevance_score"] == 0.8


def test_expired_news_is_distinguishable_from_low_scored(tmp_db):
    """
    Emekliye ayrılan haber ile gerçekten düşük puan almış haber, `is_processed`
    üzerinden ayırt EDİLEMEZ — ikisi de 1. Ayrımı yapan tek şey `expired_at`.

    Bu 2026-08-03 denetiminde yaşanan gerçek bir karışıklıktı: 31 Temmuz
    kurulum günündeki bozuk sıfır puanlar, puanlama yanlılığı sanıldı.
    """
    expired_id = _add_news_aged(tmp_db, "bayat", days_old=5)
    scored_id = _add_news_aged(tmp_db, "dusuk-puan", days_old=5)
    tmp_db.mark_news_processed(scored_id, relevance_score=0.0)

    tmp_db.expire_stale_unprocessed_news(days=2)

    with tmp_db._get_connection() as conn:
        rows = {
            r["id"]: r
            for r in conn.execute(
                "SELECT id, is_processed, relevance_score, expired_at FROM news_items"
            )
        }

    # İkisi de kuyruk dışı...
    assert rows[expired_id]["is_processed"] == 1
    assert rows[scored_id]["is_processed"] == 1
    # ...ama yalnızca biri emekli.
    assert rows[expired_id]["expired_at"] is not None
    assert rows[scored_id]["expired_at"] is None
    # Emekli haber toplama anındaki gerçek puanını KORUR — hiç işlenmediği
    # için 0 yazmak, sonradan "neden atıldı?" sorusunu cevapsız bırakırdı.
    assert rows[expired_id]["relevance_score"] > 0


def test_expiry_is_idempotent(tmp_db):
    """İkinci çalıştırma aynı satırları tekrar saymamalı."""
    _add_news_aged(tmp_db, "bayat", days_old=5)

    assert tmp_db.expire_stale_unprocessed_news(days=2) == 1
    assert tmp_db.expire_stale_unprocessed_news(days=2) == 0


def test_expired_news_is_not_deleted(tmp_db):
    """
    Satır SİLİNMEMELİ: tekilleştirme (mention_count / dedup penceresi) aynı
    tabloya bakıyor, silmek aynı haberin yeniden toplanmasına yol açardı.
    """
    _add_news_aged(tmp_db, "bayat", days_old=5)
    tmp_db.expire_stale_unprocessed_news(days=2)

    with tmp_db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 1


def test_maintenance_runs_expiry(tmp_db, tmp_path, monkeypatch):
    """Bakım zinciri emekliye ayırmayı gerçekten çağırıyor mu?"""
    from src import maintenance

    _add_news_aged(tmp_db, "bayat", days_old=5)
    monkeypatch.setattr(maintenance, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(maintenance, "prune_generated_media", lambda *a, **k: (0, 0))
    monkeypatch.setattr(maintenance, "prune_image_caches", lambda *a, **k: (0, 0))
    monkeypatch.setattr(maintenance, "check_disk_space", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "UNPROCESSED_NEWS_EXPIRY_DAYS", 2)

    summary = maintenance.run_daily_maintenance(tmp_db)

    assert summary["expired_news"] == 1
    assert tmp_db.get_unprocessed_news(limit=10) == []


def test_maintenance_survives_expiry_failure(tmp_db, tmp_path, monkeypatch):
    """Emekliye ayırma patlasa bile zincirin kalanı çalışmalı."""
    from src import maintenance

    monkeypatch.setattr(maintenance, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(maintenance, "prune_generated_media", lambda *a, **k: (3, 0))
    monkeypatch.setattr(maintenance, "prune_image_caches", lambda *a, **k: (0, 0))
    monkeypatch.setattr(maintenance, "check_disk_space", lambda *a, **k: None)
    monkeypatch.setattr(
        tmp_db, "expire_stale_unprocessed_news",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bum")),
    )

    summary = maintenance.run_daily_maintenance(tmp_db)

    assert summary["expired_news"] == 0
    assert summary["media_removed"] == 3  # zincir devam etti
