"""src.relevance ve puana göre seçim testleri.

Bağlam (2026-08-03 ölçümü): puan eskiden haber İŞLENİRKEN hesaplanıyordu,
seçim ise `collected_at DESC` ile tazeliğe göre yapılıyordu — yani puanın
hangi haberin işleneceğine hiç etkisi yoktu. 162 yüksek değerli haberin
%34'ü hiç işlenmeden atıldı. Simülasyon: puana göre seçim yakalama oranını
%16'dan %56'ya çıkarıyor.
"""

import pytest

from src.database import _apply_source_diversity
from src.relevance import calculate_relevance


def _news(**kw):
    base = {"title": "Sıradan bir başlık", "description": None,
            "image_url": None, "mention_count": 1}
    base.update(kw)
    return base


# =============================================
# Puanlama davranışı
# =============================================

def test_score_stays_in_range():
    """Kelime yığılmış bir başlık bile 1.0'ı aşmamalı."""
    t = "new first major record launch release reveal exclusive gpt-5 ps6 gta 6"
    s = calculate_relevance(_news(title=t, description="x" * 500, mention_count=9))
    assert 0.0 <= s <= 1.0


def test_more_keywords_scores_higher():
    """Ölçümdeki en güçlü sinyal kelime SAYISI (d=+1.388)."""
    az = calculate_relevance(_news(title="Bir oyun hakkında bir yazı"))
    cok = calculate_relevance(_news(title="New PS6 launch: first major reveal"))
    assert cok > az


def test_multi_source_scores_higher():
    tek = calculate_relevance(_news(mention_count=1))
    cok = calculate_relevance(_news(mention_count=6))
    assert cok > tek


def test_mention_bonus_is_capped():
    """Sinyal gerçek ama kelimelerden zayıf — sınırsız büyümemeli."""
    d4 = calculate_relevance(_news(mention_count=5))
    d9 = calculate_relevance(_news(mention_count=20))
    assert d9 == d4


def test_longer_description_scores_higher():
    """Eskiden ikili kontroldü (>50 karakter); artık kademeli."""
    kisa = calculate_relevance(_news(description="x" * 60))
    uzun = calculate_relevance(_news(description="x" * 300))
    assert uzun > kisa


def test_image_url_does_not_raise_score():
    """
    Ölçümde image_url TERS korelasyon gösterdi (d=-0.725): görselli haberler
    daha çok reddediliyor. Eskiden +0.05 BONUS veriliyordu, yani puanı yanlış
    yöne itiyordu. Artık puana hiç girmiyor.
    """
    gorselsiz = calculate_relevance(_news())
    gorselli = calculate_relevance(_news(image_url="https://example.com/a.jpg"))
    assert gorselli == gorselsiz


def test_announce_carries_no_weight():
    """
    "announce" 32 haberde geçiyordu ama ayırt etme gücü +0.022 — onaylanan
    ve reddedilen içerikte eşit sıklıkta. Sinyalsiz kelime, sinyalli olanların
    katkısını sulandırıyordu.
    """
    a = calculate_relevance(_news(title="Şirket bir şey announce etti"))
    b = calculate_relevance(_news(title="Şirket bir şey yaptı"))
    assert a == b


def test_ad_substring_no_longer_misfires():
    """
    "ad " alt dize eşleşmesiydi ve "read ", "ahead ", "instead " gibi masum
    kelimelerde tetikleniyordu.
    """
    s = calculate_relevance(_news(title="Read ahead: instead of waiting"))
    assert s >= calculate_relevance(_news(title="Sıradan bir başlık"))


def test_handles_missing_fields():
    assert 0.0 <= calculate_relevance({}) <= 1.0
    assert 0.0 <= calculate_relevance(_news(title=None, description=None)) <= 1.0


# =============================================
# Toplama anında puanlama + puana göre seçim
# =============================================

def test_score_is_stored_at_collection_time(tmp_db):
    """Puan artık işlenmeyi beklemeden, ekleme anında yazılmalı."""
    nid = tmp_db.add_news(
        title="New PS6 launch reveal", url="https://example.com/1",
        category="gaming", description="x" * 300,
    )
    with tmp_db._get_connection() as conn:
        skor = conn.execute(
            "SELECT relevance_score FROM news_items WHERE id = ?", (nid,)
        ).fetchone()["relevance_score"]
    assert skor > 0.5


