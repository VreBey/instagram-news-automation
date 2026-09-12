"""
Scheduler.auto_schedule_content() için birim testleri.
Gerçek Instagram/Cloudinary/Gemini/RSS ağ çağrısı yapılmaz — Scheduler,
tüm alt bileşenleriyle birlikte izole bir tmp_db üzerinde kurulur.
"""

import uuid
from datetime import datetime, timedelta

import schedule as schedule_lib

from config import DAILY_POST_LIMIT, DAILY_REELS_LIMIT, SCHEDULE
from src.scheduler import Scheduler


def _make_ready_content(db, title, url, relevance_score, content_type="post"):
    """Auto-schedule için gerekli tüm koşulları taşıyan bir içerik oluştur."""
    news_id = db.add_news(title=title, url=url, category="ai")
    db.mark_news_processed(news_id, relevance_score=relevance_score)
    content_id = db.add_content(news_id=news_id, content_type=content_type, caption="test")
    db.update_content_media(content_id, media_path=f"/fake/{content_type}_{news_id}.png")
    return content_id


def _scheduled_rows(db):
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT content_id, source, post_type FROM scheduled_posts"
        ).fetchall()
    return [dict(row) for row in rows]


def _make_reels_draft(db, delivered=False):
    news_id = db.add_news(
        title=f"Reels Haberi {uuid.uuid4()}", url=f"https://example.com/{uuid.uuid4()}", category="ai"
    )
    db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = db.add_content(
        news_id=news_id, content_type="reels",
        reels_script={"news_ids": [news_id]}, caption="c"
    )
    if delivered:
        db.set_manual_video_prompt(content_id, 123)
    return content_id


def test_process_content_creates_reels_regardless_of_post_story_backlog(tmp_db, mocker):
    """Gerçek olay (21 Ağustos 2026): reels üretimi eskiden post/story onay
    kuyruğuyla AYNI frene bağlıydı ve kuyruk 5+ gündür tavanın altına
    inmediği için reels 13 gün boyunca hiç üretilmedi -- oysa reels hiç
    onay gerektirmiyor (REELS_SILENT), kullanıcının inceleme yüküne hiç
    eklenmiyor. Artık post/story kuyruğunun durumundan TAMAMEN bağımsız."""
    scheduler = Scheduler(db=tmp_db)
    mocker.patch.object(scheduler.processor, "process_all_news", return_value={"processed": 0})
    reels_mock = mocker.patch.object(scheduler.processor, "create_reels_content", return_value={"id": 1})

    scheduler.process_content()

    assert reels_mock.call_count == 2  # "ai" ve "gaming" kategorileri


def test_process_content_skips_reels_when_daily_undelivered_limit_reached(tmp_db, mocker):
    """Aşırı üretimi önleyen kendi ölçütü: teslim edilmemiş (Telegram'a
    gönderilmemiş) reels taslağı zaten günlük limit kadar varsa yenisi
    üretilmemeli -- aksi halde günde 3 tur x 2 kategori Gemini senaryo
    çağrısı hiç teslim edilmeden birikirdi."""
    for _ in range(DAILY_REELS_LIMIT):
        _make_reels_draft(tmp_db, delivered=False)

    scheduler = Scheduler(db=tmp_db)
    mocker.patch.object(scheduler.processor, "process_all_news", return_value={"processed": 0})
    reels_mock = mocker.patch.object(scheduler.processor, "create_reels_content")

    scheduler.process_content()

    reels_mock.assert_not_called()


def test_process_content_creates_reels_when_below_daily_undelivered_limit(tmp_db, mocker):
    """Zaten TESLİM EDİLMİŞ reels taslakları (manual_video_prompt_message_id
    dolu) sayaca dahil edilmemeli -- yalnızca henüz gönderilmemiş olanlar
    aşırı üretim ölçüsüne girer."""
    for _ in range(DAILY_REELS_LIMIT):
        _make_reels_draft(tmp_db, delivered=True)

    scheduler = Scheduler(db=tmp_db)
    mocker.patch.object(scheduler.processor, "process_all_news", return_value={"processed": 0})
    reels_mock = mocker.patch.object(scheduler.processor, "create_reels_content", return_value={"id": 1})

    scheduler.process_content()

    assert reels_mock.call_count == 2


