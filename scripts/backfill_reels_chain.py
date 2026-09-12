"""Eski reels senaryolarına görsel zinciri alanlarını geri doldur.

SORUN
-----
Slayt üretimi arka plan zincirini senaryodan okuyor: `image_urls`,
`news_urls`, `game_titles`. Zincirin og:image ve Steam kapağı basamakları
son ikisine bağlı.

Bu alanlar senaryoya sonradan eklendi. Daha önce yazılmış senaryolarda yok,
dolayısıyla o reels'ler yeniden render edilse bile zincirin 3. ve 4.
basamakları hiç çalışamıyor ve doğrudan jenerik stok fotoğrafa düşüyorlar.

Ölçüm (8 Ağustos 2026): bildirim penceresindeki 9 bekleyen reels'in
9'unda da `news_urls` boştu. Aynı haberler için zincir tek tek sınandığında
görselsiz 8 haberin 8'inde og:image bulunuyordu — yani kayıp tamamen
senaryodaki eksik alandan kaynaklanıyordu, kaynak sitelerden değil.

EŞLEME
------
Eski senaryolarda `news_ids` de yok. `news_titles` alanı ham haber başlığı
değil, `get_published_for_reels`ten gelen TÜRKÇE ÖZET — yani
`processed_content.summary_text`. Bu yüzden eşleme özet üzerinden
yayınlanmış içeriğe, oradan `news_id` ile habere gidiyor.

KULLANIM
--------
    python scripts/backfill_reels_chain.py            # kuru çalışma
    python scripts/backfill_reels_chain.py --uygula   # yazar

Alanları doldurmak yetmez; ETKİ ETMESİ için ilgili reels'lerin videosu
yeniden üretilmelidir.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH  # noqa: E402


def _haberi_bul(conn: sqlite3.Connection, haber_id: int | None,
                ozet: str | None) -> sqlite3.Row | None:
    """Önce id ile, olmazsa Türkçe özet üzerinden habere ulaş."""
    if haber_id:
        return conn.execute(
            "SELECT id, title, url, image_url FROM news_items WHERE id = ?",
            (haber_id,),
        ).fetchone()
    if not ozet:
        return None
    return conn.execute(
        """SELECT ni.id, ni.title, ni.url, ni.image_url
           FROM processed_content pc
           JOIN news_items ni ON ni.id = pc.news_id
           WHERE pc.summary_text = ?
           ORDER BY pc.id DESC LIMIT 1""",
        (ozet,),
    ).fetchone()


def geri_doldur(db_path: str, uygula: bool = False, gun: int = 3) -> dict:
    """Bekleyen reels senaryolarına zincir alanlarını yazar.

    `gun` penceresi bilinçli: Telegram bildirim kuyruğuna zaten girmeyecek
    kadar eski taslakları düzeltmenin bir faydası yok.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rapor = {"incelenen": 0, "guncellenen": 0, "atlanan": 0, "eslesen_haber": 0}

    rows = conn.execute(
        """SELECT id, reels_script FROM processed_content
           WHERE content_type = 'reels' AND status IN ('draft', 'approved')
             AND telegram_message_id IS NULL
             AND created_at >= datetime('now', ?)
           ORDER BY created_at DESC""",
        (f"-{gun} days",),
    ).fetchall()

    for r in rows:
        rapor["incelenen"] += 1
        try:
            senaryo = json.loads(r["reels_script"] or "{}")
        except ValueError:
            rapor["atlanan"] += 1
            continue

        haber_idler = senaryo.get("news_ids") or []
        ozetler = senaryo.get("news_titles") or []
        adet = max(len(haber_idler), len(ozetler))
        if not adet:
            rapor["atlanan"] += 1
            continue

        kayitlar = [
            _haberi_bul(
                conn,
                haber_idler[i] if i < len(haber_idler) else None,
                ozetler[i] if i < len(ozetler) else None,
            )
            for i in range(adet)
        ]
        if not any(kayitlar):
            rapor["atlanan"] += 1
            continue
        rapor["eslesen_haber"] += sum(1 for k in kayitlar if k)

        senaryo["image_urls"] = [(k["image_url"] if k else None) for k in kayitlar]
        senaryo["news_urls"] = [(k["url"] if k else None) for k in kayitlar]
        # Steam kapağı HAM kaynak başlığıyla aranır, Türkçe özetle değil.
        senaryo["game_titles"] = [(k["title"] if k else None) for k in kayitlar]
        # `news_ids` yalnızca izleme için değil: `get_published_for_reels`
        # bekleyen taslakların haberlerini bununla dışlıyor. Alan olmadan
        # aynı haberler ertesi gün yeniden seçilebiliyor — ölçümde 9 taslak
        # yalnızca 4 farklı haber kümesi anlatıyordu.
        senaryo["news_ids"] = [(k["id"] if k else None) for k in kayitlar]

        if uygula:
            conn.execute(
                "UPDATE processed_content SET reels_script = ? WHERE id = ?",
                (json.dumps(senaryo, ensure_ascii=False), r["id"]),
            )
        rapor["guncellenen"] += 1

    if uygula:
        conn.commit()
    conn.close()
    return rapor


def main() -> int:
    ayristirici = argparse.ArgumentParser(description=__doc__)
    ayristirici.add_argument("--uygula", action="store_true",
                             help="değişiklikleri yaz (varsayılan: kuru çalışma)")
    ayristirici.add_argument("--gun", type=int, default=3,
                             help="kaç günlük taslak taranacak")
    a = ayristirici.parse_args()

    rapor = geri_doldur(str(DB_PATH), uygula=a.uygula, gun=a.gun)
    baslik = "UYGULANDI" if a.uygula else "KURU ÇALIŞMA (hiçbir şey yazılmadı)"
    print(baslik)
    print(f"  incelenen reels : {rapor['incelenen']}")
    print(f"  güncellenen     : {rapor['guncellenen']}")
    print(f"  atlanan         : {rapor['atlanan']}")
    print(f"  eşleşen haber   : {rapor['eslesen_haber']}")
    if a.uygula:
        print("\nAlanları doldurmak yetmez — ilgili reels'lerin videosu "
              "yeniden üretilmelidir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
