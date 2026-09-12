"""Geri yükleme: doğrulama, güvenli varsayılan ve WAL temizliği.

7 Ağustos 2026 denetimine kadar yedek ALMA tarafı sağlamdı ama geri yükleme
tarafı hiç yoktu — ne betik, ne belge. RTO tanımsızdı ve kurtarma bir kez
bile denenmemişti.

Elle geri yüklemenin belgelenmemiş ama KRİTİK adımı: yedeği news.db üzerine
kopyalayıp eski `news.db-wal` dosyasını yerinde bırakırsanız SQLite o WAL'ı
yeni dosyaya uygular ve veritabanını bozar ya da eski veriyi diriltir.
Baskı altında tam olarak bu atlanır.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import restore


def _saglam_yedek(yol: Path, satir: int = 3) -> Path:
    conn = sqlite3.connect(yol)
    conn.executescript("""
        CREATE TABLE news_items (id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE processed_content (id INTEGER PRIMARY KEY);
        CREATE TABLE publish_history (id INTEGER PRIMARY KEY);
    """)
    conn.executemany("INSERT INTO news_items (title) VALUES (?)",
                     [(f"h{i}",) for i in range(satir)])
    conn.commit()
    conn.close()
    return yol


# =============================================
# Doğrulama — ÜZERİNE YAZMADAN ÖNCE
# =============================================

def test_healthy_backup_passes(tmp_path):
    ok, aciklama = restore._dogrula(_saglam_yedek(tmp_path / "y.db"))
    assert ok and "3 haber" in aciklama


def test_missing_file_is_rejected(tmp_path):
    ok, aciklama = restore._dogrula(tmp_path / "yok.db")
    assert not ok and "dosya yok" in aciklama


def test_corrupt_file_is_rejected(tmp_path):
    bozuk = tmp_path / "bozuk.db"
    bozuk.write_bytes(b"bu bir sqlite dosyasi degil" * 50)
    ok, _ = restore._dogrula(bozuk)
    assert not ok


def test_backup_missing_core_tables_is_rejected(tmp_path):
    """
    Açılabilen ama içi yanlış olan bir dosya en tehlikelisi: sağlam görünür.
    """
    yarim = tmp_path / "yarim.db"
    conn = sqlite3.connect(yarim)
    conn.execute("CREATE TABLE news_items (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    ok, aciklama = restore._dogrula(yarim)
    assert not ok and "tablo eksik" in aciklama


# =============================================
# Güvenli varsayılan
# =============================================

def test_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    yedek = _saglam_yedek(tmp_path / "y.db")
    hedef = tmp_path / "canli.db"
    hedef.write_bytes(b"dokunulmamali")
    monkeypatch.setattr(restore, "DB_PATH", hedef)
    cagrilar = []
    monkeypatch.setattr(restore.subprocess, "run",
                        lambda *a, **k: cagrilar.append(a))

    assert restore.geri_yukle(yedek, dry_run=True) == 0
    assert hedef.read_bytes() == b"dokunulmamali"
    assert not cagrilar, "kuru çalıştırmada sistem komutu çalıştırıldı"


def test_corrupt_backup_aborts_before_touching_anything(tmp_path, monkeypatch):
    """Bozuk bir yedeği geri yüklemek, yedeksizlikten kötüdür."""
    bozuk = tmp_path / "bozuk.db"
    bozuk.write_bytes(b"cop" * 100)
    hedef = tmp_path / "canli.db"
    hedef.write_bytes(b"degerli veri")
    monkeypatch.setattr(restore, "DB_PATH", hedef)

    assert restore.geri_yukle(bozuk, dry_run=False) == 2
    assert hedef.read_bytes() == b"degerli veri"


def test_yes_flag_is_required(monkeypatch, tmp_path):
    """`--yes` verilmediyse DAİMA kuru çalışmalı — yıkıcı işlemin varsayılanı güvenli olmalı."""
    yedek = _saglam_yedek(tmp_path / "y.db")
    monkeypatch.setattr(sys, "argv", ["restore.py", str(yedek)])
    monkeypatch.setattr(restore, "DB_PATH", tmp_path / "canli.db")
    kuru = {}
    monkeypatch.setattr(restore, "geri_yukle",
                        lambda y, dry_run: kuru.setdefault("dry_run", dry_run) or 0)

    restore.main()

    assert kuru["dry_run"] is True


# =============================================
# WAL temizliği — asıl mesele
# =============================================

def test_wal_and_shm_are_removed(tmp_path, monkeypatch):
    """
    Eski -wal yerinde bırakılırsa SQLite onu YENİ dosyaya uygular ve
    veritabanını bozar ya da eski veriyi diriltir.
    """
    yedek = _saglam_yedek(tmp_path / "y.db")
    hedef = tmp_path / "canli.db"
    hedef.write_bytes(b"eski")
    wal = Path(str(hedef) + "-wal")
    shm = Path(str(hedef) + "-shm")
    wal.write_bytes(b"bayat wal")
    shm.write_bytes(b"bayat shm")

    monkeypatch.setattr(restore, "DB_PATH", hedef)
    monkeypatch.setattr(restore.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "active", "stderr": ""})())

    restore.geri_yukle(yedek, dry_run=False)

    assert not wal.exists(), "-wal silinmedi: veritabanı bozulabilir"
    assert not shm.exists(), "-shm silinmedi"


def test_current_database_is_kept_aside(tmp_path, monkeypatch):
    """Geri yüklemenin kendisi de geri alınabilir olmalı."""
    yedek = _saglam_yedek(tmp_path / "y.db")
    hedef = tmp_path / "canli.db"
    hedef.write_bytes(b"eski ama degerli")
    monkeypatch.setattr(restore, "DB_PATH", hedef)
    monkeypatch.setattr(restore.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "active", "stderr": ""})())

    restore.geri_yukle(yedek, dry_run=False)

    kenar = list(tmp_path.glob("canli.before_restore_*.db"))
    assert kenar, "mevcut veritabanı kenara alınmadı"
    assert kenar[0].read_bytes() == b"eski ama degerli"


# =============================================
# .env yedeği
# =============================================

def test_env_backup_is_written_with_tight_permissions(tmp_path, monkeypatch):
    from src import maintenance

    sahte_env = tmp_path / ".env"
    sahte_env.write_text("TELEGRAM_BOT_TOKEN=gizli\n", encoding="utf-8")
    monkeypatch.setattr(maintenance, "__file__", str(tmp_path / "src" / "maintenance.py"))

    hedef = maintenance.backup_env_file(backup_dir=tmp_path / "backups")

    assert hedef is not None and hedef.exists()
    assert "gizli" in hedef.read_text(encoding="utf-8")
    if sys.platform != "win32":
        assert oct(hedef.stat().st_mode)[-3:] == "600"


def test_env_backup_is_optional(tmp_path, monkeypatch):
    """`.env` yoksa bakım zinciri patlamamalı."""
    from src import maintenance

    monkeypatch.setattr(maintenance, "__file__", str(tmp_path / "yok" / "maintenance.py"))
    assert maintenance.backup_env_file(backup_dir=tmp_path / "backups") is None
