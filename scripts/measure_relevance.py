"""Puanlama fonksiyonunun ayırt etme gücünü YER GERÇEĞİNE karşı ölçer.

Yer gerçeği: kullanıcının Telegram/dashboard üzerinden verdiği kararlar.
Yayınlanan/onaylanan içerik "iyi", reddedilen "kötü" sayılır. İyi bir puanlama
fonksiyonu bu ikisini ayırmalıdır.

Kullanım (VDS'te):
    sudo -u appuser ./venv/bin/python scripts/measure_relevance.py

Neden var: puanlama artık yalnızca içerik üretmeyi değil, hangi haberin
İŞLENECEĞİNİ de belirliyor (bkz. src/relevance.py). Dolayısıyla oradaki
katsayıları değiştirmek doğrudan neyin yayınlandığını değiştirir. Bu script
olmadan "daha iyi oldu" demek tahminden ibaret olurdu.
"""

import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH
from src.relevance import calculate_relevance


def eski_puanlama(news: dict) -> float:
    """2026-08-03 öncesi algoritma — karşılaştırma tabanı."""
    score = 0.5
    text = ((news.get("title") or "") + " " + (news.get("description") or "")).lower()
    high = [
        "breakthrough", "launch", "release", "announce", "reveal",
        "exclusive", "first", "new", "major", "record",
        "çığır açan", "yeni", "duyuruldu", "rekor", "lansman",
        "gpt-5", "gpt5", "gemini", "claude", "ps6", "gta 6",
        "nintendo switch 2", "unreal engine", "ai model",
    ]
    low = ["opinion", "editorial", "review", "rumor", "leak",
           "sponsored", "ad ", "advertisement"]
    for k in high:
        if k in text:
            score += 0.1
    for k in low:
        if k in text:
            score -= 0.1
    if news.get("description") and len(news["description"]) > 50:
        score += 0.05
    if news.get("image_url"):
        score += 0.05
    mc = news.get("mention_count") or 1
    score += 0.05 * min(3, mc - 1)
    return max(0.0, min(1.0, score))


def _yer_gercegi(conn) -> tuple[list[dict], list[dict]]:
    """(onaylanan, reddedilen) haber kayıtlarını döndür.

    Yalnızca KULLANICININ verdiği kararlar sayılır. İki tür 'rejected'
    dışarıda bırakılıyor, çünkü ikisi de içerik hakkında bir yargı değil:

    * `expired_at` dolu olanlar — kuyrukta bayatladığı için düşenler.
      Bunlar kapasite hakkında bir sinyal; puanlamayla ilgisi yok ve
      sayıca kullanıcı kararlarını ezecek kadar çoklar.
    * `used_in_roundup` olanlar — günlük derlemeye girdiği için tekil
      yayınlanmayanlar. Kullanıcı onları reddetmedi.
    """
    rows = conn.execute("""
        SELECT n.*, pc.status
        FROM processed_content pc
        JOIN news_items n ON pc.news_id = n.id
        WHERE pc.status IN ('published', 'approved', 'rejected')
          AND pc.expired_at IS NULL
          AND COALESCE(pc.used_in_roundup, 0) = 0
    """).fetchall()
    onay, red = [], []
    for r in rows:
        d = dict(r)
        (onay if d["status"] in ("published", "approved") else red).append(d)
    return onay, red


def _rapor(ad: str, fn, onay: list[dict], red: list[dict]) -> float:
    o = [fn(x) for x in onay]
    r = [fn(x) for x in red]
    fark = statistics.mean(o) - statistics.mean(r)

    # Ayrım gücünü ölçek-bağımsız kıl: iki dağılımın ortalama farkını ortak
    # standart sapmaya böl (Cohen's d). Katsayıları değiştirdiğimizde ham
    # fark da değişir; d bunu karşılaştırılabilir tutar.
    havuz = statistics.pstdev(o + r) or 1e-9
    d = fark / havuz

    print(f"  {ad}")
    print(f"    onaylanan  n={len(o):<4} ort={statistics.mean(o):.3f}")
    print(f"    reddedilen n={len(r):<4} ort={statistics.mean(r):.3f}")
    print(f"    ham fark={fark:+.3f}  |  Cohen's d={d:+.3f}")
    print(f"    yayilim: min={min(o + r):.2f} maks={max(o + r):.2f} "
          f"std={statistics.pstdev(o + r):.3f}")
    return d


def main() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    onay, red = _yer_gercegi(conn)
    conn.close()

    if len(onay) < 10 or len(red) < 10:
        print(f"Yeterli yer gerçeği yok (onay={len(onay)}, red={len(red)}).")
        return 1

    print("=== AYIRT ETME GUCU (yuksek = daha iyi siralama) ===")
    eski_d = _rapor("ESKI (v1)", eski_puanlama, onay, red)
    print()
    yeni_d = _rapor("YENI (v2)", calculate_relevance, onay, red)

    print()
    if yeni_d > eski_d:
        print(f"SONUC: v2 daha iyi ayiriyor (d {eski_d:+.3f} -> {yeni_d:+.3f}). Benimse.")
        return 0
    print(f"SONUC: v2 IYILESTIRMIYOR (d {eski_d:+.3f} -> {yeni_d:+.3f}). v1'de kal.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