def test_selection_prefers_high_score_over_recency(tmp_db):
    """
    Asıl regresyon: en yeni değil, en değerli haber seçilmeli.
    """
    tmp_db.add_news(title="Sıradan bir yazı", url="https://example.com/dusuk",
                    category="gaming")
    tmp_db.add_news(title="New PS6 launch: first major reveal",
                    url="https://example.com/yuksek", category="gaming",
                    description="x" * 300)
    # Düşük puanlı olanı DAHA YENİ yap — tazelik sıralaması onu öne alırdı.
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE news_items SET collected_at = datetime('now', '-1 hour') "
                     "WHERE url = 'https://example.com/yuksek'")

    ilk = tmp_db.get_unprocessed_news(limit=1)[0]
    assert ilk["url"] == "https://example.com/yuksek"


def test_recency_breaks_ties(tmp_db):
    """Tazelik atılmadı: eşit puanda yeni olan öne geçer."""
    tmp_db.add_news(title="Aynı başlık", url="https://example.com/eski",
                    category="gaming")
    tmp_db.add_news(title="Aynı başlık", url="https://example.com/yeni",
                    category="gaming")
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE news_items SET collected_at = datetime('now', '-10 hours') "
                     "WHERE url = 'https://example.com/eski'")

    assert tmp_db.get_unprocessed_news(limit=1)[0]["url"] == "https://example.com/yeni"


def test_stale_high_score_eventually_loses_to_fresh(tmp_db):
    """
    Sönüm olmasa bayat ama yüksek puanlı bir haber kuyruğu sonsuza kadar
    tıkardı.
    """
    tmp_db.add_news(title="New launch reveal first", url="https://example.com/bayat",
                    category="gaming", description="x" * 300)
    tmp_db.add_news(title="New launch reveal", url="https://example.com/taze",
                    category="gaming", description="x" * 300)
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE news_items SET collected_at = datetime('now', '-30 days') "
                     "WHERE url = 'https://example.com/bayat'")

    assert tmp_db.get_unprocessed_news(limit=1)[0]["url"] == "https://example.com/taze"


def test_mention_increment_rescores_unprocessed_news(tmp_db):
    """Çok kaynaklı doğrulama toplanıp da kullanılmazsa sinyal boşa gider."""
    nid = tmp_db.add_news(title="Bir haber", url="https://example.com/1",
                          category="ai")
    with tmp_db._get_connection() as conn:
        once = conn.execute("SELECT relevance_score s FROM news_items WHERE id=?",
                            (nid,)).fetchone()["s"]

    tmp_db.increment_mention_count(nid)
    tmp_db.increment_mention_count(nid)

    with tmp_db._get_connection() as conn:
        r = conn.execute("SELECT relevance_score s, mention_count m FROM news_items WHERE id=?",
                         (nid,)).fetchone()
    assert r["m"] == 3
    assert r["s"] > once


def test_mention_increment_does_not_rescore_processed_news(tmp_db):
    """İşlenmiş haberin puanı üretilmiş içeriğin kaydı — sıralama girdisi değil."""
    nid = tmp_db.add_news(title="Bir haber", url="https://example.com/1", category="ai")
    tmp_db.mark_news_processed(nid, relevance_score=0.42)

    tmp_db.increment_mention_count(nid)

    with tmp_db._get_connection() as conn:
        r = conn.execute("SELECT relevance_score s, mention_count m FROM news_items WHERE id=?",
                         (nid,)).fetchone()
    assert r["s"] == 0.42
    assert r["m"] == 2


def test_increment_on_missing_id_is_safe(tmp_db):
    tmp_db.increment_mention_count(99999)  # patlamamalı


# =============================================
# Kaynak çeşitliliği
# =============================================

def _rows(*sources):
    return [{"source_name": s, "id": i} for i, s in enumerate(sources)]


def test_single_source_cannot_take_whole_batch():
    """
    Asıl regresyon: seçim puana çevrildiğinde bir turluk ilk 30 haberin 16'sı
    tek kaynaktan geliyordu. Puan sıralaması açlığı çözmemiş, taşımıştı.
    """
    rows = _rows(*(["Tekel"] * 20 + ["A", "B", "C", "D", "E", "F"]))
    secim = _apply_source_diversity(rows, limit=8)

    tekel = sum(1 for r in secim if r["source_name"] == "Tekel")
    assert len(secim) == 8
    assert tekel <= 2  # limit*0.25 = 2


def test_priority_order_is_preserved():
    """Çeşitlilik sırayı bozmamalı — yalnızca eleme yapmalı."""
    rows = _rows("A", "A", "A", "B", "C")
    secim = _apply_source_diversity(rows, limit=5)
    assert [r["id"] for r in secim] == sorted(r["id"] for r in secim)


