"""Panel sayaçları, saydıkları sütunla AYNI zaman evrenini kullanmalı.

Şemada iki zaman evreni var (bkz. src/database.py başındaki ZAMAN KURALI):

    YEREL                          UTC
    publish_history.published_at   news_items.collected_at

`get_stats` ikisini de YEREL Python tarihiyle karşılaştırıyordu.
`today_published` için bu doğru, `today_news` için değil: UTC+3'te gecenin
ilk üç saatinde yanlış günü sayar.

Ölçüm (8 Ağustos 2026): şu anki veride fark 0 — toplama sabah sabit bir
saatte çalıştığı için 1631 haberin hiçbiri UTC 21:00-24:00 aralığına
düşmüyor. Yani hata UYKUDAYDI; toplama saati değişse ya da gece elle bir
tur çalıştırılsa panel sessizce yanlış günü gösterecekti.
"""

import re

from src.database import Database


def test_today_news_counts_the_utc_day(tmp_db):
    """
    UTC gününe ait ama YEREL günü farklı olan bir haber sayılmalı.

    UTC 23:30'da toplanan bir haber, UTC+3'te yerel olarak ERTESİ gün
    görünür. Sayaç `collected_at` ile aynı evrende olmalı.
    """
    nid = tmp_db.add_news(title="Gece Haberi", url="https://k/gece",
                          source_name="IGN", category="ai", description="a")
    with tmp_db._get_connection() as c:
        # Bugünün UTC günü içinde ama gün sonuna çok yakın.
        c.execute("UPDATE news_items SET collected_at = "
                  "DATE('now') || ' 23:30:00' WHERE id = ?", (nid,))

    assert tmp_db.get_stats()["today_news"] == 1


def test_yesterday_news_is_not_counted(tmp_db):
    nid = tmp_db.add_news(title="Dünkü", url="https://k/dun",
                          source_name="IGN", category="ai", description="a")
    with tmp_db._get_connection() as c:
        c.execute("UPDATE news_items SET collected_at = "
                  "datetime('now', '-1 day') WHERE id = ?", (nid,))

    assert tmp_db.get_stats()["today_news"] == 0


def test_today_published_uses_the_local_day(tmp_db):
    """
    `published_at` bilerek YEREL yazılıyor; sayaç da yerel gün kullanmalı.
    İki sayacın farklı gün kaynakları kullanması bilinçli.
    """
    nid = tmp_db.add_news(title="H", url="https://k/y", source_name="IGN",
                          category="ai", description="a")
    cid = tmp_db.add_content(news_id=nid, content_type="post", caption="c")
    tmp_db.add_publish_record(content_id=cid, post_type="post",
                              status="success", instagram_media_id="m1")

    assert tmp_db.get_stats()["today_published"] == 1


def test_no_local_clock_against_collected_at():
    """
    Yapısal koruma: `collected_at`'e karşı Python'ın yerel tarihi
    kullanılmasın. Aynı hata bu projede birden fazla kez yazıldı.
    """
    import inspect

    kaynak = inspect.getsource(Database.get_stats)
    govde = "\n".join(s for s in kaynak.splitlines()
                      if not s.strip().startswith("#"))

    # collected_at karşılaştırmasının olduğu satır parametreli olmamalı.
    for satir in re.findall(r"DATE\(collected_at\)[^\n]*", govde):
        assert "?" not in satir, \
            f"collected_at yerel bir tarihle karşılaştırılıyor: {satir}"
