"""Aynı işin tekrar tekrar yapılmasını engelleyen kurallar.

7 günlük canlı veride ölçülen israf (7 Ağustos 2026):
  - 32 reels taslağı üretilmiş, 23'ü elle reddedilmişti; hepsi aynı
    yayınlanmış gönderileri yeniden anlatıyordu.
  - 5 günlük derlemenin 4'ünde carousel slaytları kaybolmuş, yerine tek
    düz görsel geçmişti.

İkisinin de sebebi aynı sınıftı: "bu iş zaten yapıldı" bilgisi yazılıyor
ama okunmuyordu.
"""

import json


def _yayinla(db, ozet, url):
    """Yayınlanmış bir haber kurar ve news_id'sini döndürür."""
    news_id = db.add_news(title="English", url=url, category="gaming")
    db.mark_news_processed(news_id, relevance_score=0.8)
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text=ozet)
    db.add_publish_record(content_id=cid, post_type="post", status="success",
                          instagram_media_id=f"m-{news_id}")
    return news_id


# =============================================
# Reels: anlatılan haber bir daha anlatılmaz
# =============================================

def test_used_news_is_not_offered_again(tmp_db):
    """
    create_reels_content her haberi mark_news_used ile işaretliyordu ama
    seçim sorgusu bu bayrağı hiç okumuyordu. Sonuç: her gün aynı gönderiler
    yeniden reels'e giriyordu.
    """
    a = _yayinla(tmp_db, "Birinci haber", "https://example.com/1")
    b = _yayinla(tmp_db, "İkinci haber", "https://example.com/2")

    assert len(tmp_db.get_published_for_reels(limit=10)) == 2

    tmp_db.mark_news_used(a)
    kalan = tmp_db.get_published_for_reels(limit=10)

    assert [r["id"] for r in kalan] == [b]


def test_reels_creation_records_which_news_it_covers(tmp_db, monkeypatch):
    """
    Senaryo kapsadığı haberlerin ID'lerini taşımalı — yakma işlemi ARTIK
    burada değil, video kullanıcıya ulaştığında yapılıyor.

    Bu test eskiden "üretim sonrası hepsi işaretlenmeli" diyordu; o kural
    yanlış çıktı. Ölçüm (7 Ağustos 2026): 33 reels taslağının hiçbiri
    yayınlanmamasına rağmen 120 haber yakılmıştı, yani yakılan haberlerin
    %100'ü boşa gitti ve havuz eridi. Bkz. tests/test_reels_pool.py.
    """
    from src.content_processor import ContentProcessor

    a = _yayinla(tmp_db, "Birinci haber", "https://example.com/1")
    b = _yayinla(tmp_db, "İkinci haber", "https://example.com/2")

    p = ContentProcessor(db=tmp_db)
    p.client = object()
    monkeypatch.setattr(p, "_create_reels_script",
                        lambda items: {"caption": "c", "hashtags": [], "segments": []})

    sonuc = p.create_reels_content()

    assert sonuc is not None
    # KALICI satır: alan add_content'ten SONRA eklenirse bellekte doğru
    # görünür ama veritabanına yazılmaz (ilk sürümde tam olarak bu oldu).
    kalici = tmp_db.get_content_by_id(sonuc["content_id"])["reels_script"]
    assert sorted(kalici["news_ids"]) == sorted([a, b])
    # Havuz YAKILMAMIŞ olmalı: taslak yayın demek değil.
    #
    # Ölçü doğrudan `is_used`'a bakıyor, havuz boyutuna değil. Bekleyen
    # taslağın haberleri havuzdan GEÇİCİ olarak çıkarılıyor (aynı haberden
    # ikinci reels üretilmesin diye) ama yakılmıyor: taslak reddedilirse
    # geri geliyorlar. Bkz. tests/test_reels_pending_exclusion.py
    with tmp_db._get_connection() as c:
        yakilan = c.execute(
            "SELECT COUNT(*) FROM news_items WHERE is_used = 1").fetchone()[0]
    assert yakilan == 0


# =============================================
# Medya: hazır olan yeniden üretilmez
# =============================================

def _scheduler(tmp_db):
    from src.scheduler import Scheduler
    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    return s