def test_batch_is_filled_even_if_only_one_source_exists():
    """
    Çeşitlilik uğruna partiyi eksik döndürmek işi boşa harcamak olurdu:
    kuyrukta gerçekten tek kaynak varsa slotlar yine dolmalı.
    """
    rows = _rows(*(["Tek"] * 10))
    secim = _apply_source_diversity(rows, limit=6)
    assert len(secim) == 6


def test_diverse_queue_is_untouched():
    """Zaten çeşitli bir kuyrukta sınır hiçbir şeyi değiştirmemeli."""
    rows = _rows("A", "B", "C", "D", "E")
    assert _apply_source_diversity(rows, limit=5) == rows


def test_missing_source_name_is_grouped_not_crashed():
    rows = [{"source_name": None, "id": i} for i in range(6)]
    secim = _apply_source_diversity(rows, limit=4)
    assert len(secim) == 4  # doldurma devreye girer


def test_empty_input():
    assert _apply_source_diversity([], limit=5) == []
    assert _apply_source_diversity(_rows("A"), limit=0) == []


def test_diversity_applies_through_the_query(tmp_db):
    """
    Uçtan uca: tekel kaynak yüksek puan alsa bile partiyi ele geçirmemeli.

    Tekel'in başlıkları kelime yüklü (yüksek puan), diğerleri sade — saf puan
    sıralamasında ilk 8'in tamamını Tekel alırdı.
    """
    for i in range(12):
        tmp_db.add_news(title=f"New launch reveal {i}", url=f"https://example.com/t{i}",
                        category="ai", source_name="Tekel", description="x" * 300)
    for i in range(10):
        tmp_db.add_news(title=f"Sıradan haber {i}", url=f"https://example.com/d{i}",
                        category="ai", source_name=f"Kaynak{i}")

    secim = tmp_db.get_unprocessed_news(limit=8)
    tekel = sum(1 for r in secim if r["source_name"] == "Tekel")
    assert len(secim) == 8
    assert tekel <= 2
    assert len({r["source_name"] for r in secim}) >= 6


# =============================================
# Ölçümün yer gerçeği kirlenmemeli
#
# scripts/measure_relevance.py puanlamanın ayırt etme gücünü KULLANICININ
# kararlarına karşı ölçüyor (7 Ağustos 2026 ölçümü: Cohen's d = +1.365).
# Ölçüm ancak "reddedildi" gerçekten kullanıcının yargısıysa anlamlı.
#
# İki mekanizma da içeriği 'rejected' yapıyor ama ikisi de yargı değil:
# bayat taslak emekliliği (kapasite sinyali) ve derlemeye dahil edilme.
# Bunlar sayıca kullanıcı kararlarını ezecek kadar çok — 466 taslaklık
# kuyrukta 257'si tek bir gecede emekliye ayrılacaktı.
# =============================================

def _karar_verilen_sayisi(db) -> tuple[int, int]:
    """measure_relevance._yer_gercegi ile AYNI sorgu — (onay, red)."""
    with db._get_connection() as conn:
        rows = conn.execute("""
            SELECT pc.status FROM processed_content pc
            JOIN news_items n ON pc.news_id = n.id
            WHERE pc.status IN ('published', 'approved', 'rejected')
              AND pc.expired_at IS NULL
              AND COALESCE(pc.used_in_roundup, 0) = 0
        """).fetchall()
    onay = sum(1 for r in rows if r["status"] in ("published", "approved"))
    return onay, len(rows) - onay


def _icerik(db, i, status="draft"):
    news_id = db.add_news(title=f"English {i}", url=f"https://example.com/g{i}",
                          category="gaming")
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text="özet")
    if status != "draft":
        db.update_content_status(cid, status)
    return cid


def test_expired_drafts_do_not_pollute_ground_truth(tmp_db):
    _icerik(tmp_db, 1, "published")
    _icerik(tmp_db, 2, "rejected")   # gerçek kullanıcı reddi
    bayat = _icerik(tmp_db, 3)
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE processed_content SET created_at = datetime('now','-9 days') "
                     "WHERE id = ?", (bayat,))
    tmp_db.expire_stale_drafts(days=3)

    assert _karar_verilen_sayisi(tmp_db) == (1, 1)


def test_roundup_members_do_not_pollute_ground_truth(tmp_db):
    _icerik(tmp_db, 4, "published")
    derlemeye_giren = _icerik(tmp_db, 5, "rejected")
    tmp_db.mark_used_in_roundup([derlemeye_giren])

    assert _karar_verilen_sayisi(tmp_db) == (1, 0)