def test_auto_schedule_content_respects_threshold_and_daily_limit(tmp_db):
    high1 = _make_ready_content(tmp_db, "Yüksek Skor 1", "https://example.com/h1", 0.90)
    high2 = _make_ready_content(tmp_db, "Yüksek Skor 2", "https://example.com/h2", 0.85)
    high3 = _make_ready_content(tmp_db, "Yüksek Skor 3 (kota dışı kalmalı)", "https://example.com/h3", 0.80)
    low = _make_ready_content(tmp_db, "Düşük Skor", "https://example.com/low", 0.50)

    scheduler = Scheduler(db=tmp_db)
    stats = scheduler.auto_schedule_content()

    assert stats["post"] == DAILY_POST_LIMIT

    # Not: get_scheduled_count_today() burada kasıtlı olarak kontrol edilmiyor —
    # slotlardan biri (ör. "morning_post" 10:00) testin çalıştığı ana göre günün
    # ilerleyen saatinde zaten geçmiş olabilir ve _next_slot_datetime() o öğeyi
    # yarına kaydırır. Bu, saatten bağımsız olması gereken bir teste saat-bağımlı
    # bir kırılganlık sokar. Aşağıdaki content_id bazlı kontroller zaten hangi
    # içeriklerin zamanlandığını (hangi güne düştüğünden bağımsız) doğruluyor.
    rows = _scheduled_rows(tmp_db)
    scheduled_ids = {row["content_id"] for row in rows}
    assert high1 in scheduled_ids
    assert high2 in scheduled_ids
    assert high3 not in scheduled_ids  # günlük limit dolduğu için kotada kaldı
    assert low not in scheduled_ids  # eşiği geçemedi

    assert all(row["source"] == "auto" for row in rows)

    assert tmp_db.get_content_by_id(high1)["status"] == "approved"
    assert tmp_db.get_content_by_id(low)["status"] == "draft"


def test_auto_schedule_content_skips_items_without_media(tmp_db):
    news_id = tmp_db.add_news(title="Medyasız Haber", url="https://example.com/no-media", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.95)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="test")
    # media_path kasıtlı olarak set edilmedi (medya üretimi henüz tamamlanmamış gibi)

    scheduler = Scheduler(db=tmp_db)
    stats = scheduler.auto_schedule_content()

    assert stats["post"] == 0
    assert tmp_db.get_content_by_id(content_id)["status"] == "draft"


def test_auto_schedule_content_noop_when_no_drafts(tmp_db):
    scheduler = Scheduler(db=tmp_db)
    stats = scheduler.auto_schedule_content()
    assert stats["total"] == 0


def test_auto_schedule_never_reuses_a_taken_slot(tmp_db):
    """
    ART ARDA ÇAĞRILAR AYNI SLOTA ÇAKIŞMAMALI.

    Eski kod slotu SAYIYLA seçiyordu: `slot_keys[i % len(slot_keys)]`.
    `i` her çağrıda sıfırdan başladığı için ikinci çağrı da ilk slotu
    seçiyordu — slot zaten dolu olsa bile. Gerçek olay buydu: art arda iki
    onay AYNI yarınki slota çakıştı ve bugünün hâlâ boş olan gece slotu hiç
    kullanılmadı.

    `compute_next_schedule_time` bu hatayı düzeltmek için yazılmıştı ama
    HİÇBİR ÜRETİM KODU ONU ÇAĞIRMIYORDU — düzeltme yalnızca kendi birim
    testlerinde yaşıyordu (tests/test_schedule_utils.py). Bu test, gerçek
    zamanlama yolunun onu kullandığını doğruluyor.
    """
    _make_ready_content(tmp_db, "Birinci", "https://example.com/s1", 0.95)
    Scheduler(db=tmp_db).auto_schedule_content()

    _make_ready_content(tmp_db, "İkinci", "https://example.com/s2", 0.94)
    Scheduler(db=tmp_db).auto_schedule_content()

    with tmp_db._get_connection() as conn:
        zamanlar = [r[0] for r in conn.execute(
            "SELECT scheduled_time FROM scheduled_posts ORDER BY scheduled_time")]

    assert len(zamanlar) == 2, f"iki içerik zamanlanmalıydı: {zamanlar}"
    assert zamanlar[0] != zamanlar[1], \
        f"iki içerik AYNI slota zamanlandı: {zamanlar}"


