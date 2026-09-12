"""Reels havuzu, yayınlanmayan taslaklar yüzünden erimemeli.

Ölçüm (7 Ağustos 2026): 33 reels taslağı üretilmiş, HİÇBİRİ yayınlanmamış,
ama 120 haber `is_used=1` ile yakılmıştı — yakılan haberlerin %100'ü boşa
gitti. Aynı gün `get_published_for_reels`'e `is_used = 0` filtresi
eklenince bu, havuzu kalıcı olarak eriten bir sızıntıya dönüştü:
tüketim günde 10-40 haber, üretim günde 1-5.

Kural: haber ancak video GERÇEKTEN kullanıcıya ulaştığında yakılır.

ÖLÇÜ NOTU (8 Ağustos 2026): bu testler eskiden "yakıldı mı"yı
`get_published_for_reels` boyutuyla ölçüyordu. O vekil ölçü artık geçersiz:
aynı fonksiyon, bekleyen bir taslağın haberlerini de havuzdan çıkarıyor
(aksi hâlde aynı haberlerden ertesi gün ikinci bir reels üretiliyordu —
ölçümde 9 taslak yalnızca 4 farklı haber kümesi anlatıyordu).

İki durum çok farklı:
  * YAKMA  — kalıcı; `is_used = 1`; taslak reddedilse bile geri gelmez.
  * DIŞLAMA — geçici; taslak reddedilince haber havuza döner.

Bu yüzden testler artık doğrudan `is_used`'a bakıyor. Vekil değil, asıl şey.
"""

import pytest


def _yakilan(db) -> int:
    """Kalıcı olarak tüketilmiş haber sayısı."""
    with db._get_connection() as c:
        return c.execute(
            "SELECT COUNT(*) FROM news_items WHERE is_used = 1").fetchone()[0]


def _yayinlanmis_haber(db, i):
    news_id = db.add_news(title=f"English {i}", url=f"https://example.com/p{i}",
                          category="gaming")
    db.mark_news_processed(news_id, relevance_score=0.8)
    cid = db.add_content(news_id=news_id, content_type="post", caption="c",
                         summary_text=f"Türkçe özet {i}")
    db.add_publish_record(content_id=cid, post_type="post", status="success",
                          instagram_media_id=f"m{i}")
    return news_id


def test_draft_creation_does_not_burn_the_pool(tmp_db, monkeypatch):
    """Taslak üretmek havuzu tüketmemeli — taslak yayın demek değil."""
    from src.content_processor import ContentProcessor

    for i in range(3):
        _yayinlanmis_haber(tmp_db, i)

    p = ContentProcessor(db=tmp_db)
    p.client = object()
    monkeypatch.setattr(p, "_create_reels_script",
                        lambda items: {"caption": "c", "hashtags": [], "segments": []})

    assert p.create_reels_content() is not None

    assert _yakilan(tmp_db) == 0, "taslak üretimi havuzu yaktı"


def test_script_carries_the_ids_needed_to_burn_later(tmp_db, monkeypatch):
    from src.content_processor import ContentProcessor

    beklenen = [_yayinlanmis_haber(tmp_db, i) for i in range(3)]
    p = ContentProcessor(db=tmp_db)
    p.client = object()
    monkeypatch.setattr(p, "_create_reels_script",
                        lambda items: {"caption": "c", "hashtags": [], "segments": []})

    sonuc = p.create_reels_content()

    # KALICI satır denetleniyor, bellekteki dönüş değeri değil. İlk sürümde
    # `news_ids` add_content'ten SONRA ekleniyordu; bellekte doğru görünüyor
    # ama veritabanına hiç yazılmıyordu, dolayısıyla yakma hiç çalışmıyordu.
    # Bellekteki değeri denetleyen test bunu göremedi.
    kalici = tmp_db.get_content_by_id(sonuc["content_id"])["reels_script"]
    assert sorted(kalici["news_ids"]) == sorted(beklenen),         "news_ids veritabanına yazılmamış"