def test_finished_drafts_do_not_consume_the_window(tmp_db):
    """
    Eleme SQL'de olmalı, çağıran tarafta değil. Pencere `limit` kadar taslak
    çekip sonra medyalıları atlarsa, işi bitmiş taslaklar pencereyi doldurur
    ve iş bekleyenler hiç sıraya gelmez. Canlı ölçüm (7 Ağustos 2026):
    42'lik pencerenin 27'si zaten medyalıydı; medyasız 163 taslağın 148'i
    pencereye hiç girmiyordu.
    """
    news_id = tmp_db.add_news(title="English", url="https://example.com/p",
                              category="gaming", )
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    # Yüksek puanlı ve medyası hazır 3 taslak pencerenin başını tutuyor.
    for _ in range(3):
        cid = tmp_db.add_content(news_id=news_id, content_type="post",
                                 caption="c", summary_text="hazır")
        tmp_db.update_content_media(cid, "/tmp/hazir.png")
    dusuk = tmp_db.add_news(title="English 2", url="https://example.com/q",
                            category="gaming")
    tmp_db.mark_news_processed(dusuk, relevance_score=0.1)
    bekleyen = tmp_db.add_content(news_id=dusuk, content_type="post",
                                  caption="c", summary_text="bekliyor")

    pencere = tmp_db.get_draft_content(limit=3, order_by_relevance=True,
                                       needs_media=True)

    assert [c["id"] for c in pencere] == [bekleyen]


def test_media_generation_skips_content_that_already_has_media(tmp_db, monkeypatch):
    from src.scheduler import Scheduler

    s = _scheduler(tmp_db)
    news_id = tmp_db.add_news(title="English", url="https://example.com/m",
                              category="gaming")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    hazir = tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                               summary_text="Medyası hazır")
    tmp_db.update_content_media(hazir, "/tmp/hazir.png")
    bos = tmp_db.add_content(news_id=news_id, content_type="story",
                             summary_text="Medyası yok")

    uretilen = []

    class _Sahte:
        def generate_post_image(self, content_id):
            uretilen.append(content_id)
            return "/tmp/x.png"

        def generate_story_image(self, content_id):
            uretilen.append(content_id)
            return "/tmp/x.png"

    s.img_gen = s.story_gen = _Sahte()
    s.video_gen = _Sahte()
    monkeypatch.setattr(Scheduler, "_notify_quota_exhausted", lambda *a, **k: None,
                        raising=False)

    s.generate_all_media()

    assert bos in uretilen
    assert hazir not in uretilen


def test_roundup_carousel_survives_media_generation(tmp_db, monkeypatch):
    """
    Derlemenin `list_items` alanı yok, o yüzden yeniden üretilirse tekil
    görsel üretilip carousel_paths NULL'a çekiliyordu. Canlı veride 5
    derlemenin 4'ü böyle düzleşmişti — ve carousel_paths boşalınca derleme
    yeniden "derleme adayı" olup bir sonrakinin içine giriyordu.
    """
    from src.scheduler import Scheduler

    s = _scheduler(tmp_db)
    news_id = tmp_db.add_news(title="English", url="https://example.com/d",
                              category="gaming")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    derleme = tmp_db.add_content(news_id=news_id, content_type="post",
                                 caption="c", summary_text="Günün 6 Haberi")
    tmp_db.update_content_carousel(derleme, ["/tmp/s1.png", "/tmp/s2.png"])

    class _Sahte:
        def generate_post_image(self, content_id):
            tmp_db.update_content_media(content_id, "/tmp/duz.png")
            return "/tmp/duz.png"

        def generate_story_image(self, content_id):
            return None

    s.img_gen = s.story_gen = s.video_gen = _Sahte()
    monkeypatch.setattr(Scheduler, "_notify_quota_exhausted", lambda *a, **k: None,
                        raising=False)

    s.generate_all_media()

    with tmp_db._get_connection() as conn:
        satir = conn.execute(
            "SELECT carousel_paths FROM processed_content WHERE id = ?", (derleme,)
        ).fetchone()
    assert satir["carousel_paths"], "derleme slaytları silinmiş"
    assert len(json.loads(satir["carousel_paths"])) == 2
    # İkinci etki: carousel_paths boşalsaydı derleme yeniden "derleme adayı"
    # olurdu (get_roundup_candidates yalnızca carousel_paths dolu olanı eler)
    # ve bir sonraki derlemenin içine madde olarak girerdi.
    assert derleme not in [c["id"] for c in tmp_db.get_roundup_candidates(limit=10)]