def test_auto_schedule_ineligible_items_do_not_eat_the_window(tmp_db):
    """
    ELEME SQL'DE OLMALI. Eskiden `limit` kadar taslak çekilip medyasızlar ve
    eşiği geçemeyenler Python'da atılıyordu; pencere uygun OLMAYAN kayıtlarla
    dolunca gerçekten uygun içerik hiç sıraya gelmiyordu.

    Burada uygun olmayanlar daha YÜKSEK puanlı, yani sıralamada önde —
    eleme limitten sonra yapılsaydı uygun içerik hiç zamanlanmazdı.
    """
    for i in range(DAILY_POST_LIMIT):
        nid = tmp_db.add_news(title=f"Medyasız {i}",
                              url=f"https://example.com/nm{i}", category="ai")
        tmp_db.mark_news_processed(nid, relevance_score=0.99)
        tmp_db.add_content(news_id=nid, content_type="post", caption="c")

    uygun = _make_ready_content(tmp_db, "Uygun", "https://example.com/ok", 0.90)

    Scheduler(db=tmp_db).auto_schedule_content()

    assert uygun in {r["content_id"] for r in _scheduled_rows(tmp_db)}, \
        "uygun olmayan kayıtlar pencereyi yedi"


def _make_scheduled_post(db, scheduled_time=None):
    # get_pending_scheduled() sadece scheduled_time <= şu an olan öğeleri
    # seçtiği için, testin çalıştırıldığı gerçek saatten bağımsız olması adına
    # sabit bir tarih yerine "az önce" bir zaman kullanılıyor.
    scheduled_time = scheduled_time or (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    content_id = _make_ready_content(db, "Zamanlanmış Haber", "https://example.com/scheduled", 0.9)
    schedule_id = db.schedule_post(content_id=content_id, scheduled_time=scheduled_time, post_type="post")
    return schedule_id, content_id


def _schedule_status(db, schedule_id):
    with db._get_connection() as conn:
        row = conn.execute("SELECT status FROM scheduled_posts WHERE id = ?", (schedule_id,)).fetchone()
    return row["status"]


def test_publish_scheduled_retries_recording_a_successful_publish(tmp_db, mocker, monkeypatch):
    """
    Gerçek olay: Instagram'a başarıyla yayınlanan bir gönderi, hemen ardından
    gelen "database is locked" hatası yüzünden veritabanına hiç 'published'
    olarak yazılamıyor, sistem onu yanlışlıkla 'failed' işaretliyordu -- bu da
    bir sonraki kontrolde AYNI içeriğin tekrar paylaşılma riskini doğuruyordu.
    Bu test, kayıt adımı ilk denemede geçici olarak patlasa bile (ör. kilit
    hatası) sonunda 'published' olarak doğru şekilde kaydedildiğini ve asla
    yanlışlıkla 'failed' işaretlenmediğini doğrular.
    """
    schedule_id, content_id = _make_scheduled_post(tmp_db)

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(scheduler.ig_client, "upload_media_to_host", lambda path: "https://cdn.example.com/post.png")
    monkeypatch.setattr(
        scheduler.ig_client, "publish_post",
        lambda **kwargs: {"success": True, "media_id": "IG12345", "permalink": "https://instagram.com/p/x"}
    )
    monkeypatch.setattr(scheduler, "_record_publish_result", mocker.Mock(wraps=scheduler._record_publish_result))
    mocker.patch("src.scheduler.time.sleep")

    real_add_publish_record = tmp_db.add_publish_record
    call_count = {"n": 0}

    def flaky_add_publish_record(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("database is locked")
        return real_add_publish_record(*args, **kwargs)

    monkeypatch.setattr(tmp_db, "add_publish_record", flaky_add_publish_record)

    scheduler.publish_scheduled()

    assert _schedule_status(tmp_db, schedule_id) == "published"
    assert call_count["n"] == 2  # ilk deneme patladı, ikinci denemede başarılı oldu
    history = tmp_db.get_publish_history()
    assert any(h["instagram_media_id"] == "IG12345" and h["status"] == "success" for h in history)


def test_publish_scheduled_logs_critical_when_recording_permanently_fails(tmp_db, mocker, monkeypatch, caplog):
    """
    Kayıt adımı TÜM denemelerde başarısız olursa (kalıcı kilit), gerçekten
    yayınlanmış bir gönderi sessizce 'failed' işaretlenmemeli -- bu, bir
    sonraki kontrolde mükerrer paylaşıma yol açabilir. Bunun yerine kritik bir
    log ile açıkça işaretlenmeli ki manuel müdahale edilebilsin.
    """
    schedule_id, content_id = _make_scheduled_post(tmp_db)

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(scheduler.ig_client, "upload_media_to_host", lambda path: "https://cdn.example.com/post.png")
    monkeypatch.setattr(
        scheduler.ig_client, "publish_post",
        lambda **kwargs: {"success": True, "media_id": "IG99999", "permalink": "https://instagram.com/p/y"}
    )
    monkeypatch.setattr(tmp_db, "update_schedule_status", mocker.Mock(side_effect=Exception("database is locked")))
    mocker.patch("src.scheduler.time.sleep")

    with caplog.at_level("CRITICAL"):
        scheduler.publish_scheduled()

    assert _schedule_status(tmp_db, schedule_id) == "pending"  # asla sessizce 'failed' yazılmadı
    assert any("IG99999" in message and "KRİTİK" in message for message in caplog.messages)


def test_publish_scheduled_sends_manual_fallback_when_post_fails(tmp_db, mocker, monkeypatch):
    """Gerçek olay (21 Ağustos 2026): Meta feed post yayınını API'den kalıcı
    olarak engellemeye başladı (code 4 / subcode 2207051). Otomatik yeniden
    deneme olmadığından, onaylanan içerik kullanıcı Instagram'dan ELLE
    paylaşana kadar hiçbir yere gitmiyordu. Post başarısız olduğunda elle
    paylaşım paketi (görsel+caption+onay butonu) gönderilmeli."""
    schedule_id, content_id = _make_scheduled_post(tmp_db)

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(scheduler.ig_client, "upload_media_to_host", lambda path: "https://cdn.example.com/post.png")
    monkeypatch.setattr(
        scheduler.ig_client, "publish_post",
        lambda **kwargs: {"success": False, "error": "Application request limit reached"}
    )
    fallback_mock = mocker.patch(
        "src.scheduler.telegram_bot.send_manual_publish_fallback", return_value=777
    )
    mocker.patch("src.scheduler.telegram_bot._send_text")
    mocker.patch("src.scheduler.time.sleep")

    scheduler.publish_scheduled()

    fallback_mock.assert_called_once()
    assert fallback_mock.call_args[0][0]["id"] == content_id
    assert tmp_db.get_content_by_id(content_id)["telegram_message_id"] == 777


def test_publish_scheduled_sends_manual_fallback_when_story_fails(tmp_db, mocker, monkeypatch):
    """Gerçek olay (4 Eylül 2026): Meta'nın engeli 23 Ağustos'ta GENİŞLEDİ —
    artık container oluşturmanın ilk adımında "API access blocked" (code
    200) alınıyor ve story da etkileniyor (23 Ağustos'tan beri tek bir
    story bile yayınlanamadı). Elle paylaşım paketi artık story
    başarısızlığında da gönderilmeli — reels hariç (kendi elle-paylaşım
    yolu zaten var)."""
    schedule_id, story_content_id, _ = _make_story_with_sibling_post(tmp_db, sibling_status=None)

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(scheduler.ig_client, "upload_media_to_host", lambda path: "https://cdn.example.com/story.png")
    monkeypatch.setattr(
        scheduler.ig_client, "publish_story",
        lambda **kwargs: {"success": False, "error": "API access blocked."}
    )
    mocker.patch.object(scheduler.story_gen, "generate_story_image", return_value=None)
    fallback_mock = mocker.patch(
        "src.scheduler.telegram_bot.send_manual_publish_fallback", return_value=888
    )
    mocker.patch("src.scheduler.telegram_bot._send_text")
    mocker.patch("src.scheduler.time.sleep")

    scheduler.publish_scheduled()

    fallback_mock.assert_called_once()
    assert fallback_mock.call_args[0][0]["id"] == story_content_id
    assert tmp_db.get_content_by_id(story_content_id)["telegram_message_id"] == 888


def test_publish_scheduled_does_not_send_manual_fallback_when_reels_fails(tmp_db, mocker, monkeypatch):
    """Reels'in kendi elle-paylaşım yolu zaten var (send_reels_for_manual_
    publish, bkz. video_generator/scheduler.generate_all_media) — post/story
    fallback'i reels için tetiklenmemeli, mükerrer gönderim olur."""
    news_id = tmp_db.add_news(title="Reels Haberi", url="https://example.com/reels-fail", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(news_id=news_id, content_type="reels", caption="c")
    tmp_db.update_content_media(content_id, media_path="/fake/reels.mp4")
    scheduled_time = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    tmp_db.schedule_post(content_id=content_id, scheduled_time=scheduled_time, post_type="reels")

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(scheduler.ig_client, "upload_media_to_host", lambda path: "https://cdn.example.com/reels.mp4")
    monkeypatch.setattr(
        scheduler.ig_client, "publish_reels",
        lambda **kwargs: {"success": False, "error": "API access blocked."}
    )
    fallback_mock = mocker.patch("src.scheduler.telegram_bot.send_manual_publish_fallback")
    mocker.patch("src.scheduler.telegram_bot._send_text")
    mocker.patch("src.scheduler.time.sleep")

    scheduler.publish_scheduled()

    fallback_mock.assert_not_called()


def _make_story_with_sibling_post(db, sibling_status: str | None):
    """Aynı habere ait hem 'post' hem 'story' içeriği olan bir çift oluşturur."""
    news_id = db.add_news(
        title="Ortak Haber", url=f"https://example.com/sibling-{uuid.uuid4()}", category="ai"
    )
    db.mark_news_processed(news_id, relevance_score=0.9)

    post_content_id = db.add_content(news_id=news_id, content_type="post", caption="post caption")
    if sibling_status:
        db.update_content_status(post_content_id, sibling_status)

    story_content_id = db.add_content(news_id=news_id, content_type="story", summary_text="story text")
    db.update_content_media(story_content_id, media_path="/fake/story.png")

    scheduled_time = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    schedule_id = db.schedule_post(content_id=story_content_id, scheduled_time=scheduled_time, post_type="story")
    return schedule_id, story_content_id, post_content_id


def test_publish_scheduled_story_uses_profile_cta_when_sibling_post_published(tmp_db, mocker, monkeypatch):
    """
    Kullanıcı talebi: hikaye "detaylar profilde" diyebilir AMA SADECE aynı
    habere ait feed gönderisi GERÇEKTEN yayınlanmışsa — aksi halde takipçi
    profilde olmayan bir şeye yönlendirilmiş (yanlış yönlendirme) olur.
    publish_scheduled(), hikayeyi yayınlamadan hemen önce sibling post'un en
    güncel durumunu kontrol edip görseli buna göre yeniden oluşturmalı.
    """
    schedule_id, story_content_id, _ = _make_story_with_sibling_post(tmp_db, sibling_status="published")

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(scheduler.ig_client, "upload_media_to_host", lambda path: "https://cdn.example.com/story.png")
    monkeypatch.setattr(scheduler.ig_client, "publish_story", lambda **kwargs: {"success": True, "media_id": "IGSTORY1"})
    spy = mocker.patch.object(scheduler.story_gen, "generate_story_image", return_value="/fake/story_regenerated.png")
    mocker.patch("src.scheduler.time.sleep")

    scheduler.publish_scheduled()

    spy.assert_called_once_with(content_id=story_content_id, sibling_post_published=True)
    assert _schedule_status(tmp_db, schedule_id) == "published"


def test_publish_scheduled_story_uses_generic_cta_when_sibling_post_not_published(tmp_db, mocker, monkeypatch):
    """Sibling post hiç yayınlanmamışsa (draft/pending/yok), hikaye asla 'detaylar profilde' demeye çalışmamalı."""
    schedule_id, story_content_id, _ = _make_story_with_sibling_post(tmp_db, sibling_status=None)

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(scheduler.ig_client, "upload_media_to_host", lambda path: "https://cdn.example.com/story.png")
    monkeypatch.setattr(scheduler.ig_client, "publish_story", lambda **kwargs: {"success": True, "media_id": "IGSTORY2"})
    spy = mocker.patch.object(scheduler.story_gen, "generate_story_image", return_value="/fake/story_regenerated.png")
    mocker.patch("src.scheduler.time.sleep")

    scheduler.publish_scheduled()

    spy.assert_called_once_with(content_id=story_content_id, sibling_post_published=False)
    assert _schedule_status(tmp_db, schedule_id) == "published"


def test_notify_pending_approvals_sends_post_and_story_of_same_news_together(tmp_db, mocker):
    """
    Gerçek kullanıcı şikayeti: notify_pending_approvals eskiden her
    content_type için AYRI sorgu atıp önce TÜM post'ları sonra TÜM
    story'leri gönderiyordu — aynı habere ait post ve story Telegram'da
    birbirinden kopup araya başka haberler giriyordu. Artık tek bir sorgu
    kullanıldığı için (bkz. get_content_pending_telegram_notification) aynı
    haberin post'u ve story'si art arda gönderilmeli.
    """
    news_id = tmp_db.add_news(title="Ortak Haber", url="https://example.com/paired", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    post_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.update_content_media(post_id, media_path="/fake/post.png")
    story_id = tmp_db.add_content(news_id=news_id, content_type="story", summary_text="s")
    tmp_db.update_content_media(story_id, media_path="/fake/story.png")

    # Aradaki gönderim başka bir habere ait olmasın diye ikinci bir haber
    # (daha düşük skorlu) eklenip sıralamanın bozulmadığı doğrulanır.
    other_news_id = tmp_db.add_news(title="Diğer Haber", url="https://example.com/other", category="gaming")
    tmp_db.mark_news_processed(other_news_id, relevance_score=0.5)
    other_post_id = tmp_db.add_content(news_id=other_news_id, content_type="post", caption="c")
    tmp_db.update_content_media(other_post_id, media_path="/fake/other_post.png")

    scheduler = Scheduler(db=tmp_db)
    # Telegram hız limiti gecikmesi testte gereksiz yere bekletmesin
    mocker.patch("src.scheduler.TELEGRAM_NOTIFY_DELAY_SECONDS", 0)
    sent_ids = []
    mocker.patch(
        "src.scheduler.telegram_bot.send_approval_request",
        side_effect=lambda content: sent_ids.append(content["id"]) or 1000 + content["id"],
    )

    scheduler.notify_pending_approvals()

    assert sent_ids == [post_id, story_id, other_post_id]


def test_notify_pending_approvals_throttles_between_messages(tmp_db, mocker):
    """
    Telegram bir botun AYNI sohbete gönderimini kabaca dakikada 20 mesajla
    sınırlıyor. Bildirim döngüsü 20 içeriği araya hiç boşluk koymadan
    gönderiyordu — limitin tam sınırı. Mesajlar arasında bekleme olmalı,
    ama İLK mesajdan önce beklenmemeli (gereksiz gecikme).
    """
    news_id = tmp_db.add_news(title="Haber", url="https://example.com/throttle", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    for i in range(3):
        cid = tmp_db.add_content(news_id=news_id, content_type="post", caption=f"c{i}")
        tmp_db.update_content_media(cid, media_path=f"/fake/{i}.png")

    scheduler = Scheduler(db=tmp_db)
    mocker.patch("src.scheduler.TELEGRAM_NOTIFY_DELAY_SECONDS", 2.5)
    mocker.patch("src.scheduler.telegram_bot.send_approval_request", return_value=1)
    sleep_mock = mocker.patch("src.scheduler.time.sleep")

    scheduler.notify_pending_approvals()

    # 3 mesaj gönderildiyse aralarda 2 bekleme olmalı
    assert sleep_mock.call_count == 2
    assert all(call.args[0] == 2.5 for call in sleep_mock.call_args_list)


def test_send_reminders_resends_stale_items_and_updates_message_id(tmp_db, mocker):
    """Gerçek olay (13 Ağustos 2026): zaten gönderilmiş ama cevapsız kalan
    26 onay isteği kullanıcının Telegram akışında kayboldu — notify_pending_
    approvals bunları bir daha asla bulmuyordu (sadece hiç gönderilmemişleri
    arar). send_reminders bu boşluğu kapatır."""
    news_id = tmp_db.add_news(title="Cevapsız Haber", url="https://example.com/stale-reminder", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.update_content_media(content_id, media_path="/fake/stale.png")
    tmp_db.set_content_telegram_message(content_id, 500)
    with tmp_db._get_connection() as conn:
        conn.execute(
            "UPDATE processed_content SET last_notified_at = datetime('now', '-25 hours') WHERE id = ?",
            (content_id,),
        )

    scheduler = Scheduler(db=tmp_db)
    mocker.patch("src.scheduler.TELEGRAM_NOTIFY_DELAY_SECONDS", 0)
    mocker.patch("src.scheduler.TELEGRAM_REMINDER_AFTER_HOURS", 24)
    reminder_mock = mocker.patch("src.scheduler.telegram_bot.send_reminder", return_value=999)

    stats = scheduler.send_reminders()

    reminder_mock.assert_called_once()
    assert reminder_mock.call_args[0][0]["id"] == content_id
    assert stats["reminded"] == 1
    assert tmp_db.get_content_by_id(content_id)["telegram_message_id"] == 999


def test_send_reminders_does_nothing_when_no_stale_items(tmp_db, mocker):
    scheduler = Scheduler(db=tmp_db)
    reminder_mock = mocker.patch("src.scheduler.telegram_bot.send_reminder")

    stats = scheduler.send_reminders()

    reminder_mock.assert_not_called()
    assert stats["reminded"] == 0


def test_publish_scheduled_uses_carousel_endpoint_when_carousel_paths_set(tmp_db, mocker, monkeypatch):
    """
    Kullanıcı geri bildirimi: liste tipi haberler feed'de kaydırmalı
    (carousel) olarak paylaşılabilmeli. carousel_paths doluysa
    publish_scheduled() her slaytı ayrı ayrı yükleyip publish_carousel
    kullanmalı, publish_post'a hiç düşmemeli.
    """
    content_id = _make_ready_content(tmp_db, "Liste Haberi", "https://example.com/carousel-publish", 0.9)
    tmp_db.update_content_carousel(content_id, ["/fake/slide1.png", "/fake/slide2.png", "/fake/slide3.png"])
    scheduled_time = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    schedule_id = tmp_db.schedule_post(content_id=content_id, scheduled_time=scheduled_time, post_type="post")

    scheduler = Scheduler(db=tmp_db)
    monkeypatch.setattr(scheduler.ig_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        scheduler.ig_client, "upload_media_to_host",
        lambda path: f"https://cdn.example.com/{path.split('/')[-1]}"
    )
    publish_post_spy = mocker.patch.object(scheduler.ig_client, "publish_post")
    publish_carousel_spy = mocker.patch.object(
        scheduler.ig_client, "publish_carousel",
        return_value={"success": True, "media_id": "IGCAROUSEL1", "permalink": "https://instagram.com/p/z"}
    )

    scheduler.publish_scheduled()

    publish_carousel_spy.assert_called_once()
    call_kwargs = publish_carousel_spy.call_args.kwargs
    assert call_kwargs["image_urls"] == [
        "https://cdn.example.com/slide1.png",
        "https://cdn.example.com/slide2.png",
        "https://cdn.example.com/slide3.png",
    ]
    publish_post_spy.assert_not_called()
    assert _schedule_status(tmp_db, schedule_id) == "published"


def test_setup_schedule_checks_approvals_morning_noon_and_evening(tmp_db):
    """
    Kullanıcı isteği: Telegram onay bildirimi (ya da auto modda otomatik
    zamanlama) günde tek sefer yerine sabah/öğle/akşam 3 kez kontrol
    edilmeli — böylece medyası ilk turda hazır olmayan ya da gönderim
    limitine takılan içerikler de aynı gün içinde Telegram'a ulaşır.
    """
    schedule_lib.clear()
    try:
        scheduler = Scheduler(db=tmp_db)
        scheduler.setup_schedule()

        approval_times = {
            job.at_time.strftime("%H:%M")
            for job in schedule_lib.jobs
            if job.job_func.args and job.job_func.args[1] in ("Telegram Onay Bildirimi", "Otomatik Zamanlama")
        }

        assert approval_times == {
            SCHEDULE["notify_morning"], SCHEDULE["notify_noon"], SCHEDULE["notify_evening"]
        }
    finally:
        schedule_lib.clear()


def test_content_processing_and_media_also_run_three_times_a_day(tmp_db):
    """
    Olay (10 Ağustos 2026): İçerik İşleme + Medya Üretimi günde TEK sefer
    (haber toplamadan +30/+60dk) çalışıyordu. Sabahki o tek denemede onay
    kuyruğu tavanın üstündeyse (backlog freni), üretim o gün BİR DAHA
    denenmiyordu — kuyruk öğleden sonra boşalsa bile. Kullanıcı "bugün
    hiçbir yeni içerik oluşmadı" dedi, ki teknik olarak doğruydu.

    Artık bildirimle aynı ritimde (sabah/öğle/akşam) çalışıyor: fren gün
    içinde açılırsa aynı gün fark ediliyor.
    """
    schedule_lib.clear()
    try:
        scheduler = Scheduler(db=tmp_db)
        scheduler.setup_schedule()

        for isim in ("İçerik İşleme", "Medya Üretimi"):
            saatler = {
                job.at_time.strftime("%H:%M")
                for job in schedule_lib.jobs
                if job.job_func.args and job.job_func.args[1] == isim
            }
            assert len(saatler) == 3, f"{isim} günde 3 kez kayıtlı değil: {saatler}"
    finally:
        schedule_lib.clear()


def test_process_content_slot_runs_before_its_notification_slot(tmp_db):
    """
    Sıra önemli: üretilen içerik AYNI turun bildirimiyle Telegram'a
    gitsin diye İçerik İşleme/Medya Üretimi, bildirim saatinden ÖNCE
    olmalı — sonra değil.
    """
    schedule_lib.clear()
    try:
        scheduler = Scheduler(db=tmp_db)
        scheduler.setup_schedule()

        def saatler(isim):
            return sorted(
                job.at_time.strftime("%H:%M")
                for job in schedule_lib.jobs
                if job.job_func.args and job.job_func.args[1] == isim
            )

        islenme = saatler("İçerik İşleme")
        bildirim = sorted([SCHEDULE["notify_morning"], SCHEDULE["notify_noon"],
                           SCHEDULE["notify_evening"]])

        for i, b in zip(islenme, bildirim):
            assert i < b, f"İçerik İşleme ({i}) bildirimden ({b}) önce değil"
    finally:
        schedule_lib.clear()


def test_setup_schedule_registers_reminder_job_once_a_day(tmp_db):
    schedule_lib.clear()
    try:
        scheduler = Scheduler(db=tmp_db)
        scheduler.setup_schedule()

        reminder_jobs = [
            job for job in schedule_lib.jobs
            if job.job_func.args and job.job_func.args[1] == "Onay Hatırlatması"
        ]

        assert len(reminder_jobs) == 1
    finally:
        schedule_lib.clear()


def test_setup_schedule_skips_reminder_job_in_auto_mode(tmp_db, monkeypatch):
    """Auto modda Telegram'a hiç onay isteği gitmiyor (auto_schedule_content
    doğrudan zamanlıyor) — hatırlatacak bir şey yok, iş kaydedilmemeli."""
    monkeypatch.setattr("src.scheduler.APPROVAL_MODE", "auto")
    schedule_lib.clear()
    try:
        scheduler = Scheduler(db=tmp_db)
        scheduler.setup_schedule()

        reminder_jobs = [
            job for job in schedule_lib.jobs
            if job.job_func.args and job.job_func.args[1] == "Onay Hatırlatması"
        ]

        assert reminder_jobs == []
    finally:
        schedule_lib.clear()


def test_generate_all_media_keeps_post_and_story_of_same_news_together(tmp_db, mocker, monkeypatch):
    """
    Gerçek kullanıcı şikayeti: "hikaye ve feedleri sıralı değil ayrı ayrı
    gönderdi". Kök neden medya üretimindeydi: post ve story AYRI sorgularla,
    farklı kotalarla (6 post / 15 story) seçiliyordu. Post kotası dolunca
    sıradaki haberin STORY'si üretilip Telegram'a tek başına gidiyor,
    POST'u ise günler sonra başka bir turda gönderiliyordu.

    Medya artık haber bazında üretildiği için: kota yetmiyorsa haberin
    HİÇBİR parçası üretilmemeli (yarım grup oluşmamalı).
    """
    monkeypatch.setattr("src.scheduler.DAILY_POST_LIMIT", 1)
    monkeypatch.setattr("src.scheduler.DAILY_STORY_LIMIT", 5)
    monkeypatch.setattr("src.scheduler.MEDIA_GENERATION_MULTIPLIER", 1)  # post kotası = 1

    ids = {}
    for i, score in enumerate([0.9, 0.8]):
        news_id = tmp_db.add_news(title=f"Haber {i}", url=f"https://example.com/n{i}", category="ai")
        tmp_db.mark_news_processed(news_id, relevance_score=score)
        ids[i] = {
            "post": tmp_db.add_content(news_id=news_id, content_type="post", caption="c"),
            "story": tmp_db.add_content(news_id=news_id, content_type="story", summary_text="s"),
        }

    scheduler = Scheduler(db=tmp_db)
    mocker.patch.object(scheduler.img_gen, "generate_post_image",
                        side_effect=lambda content_id: f"/fake/post_{content_id}.png")
    mocker.patch.object(scheduler.story_gen, "generate_story_image",
                        side_effect=lambda content_id: f"/fake/story_{content_id}.png")

    scheduler.generate_all_media()

    # 1. haber (yüksek skor) tam üretilmeli; 2. haber post kotası dolduğu
    # için HİÇ üretilmemeli — story'si tek başına kalmamalı.
    assert scheduler.img_gen.generate_post_image.call_count == 1
    assert scheduler.story_gen.generate_story_image.call_count == 1
    story_calls = [c.kwargs["content_id"] for c in scheduler.story_gen.generate_story_image.call_args_list]
    assert story_calls == [ids[0]["story"]], "kardeşi üretilmeyen haberin story'si tek başına üretilmiş"