def test_pool_burns_only_when_the_video_reaches_the_user(tmp_db, monkeypatch):
    """ASIL KURAL: yakma, videonun Telegram'a ulaştığı anda."""
    from src.scheduler import Scheduler

    haberler = [_yayinlanmis_haber(tmp_db, i) for i in range(3)]
    cid = tmp_db.add_content(
        news_id=haberler[0], content_type="reels", caption="c",
        reels_script={"segments": ["a"], "news_ids": haberler})

    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    s.img_gen = s.story_gen = type("G", (), {
        "generate_post_image": lambda self, content_id: None,
        "generate_story_image": lambda self, content_id: None})()
    s.video_gen = type("V", (), {
        "generate_reels": lambda self, content_id: "/tmp/v.mp4"})()
    monkeypatch.setattr("src.scheduler.telegram_bot.send_reels_for_manual_publish",
                        lambda content, path: 4242)
    monkeypatch.setattr("src.scheduler.REELS_SILENT", True, raising=False)

    assert _yakilan(tmp_db) == 0
    s.generate_all_media()

    assert _yakilan(tmp_db) == 3, \
        "video gönderildiği hâlde havuz yakılmadı"


def test_pool_survives_a_failed_delivery(tmp_db, monkeypatch):
    """Telegram gönderimi başarısızsa haberler yanmamalı."""
    from src.scheduler import Scheduler

    haberler = [_yayinlanmis_haber(tmp_db, i) for i in range(3)]
    tmp_db.add_content(news_id=haberler[0], content_type="reels", caption="c",
                       reels_script={"segments": ["a"], "news_ids": haberler})

    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    s.img_gen = s.story_gen = type("G", (), {
        "generate_post_image": lambda self, content_id: None,
        "generate_story_image": lambda self, content_id: None})()
    s.video_gen = type("V", (), {
        "generate_reels": lambda self, content_id: "/tmp/v.mp4"})()
    monkeypatch.setattr("src.scheduler.telegram_bot.send_reels_for_manual_publish",
                        lambda content, path: None)  # gönderim başarısız
    monkeypatch.setattr("src.scheduler.REELS_SILENT", True, raising=False)

    s.generate_all_media()

    assert _yakilan(tmp_db) == 0, "gönderilemeyen video havuzu yaktı"


# =============================================
# Kapak görseli korunmalı
#
# `generate_reels` medya yolunu VE kapak görselini yazıyor. Scheduler hemen
# ardından `update_content_media(id, video_path)` çağırıyordu; o fonksiyonun
# `thumbnail_path` varsayılanı None olduğu için kapak her seferinde NULL'a
# çekiliyordu. Panelde her reels'in önizlemesi bu yüzden boştu.
# =============================================

def test_reels_cover_survives_media_generation(tmp_db, monkeypatch):
    from src.scheduler import Scheduler

    news_id = tmp_db.add_news(title="English", url="https://example.com/k",
                              category="gaming")
    cid = tmp_db.add_content(news_id=news_id, content_type="reels", caption="c",
                             reels_script={"segments": ["a"], "news_ids": [news_id]})

    def _uret(content_id):
        # Gerçek generate_reels'in yaptığı gibi: medya + kapak birlikte.
        tmp_db.update_content_media(content_id, "/tmp/v.mp4",
                                    thumbnail_path="/tmp/kapak.png")
        return "/tmp/v.mp4"

    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    s.img_gen = s.story_gen = type("G", (), {
        "generate_post_image": lambda self, content_id: None,
        "generate_story_image": lambda self, content_id: None})()
    s.video_gen = type("V", (), {"generate_reels": staticmethod(_uret)})()
    monkeypatch.setattr("src.scheduler.telegram_bot.send_reels_for_manual_publish",
                        lambda content, path: 1)
    monkeypatch.setattr("src.scheduler.REELS_SILENT", True, raising=False)

    s.generate_all_media()

    with tmp_db._get_connection() as conn:
        kapak = conn.execute(
            "SELECT thumbnail_path FROM processed_content WHERE id=?", (cid,)
        ).fetchone()["thumbnail_path"]
    assert kapak == "/tmp/kapak.png", "kapak görseli silinmiş"
