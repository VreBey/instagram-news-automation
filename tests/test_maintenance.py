"""src.maintenance birim testleri — yedekleme, retention, disk kontrolü."""

import sqlite3
import time
from pathlib import Path

import pytest

from src import maintenance
from src.maintenance import (
    backup_database,
    check_disk_space,
    get_latest_backup_age_hours,
    prune_old_backups,
    run_daily_maintenance,
)


def _age_file(path: Path, days: float):
    """Dosyanın değiştirilme zamanını geriye al (retention testleri için)."""
    old = time.time() - days * 86400
    import os
    os.utime(path, (old, old))


# =============================================
# Yedekleme
# =============================================

def test_backup_creates_verifiable_copy(tmp_db, tmp_path):
    tmp_db.add_news(title="Haber", url="https://example.com/1", category="ai")
    backup_dir = tmp_path / "backups"

    result = backup_database(db_path=tmp_db.db_path, backup_dir=backup_dir)

    assert result is not None and result.exists()
    # Yedek gerçekten okunabilir ve veriyi taşıyor olmalı.
    with sqlite3.connect(str(result)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 1


def test_backup_captures_unflushed_wal_writes(tmp_db, tmp_path):
    """
    Asıl gerekçe: proje WAL modunda ve dört süreç aynı dosyaya yazıyor.
    Düz dosya kopyası `-wal` dosyasını almadığı için henüz ana dosyaya
    işlenmemiş yazmaları KAÇIRIR. sqlite3'ün backup() API'si almalı.
    """
    for i in range(5):
        tmp_db.add_news(title=f"H{i}", url=f"https://example.com/{i}", category="ai")

    result = backup_database(db_path=tmp_db.db_path, backup_dir=tmp_path / "b")

    with sqlite3.connect(str(result)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 5


def test_backup_returns_none_when_source_missing(tmp_path):
    result = backup_database(db_path=tmp_path / "yok.db", backup_dir=tmp_path / "b")
    assert result is None


def test_corrupt_backup_is_deleted_not_kept(tmp_db, tmp_path, monkeypatch):
    """Bozuk bir yedeği saklamak, yedeğin hiç olmamasından tehlikelidir."""
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(maintenance, "_verify_backup", lambda p: False)

    result = backup_database(db_path=tmp_db.db_path, backup_dir=backup_dir)

    assert result is None
    assert list(backup_dir.glob("news_*.db")) == []


def test_prune_keeps_newest_even_when_all_are_old(tmp_path):
    """Sistem bir süre durmuşsa hepsi 'eski' olur — son kopya yine de kalmalı."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(3):
        f = backup_dir / f"news_2026010{i}_000000.db"
        f.write_text("x")
        _age_file(f, days=100 - i)

    prune_old_backups(backup_dir=backup_dir, keep_days=14)

    assert len(list(backup_dir.glob("news_*.db"))) == 1


def test_prune_removes_only_expired_backups(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    fresh = backup_dir / "news_20260803_000000.db"
    fresh.write_text("x")
    old = backup_dir / "news_20260701_000000.db"
    old.write_text("x")
    _age_file(old, days=40)

    removed = prune_old_backups(backup_dir=backup_dir, keep_days=14)

    assert removed == 1
    assert fresh.exists() and not old.exists()


def test_backup_age_none_when_no_backups(tmp_path):
    assert get_latest_backup_age_hours(backup_dir=tmp_path / "yok") is None


def test_backup_age_reflects_newest_file(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "news_20260701_000000.db"
    old.write_text("x")
    _age_file(old, days=10)
    (backup_dir / "news_20260803_000000.db").write_text("x")

    age = get_latest_backup_age_hours(backup_dir=backup_dir)
    assert age is not None and age < 1


# =============================================
# Disk retention
# =============================================

def test_prune_directory_removes_only_old_files(tmp_path):
    d = tmp_path / "posts"
    d.mkdir()
    old = d / "post_eski.png"
    old.write_bytes(b"0" * 100)
    _age_file(old, days=30)
    fresh = d / "post_yeni.png"
    fresh.write_bytes(b"0" * 100)

    removed, freed = maintenance._prune_directory(d, keep_days=14)

    assert removed == 1 and freed == 100
    assert fresh.exists() and not old.exists()


def test_prune_directory_never_deletes_gitkeep(tmp_path):
    """`.gitkeep` silinirse dizin git'ten düşer ve sonraki kurulum patlar."""
    d = tmp_path / "posts"
    d.mkdir()
    keep = d / ".gitkeep"
    keep.write_text("")
    _age_file(keep, days=999)

    removed, _ = maintenance._prune_directory(d, keep_days=14)

    assert removed == 0 and keep.exists()


def test_prune_directory_tolerates_missing_dir(tmp_path):
    assert maintenance._prune_directory(tmp_path / "yok", keep_days=14) == (0, 0)


def test_prune_generated_media_covers_all_three_output_dirs(tmp_path, monkeypatch):
    dirs = {}
    for name in ("POSTS_OUTPUT_DIR", "STORIES_OUTPUT_DIR", "REELS_OUTPUT_DIR"):
        d = tmp_path / name
        d.mkdir()
        f = d / "eski.bin"
        f.write_bytes(b"0" * 10)
        _age_file(f, days=30)
        dirs[name] = d
        monkeypatch.setattr(maintenance, name, d)

    removed, freed = maintenance.prune_generated_media(keep_days=14)

    assert removed == 3 and freed == 30
    assert all(not (d / "eski.bin").exists() for d in dirs.values())


# =============================================
# Disk doluluk
# =============================================

def test_disk_check_silent_below_threshold(monkeypatch):
    monkeypatch.setattr(
        maintenance, "get_disk_usage",
        lambda *a, **k: {"total_gb": 100, "used_gb": 40, "free_gb": 60, "percent_used": 40.0},
    )
    assert check_disk_space() is None


def test_disk_check_warns_above_threshold(monkeypatch):
    monkeypatch.setattr(
        maintenance, "get_disk_usage",
        lambda *a, **k: {"total_gb": 100, "used_gb": 92, "free_gb": 8, "percent_used": 92.0},
    )
    warning = check_disk_space()
    assert warning is not None and "%92" in warning


# =============================================
# Günlük bakım zinciri
# =============================================

def test_maintenance_backs_up_before_deleting(tmp_db, tmp_path, monkeypatch):
    """
    Sıra kritik: veri silen HER adımdan önce yedek alınmalı. Hatalı bir
    temizlik çalıştığında geri dönülecek kopya kalmalı.
    """
    calls = []
    monkeypatch.setattr(maintenance, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(
        maintenance, "backup_database",
        lambda *a, **k: (calls.append("backup"), tmp_path / "f.db")[1],
    )
    monkeypatch.setattr(maintenance, "prune_old_backups", lambda *a, **k: 0)
    monkeypatch.setattr(
        maintenance, "prune_generated_media",
        lambda *a, **k: (calls.append("prune_media"), (0, 0))[1],
    )
    monkeypatch.setattr(maintenance, "prune_image_caches", lambda *a, **k: (0, 0))
    monkeypatch.setattr(maintenance, "check_disk_space", lambda *a, **k: None)
    monkeypatch.setattr(
        tmp_db, "cleanup_old_data",
        lambda *a, **k: calls.append("cleanup"),
    )
    monkeypatch.setattr(tmp_db, "vacuum", lambda *a, **k: None)

    run_daily_maintenance(tmp_db)

    assert calls.index("backup") < calls.index("cleanup")
    assert calls.index("backup") < calls.index("prune_media")


def test_maintenance_continues_when_cleanup_fails(tmp_db, tmp_path, monkeypatch):
    """Bir adımın patlaması diğer bakım adımlarını iptal etmemeli."""
    monkeypatch.setattr(maintenance, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(maintenance, "prune_generated_media", lambda *a, **k: (7, 0))
    monkeypatch.setattr(maintenance, "prune_image_caches", lambda *a, **k: (0, 0))
    monkeypatch.setattr(maintenance, "check_disk_space", lambda *a, **k: None)
    monkeypatch.setattr(
        tmp_db, "cleanup_old_data",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )

    summary = run_daily_maintenance(tmp_db)

    assert summary["db_cleaned"] is False
    assert summary["media_removed"] == 7  # zincir devam etti


def test_vacuum_shrinks_database_after_deletes(tmp_db):
    """VACUUM olmadan SQLite dosyası satırlar silinse bile küçülmez."""
    for i in range(300):
        tmp_db.add_news(
            title=f"Haber {i}" + "x" * 500,
            url=f"https://example.com/{i}",
            category="ai",
        )
    with tmp_db._get_connection() as conn:
        conn.execute("DELETE FROM news_items")

    before = Path(tmp_db.db_path).stat().st_size
    tmp_db.vacuum()
    after = Path(tmp_db.db_path).stat().st_size

    assert after < before
