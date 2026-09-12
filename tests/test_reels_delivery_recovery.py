"""Videosu üretilmiş ama TESLİM EDİLMEMİŞ reels kaybolmamalı.

Reels için gönderim işareti `manual_video_prompt_message_id`; medya
üretim penceresi ise `needs_media=True` (yani `media_path IS NULL`) ile
seçiliyordu.

Aradaki boşluk: video üretildi, `media_path` yazıldı, ama Telegram
gönderimi başarısız oldu. O reels bir daha HİÇ seçilmiyordu —
`media_path` dolu olduğu için üretim penceresine girmiyor, onay
kuyruğuna da girmiyor (`REELS_SILENT` reels'i oradan ayıklıyor). Video
diskte duruyor, kullanıcı hiç görmüyor, hiçbir tur bunu düzeltmiyor.

Bu, 8 Ağustos 2026'da elle yeniden render edilen 4 reels'te fiilen
yaşandı: videolar hazırdı, hiçbiri kullanıcıya ulaşmıyordu.
"""

import pytest


def _yayinlanmis_haber(db, i):
    nid = db.add_news(title=f"English {i}", url=f"https://k/{i}",
                      source_name="IGN", category="gaming", description="a")
    db.mark_news_processed(nid, relevance_score=0.9)
    cid = db.add_content(news_id=nid, content_type="post", caption="c",
                         summary_text=f"Türkçe özet {i}")
    db.add_publish_record(content_id=cid, post_type="post", status="success",
                          instagram_media_id=f"m{i}")
    return nid


@pytest.fixture
def s(tmp_db, monkeypatch):
    from src.scheduler import Scheduler

    sch = Scheduler.__new__(Scheduler)
    sch.db = tmp_db
    sch.img_gen = sch.story_gen = type("G", (), {
        "generate_post_image": lambda self, content_id: None,
        "generate_story_image": lambda self, content_id: None})()
    sch.uretilen = []
    sch.video_gen = type("V", (), {
        "generate_reels": lambda _self, content_id: "/tmp/yeni.mp4"})()
    monkeypatch.setattr("src.scheduler.REELS_SILENT", True, raising=False)
    return sch


def _gonderimleri_yakala(monkeypatch, basarili=True):
    gonderilen = []

    def _sahte(content, path):
        gonderilen.append((content["id"], path))
        return 4242 if basarili else None

    monkeypatch.setattr(
        "src.scheduler.telegram_bot.send_reels_for_manual_publish", _sahte)
    return gonderilen


def test_rendered_but_undelivered_reels_is_sent(s, tmp_db, monkeypatch, tmp_path):
    """ASIL DURUM: video var, gönderim işareti yok — teslim edilmeli."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    haber = _yayinlanmis_haber(tmp_db, 1)
    cid = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                             reels_script={"segments": ["a"], "news_ids": [haber]})
    tmp_db.update_content_media(cid, media_path=str(video))

    gonderilen = _gonderimleri_yakala(monkeypatch)
    s.generate_all_media()

    assert gonderilen == [(cid, str(video))], "hazır video kullanıcıya ulaşmadı"


def test_existing_video_is_not_re_rendered(s, tmp_db, monkeypatch, tmp_path):
    """Teslim için yeniden render gerekmez — dakikalar süren boşuna iş."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    haber = _yayinlanmis_haber(tmp_db, 2)
    cid = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                             reels_script={"segments": ["a"], "news_ids": [haber]})
    tmp_db.update_content_media(cid, media_path=str(video))

    uretildi = []
    s.video_gen = type("V", (), {
        "generate_reels": lambda _s, content_id: uretildi.append(content_id)})()
    _gonderimleri_yakala(monkeypatch)

    s.generate_all_media()

    assert uretildi == [], "var olan video yeniden üretildi"


def test_missing_file_is_re_rendered(s, tmp_db, monkeypatch):
    """`media_path` dolu ama dosya silinmişse yeniden üretilmeli."""
    haber = _yayinlanmis_haber(tmp_db, 3)
    cid = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                             reels_script={"segments": ["a"], "news_ids": [haber]})
    tmp_db.update_content_media(cid, media_path="/yok/olan/dosya.mp4")

    gonderilen = _gonderimleri_yakala(monkeypatch)
    s.generate_all_media()

    assert gonderilen == [(cid, "/tmp/yeni.mp4")]


