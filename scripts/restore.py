"""Veritabanını bir yedekten geri yükler.

NEDEN VAR: 7 Ağustos 2026 denetimine kadar bu projede yedek ALMA tarafı
sağlamdı (SQLite `Connection.backup()`, `PRAGMA integrity_check`, bozuk
yedeği saklamama, en yeniyi koruma) ama **geri yükleme tarafı hiç yoktu** —
ne betik, ne belge, ne prosedür. Yani RTO tanımsızdı ve kurtarma bir kez
bile denenmemişti.

Elle geri yüklemenin belgelenmemiş ama KRİTİK adımı şu: yedeği `news.db`
üzerine kopyalayıp eski `news.db-wal` dosyasını yerinde bırakırsanız SQLite
o WAL'ı yeni dosyaya uygular ve **veritabanını bozar ya da eski veriyi
diriltir**. Baskı altında tam olarak bu atlanır. Bu betik o adımı zorunlu
kılıyor.

Kullanım (VDS'te, root olarak):
    python scripts/restore.py --list                  # yedekleri listele
    python scripts/restore.py --dry-run <yedek>       # ne yapacağını göster
    python scripts/restore.py --yes <yedek>           # gerçekten geri yükle

Betik hiçbir şeyi onaysız yapmaz: `--yes` verilmeden yalnızca planı yazar.
"""

import argparse
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BACKUP_DIR, DB_PATH

# Geri yükleme sırasında durdurulması gereken servisler. Durdurulmazsa
# `Restart=always` onları geri getirir ve yarısı geri yüklenmiş dosyaya
# yazarlar — sessiz bozulmanın en kolay yolu.
SERVICES = ("instagram-scheduler", "instagram-telegram",
            "instagram-dashboard", "instagram-mcp")


def _yedekleri_listele() -> list[Path]:
    return sorted(Path(BACKUP_DIR).glob("news_*.db"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _dogrula(yedek: Path) -> tuple[bool, str]:
    """Yedek gerçekten sağlam mı? ÜZERİNE YAZMADAN ÖNCE kontrol edilir."""
    if not yedek.exists():
        return False, "dosya yok"
    try:
        conn = sqlite3.connect(f"file:{yedek}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as e:
        return False, f"açılamadı: {e}"
    try:
        sonuc = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if sonuc != "ok":
            return False, f"integrity_check: {sonuc}"
        tablolar = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        eksik = {"news_items", "processed_content", "publish_history"} - tablolar
        if eksik:
            return False, f"tablo eksik: {', '.join(sorted(eksik))}"
        n = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        return True, f"sağlam, {n} haber satırı"
    except sqlite3.Error as e:
        return False, f"okunamadı: {e}"
    finally:
        conn.close()


def _systemctl(eylem: str, dry_run: bool) -> None:
    for svc in SERVICES:
        if dry_run:
            print(f"    [kuru] systemctl {eylem} {svc}")
            continue
        subprocess.run(["systemctl", eylem, svc], check=False)


def geri_yukle(yedek: Path, dry_run: bool) -> int:
    db = Path(DB_PATH)

    print(f"\nYedek : {yedek}")
    saglam, aciklama = _dogrula(yedek)
    print(f"Kontrol: {'✅' if saglam else '❌'} {aciklama}")
    if not saglam:
        print("\nGeri yükleme İPTAL — bozuk bir yedeği geri yüklemek, "
              "yedeksizlikten kötüdür.")
        return 2

    print(f"Hedef : {db}")
    print("\nAdımlar:")
    print(f"  1. {len(SERVICES)} servis durdurulur")
    print(f"  2. Mevcut veritabanı kenara alınır (geri yükleme de geri alınabilsin)")
    print(f"  3. -wal / -shm dosyaları SİLİNİR  ← atlanırsa veritabanı bozulur")
    print(f"  4. Yedek kopyalanır, sahiplik appuser'a verilir")
    print(f"  5. Servisler başlatılır ve doğrulanır")

    if dry_run:
        print("\n(kuru çalıştırma — hiçbir şey değiştirilmedi)")
        return 0

    print("\n--> 1/5 servisler durduruluyor")
    _systemctl("stop", dry_run)

    print("--> 2/5 mevcut veritabanı kenara alınıyor")
    if db.exists():
        kenar = db.with_name(f"{db.stem}.before_restore_"
                             f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db, kenar)
        print(f"    {kenar.name}")

    print("--> 3/5 -wal / -shm siliniyor")
    for ek in ("-wal", "-shm"):
        yan = Path(str(db) + ek)
        if yan.exists():
            yan.unlink()
            print(f"    silindi: {yan.name}")
        else:
            print(f"    yok: {yan.name}")

    print("--> 4/5 yedek kopyalanıyor")
    shutil.copy2(yedek, db)
    subprocess.run(["chown", "appuser:appuser", str(db)], check=False)

    print("--> 5/5 servisler başlatılıyor")
    _systemctl("start", dry_run)

    print("\nDurum:")
    hatali = 0
    for svc in SERVICES:
        r = subprocess.run(["systemctl", "is-active", svc],
                           capture_output=True, text=True)
        durum = (r.stdout or r.stderr).strip()
        print(f"    {svc:<28} {durum}")
        if durum != "active":
            hatali += 1

    saglam, aciklama = _dogrula(db)
    print(f"\nGeri yüklenen veritabanı: {'✅' if saglam else '❌'} {aciklama}")

    if hatali or not saglam:
        print("\n⚠️  Geri yükleme tamamlandı ama sorun var — journalctl'e bakın.")
        return 1
    print("\n✅ Geri yükleme tamam.")
    print("   NOT: .env geri yüklenmedi. Gerekiyorsa "
          "data/backups/env_*.bak dosyasından elle kopyalayın (chmod 600).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Veritabanını yedekten geri yükle")
    p.add_argument("backup", nargs="?", help="Yedek dosyası (yol ya da dosya adı)")
    p.add_argument("--list", action="store_true", help="Mevcut yedekleri listele")
    p.add_argument("--dry-run", action="store_true",
                   help="Ne yapılacağını göster, hiçbir şeyi değiştirme")
    p.add_argument("--yes", action="store_true",
                   help="Gerçekten geri yükle (bu bayrak olmadan kuru çalışır)")
    args = p.parse_args()

    if args.list or not args.backup:
        yedekler = _yedekleri_listele()
        if not yedekler:
            print(f"Yedek bulunamadı: {BACKUP_DIR}")
            return 1
        print(f"{BACKUP_DIR} içindeki yedekler (en yeni önce):\n")
        for y in yedekler:
            saglam, aciklama = _dogrula(y)
            yas = datetime.fromtimestamp(y.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {'✅' if saglam else '❌'} {y.name:<32} {yas}  "
                  f"{y.stat().st_size / 1024 / 1024:.1f} MB  {aciklama}")
        if not args.backup:
            print("\nGeri yüklemek için: python scripts/restore.py --yes <dosya_adi>")
        return 0

    yedek = Path(args.backup)
    if not yedek.is_absolute() and not yedek.exists():
        yedek = Path(BACKUP_DIR) / args.backup

    # `--yes` verilmediyse DAİMA kuru çalış. Geri yükleme yıkıcı bir
    # işlem; varsayılanın güvenli olması gerekiyor.
    return geri_yukle(yedek, dry_run=not args.yes)


if __name__ == "__main__":
    sys.exit(main())