def test_already_delivered_reels_is_not_sent_again(s, tmp_db, monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    haber = _yayinlanmis_haber(tmp_db, 4)
    cid = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                             reels_script={"segments": ["a"], "news_ids": [haber]})
    tmp_db.update_content_media(cid, media_path=str(video))
    tmp_db.set_manual_video_prompt(cid, 999)

    gonderilen = _gonderimleri_yakala(monkeypatch)
    s.generate_all_media()

    assert gonderilen == [], "aynı reels ikinci kez gönderildi"


def test_failed_delivery_leaves_it_recoverable(s, tmp_db, monkeypatch, tmp_path):
    """
    Gönderim başarısızsa işaret KONULMAMALI ki bir sonraki tur yeniden
    denesin. Aksi hâlde içerik sessizce kaybolur.
    """
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    haber = _yayinlanmis_haber(tmp_db, 5)
    cid = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                             reels_script={"segments": ["a"], "news_ids": [haber]})
    tmp_db.update_content_media(cid, media_path=str(video))

    _gonderimleri_yakala(monkeypatch, basarili=False)
    s.generate_all_media()

    kayit = tmp_db.get_content_by_id(cid)
    assert kayit["manual_video_prompt_message_id"] is None

    # İkinci tur: bu kez gönderim başarılı.
    gonderilen = _gonderimleri_yakala(monkeypatch, basarili=True)
    s.generate_all_media()
    assert gonderilen == [(cid, str(video))], "başarısız teslim kurtarılamadı"


def test_delivered_reels_do_not_block_the_window(s, tmp_db, monkeypatch, tmp_path):
    """
    ELEME SQL'DE OLMALI, çağıran tarafta değil.

    `limit` sıralamanın ilk N'ini alır; eleme sonradan yapılırsa o N kayıt
    pencereyi işgal eder ve yerlerine kimse gelmez. İlk denememde tam olarak
    bu oldu: 4 reels'in ilk 3'ü teslim edilince dördüncüsü pencereye HİÇ
    giremedi, tur "0 reels" dedi ve hiçbir hata vermedi.

    Bu, projenin bilinen tekrarlayan hata sınıflarından biri.
    """
    from config import DAILY_REELS_LIMIT, MEDIA_GENERATION_MULTIPLIER

    pencere = DAILY_REELS_LIMIT * MEDIA_GENERATION_MULTIPLIER
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    # Pencereyi tam dolduracak kadar TESLİM EDİLMİŞ reels + 1 bekleyen.
    for i in range(pencere):
        haber = _yayinlanmis_haber(tmp_db, 100 + i)
        cid = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                                 reels_script={"segments": ["a"], "news_ids": [haber]})
        tmp_db.update_content_media(cid, media_path=str(video))
        tmp_db.set_manual_video_prompt(cid, 900 + i)

    haber = _yayinlanmis_haber(tmp_db, 200)
    bekleyen = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                                  reels_script={"segments": ["a"], "news_ids": [haber]})
    tmp_db.update_content_media(bekleyen, media_path=str(video))

    gonderilen = _gonderimleri_yakala(monkeypatch)
    s.generate_all_media()

    assert gonderilen == [(bekleyen, str(video))], \
        "teslim edilmiş reels'ler pencereyi tıkadı"


def test_pool_burns_only_after_successful_delivery(s, tmp_db, monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    haber = _yayinlanmis_haber(tmp_db, 6)
    cid = tmp_db.add_content(news_id=haber, content_type="reels", caption="c",
                             reels_script={"segments": ["a"], "news_ids": [haber]})
    tmp_db.update_content_media(cid, media_path=str(video))

    _gonderimleri_yakala(monkeypatch, basarili=False)
    s.generate_all_media()
    with tmp_db._get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM news_items WHERE is_used=1"
                         ).fetchone()[0] == 0

    _gonderimleri_yakala(monkeypatch, basarili=True)
    s.generate_all_media()
    with tmp_db._get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM news_items WHERE is_used=1"
                         ).fetchone()[0] == 1
