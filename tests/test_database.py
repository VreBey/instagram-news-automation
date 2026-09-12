"""Database sınıfı için birim testleri."""

from datetime import datetime, timedelta


def test_connection_has_generous_busy_timeout(tmp_db):
    """
    Birden fazla süreç (dashboard, telegram_bot, scheduler, mcp_server) ayni
    SQLite dosyasini paylasiyor. Kisa bir timeout, gercek kullanimda basariyla
    yayinlanmis icerigin durumunun veritabanina hic yazilamamasina yol acti
    ("database is locked") -- bu test o regresyonu kilitliyor.
    """
    with tmp_db._get_connection() as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_timeout >= 30000


def test_add_publish_record_does_not_nest_open_connection(tmp_db, monkeypatch):
    """
    Gerçek olay: add_publish_record, kendi INSERT'i için açtığı bağlantı
    HENÜZ commit edilmeden (with bloğu içindeyken) update_content_status'u
    çağırıp İKİNCİ bir bağlantı açıyordu. Aynı süreç içinde olsa bile SQLite
    bunları ayrı bağlantı sayıyor: dıştaki commit olmadan kilidi bırakmıyor,
    içteki busy_timeout boyunca bekleyip "database is locked" ile patlıyordu
    -- başarıyla yayınlanmış bir gönderi hiç kaydedilemiyordu. Bu test,
    update_content_status'un artık INSERT bağlantısı KAPANDIKTAN SONRA
    çağrıldığını doğrular: çağrı anında aktif/açık bir bağlantı olmamalı.
    """
    news_id = tmp_db.add_news(title="Test Haberi", url="https://example.com/publish-record", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="test")

    real_get_connection = tmp_db._get_connection.__func__
    open_connections = []

    from contextlib import contextmanager

    @contextmanager
    def tracking_get_connection(self):
        if open_connections:
            raise AssertionError(
                "update_content_status bir önceki bağlantı kapanmadan çağrıldı "
                "(nested-connection self-deadlock regresyonu)"
            )
        with real_get_connection(self) as conn:
            open_connections.append(conn)
            try:
                yield conn
            finally:
                open_connections.pop()

    monkeypatch.setattr(type(tmp_db), "_get_connection", tracking_get_connection)

    record_id = tmp_db.add_publish_record(content_id=content_id, post_type="post", status="success")

    assert record_id is not None
    assert tmp_db.get_content_by_id(content_id)["status"] == "published"


def test_mark_manually_published_closes_out_content_and_schedule(tmp_db):
    """Meta feed post yayınını API'den engellediğinde (bkz. proje hafızası:
    instagram_feed_publish_blocked), kullanıcı Instagram'dan elle
    paylaştıktan sonra bu metod içeriği ve schedule kaydını kapatır --
    aksi halde 'approved' durumunda sonsuza dek asılı kalırdı."""
    news_id = tmp_db.add_news(title="Elle Paylaşım Haberi", url="https://example.com/manual-pub", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="test")
    tmp_db.update_content_media(content_id, media_path="/fake/manual.png")
    schedule_id = tmp_db.schedule_post(content_id=content_id, scheduled_time="2026-01-01 10:00:00", post_type="post")
    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE scheduled_posts SET status='failed' WHERE id=?", (schedule_id,))

    tmp_db.mark_manually_published(content_id, "post")

    assert tmp_db.get_content_by_id(content_id)["status"] == "published"
    with tmp_db._get_connection() as conn:
        row = conn.execute("SELECT status FROM scheduled_posts WHERE id=?", (schedule_id,)).fetchone()
    assert row["status"] == "published"
    history = tmp_db.get_publish_history()
    assert any(h["content_id"] == content_id and h["status"] == "success" for h in history)


def test_mark_manually_published_works_without_a_schedule_row(tmp_db):
    """schedule kaydı hiç yoksa (ör. hiç zamanlanmamış bir içerik) bile
    içerik yine de 'published' olarak kapatılabilmeli, hata fırlatmamalı."""
    news_id = tmp_db.add_news(title="Zamanlanmamış Haber", url="https://example.com/manual-pub-2", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="test")

    tmp_db.mark_manually_published(content_id, "post")

    assert tmp_db.get_content_by_id(content_id)["status"] == "published"


def test_add_news_url_uniqueness(tmp_db):
    news_id = tmp_db.add_news(title="Haber 1", url="https://example.com/1", category="ai")
    assert news_id is not None

    duplicate_id = tmp_db.add_news(title="Haber 1 (tekrar)", url="https://example.com/1", category="ai")
    assert duplicate_id is None


def test_mark_news_processed_and_used(tmp_db):
    news_id = tmp_db.add_news(title="Haber 2", url="https://example.com/2", category="gaming")

    unprocessed = tmp_db.get_unprocessed_news(category="gaming")
    assert any(n["id"] == news_id for n in unprocessed)

    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    unprocessed_after = tmp_db.get_unprocessed_news(category="gaming")
    assert not any(n["id"] == news_id for n in unprocessed_after)

    unused = tmp_db.get_unused_news(category="gaming")
    assert any(n["id"] == news_id for n in unused)

    tmp_db.mark_news_used(news_id)
    unused_after = tmp_db.get_unused_news(category="gaming")
    assert not any(n["id"] == news_id for n in unused_after)


def test_get_content_pending_telegram_notification_pairs_post_and_story(tmp_db):
    """
    Gerçek kullanıcı şikayeti: Telegram'da bir haberin post'u ile story'si
    birbirinden kopuk, araya başka haberlerin içeriği girmiş şekilde
    geliyordu (eskiden her content_type ayrı ayrı sorgulanıp önce TÜM
    post'lar sonra TÜM story'ler gönderiliyordu). Artık aynı habere ait
    içerikler (post → story → reels sırasıyla) yan yana, haberler de
    relevance_score'a göre gelmeli.
    """
    # Düşük skorlu haber, ÖNCE eklendi ama SONRA gelmeli (skora göre sıralı).
    news_low = tmp_db.add_news(title="Az Önemli Haber", url="https://example.com/low", category="ai")
    tmp_db.mark_news_processed(news_low, relevance_score=0.5)
    low_post = tmp_db.add_content(news_id=news_low, content_type="post", caption="c")
    tmp_db.update_content_media(low_post, media_path="/fake/low_post.png")
    low_story = tmp_db.add_content(news_id=news_low, content_type="story", summary_text="s")
    tmp_db.update_content_media(low_story, media_path="/fake/low_story.png")

    # Yüksek skorlu haber, SONRA eklendi ama ÖNCE gelmeli.
    news_high = tmp_db.add_news(title="Önemli Haber", url="https://example.com/high", category="gaming")
    tmp_db.mark_news_processed(news_high, relevance_score=0.95)
    high_story = tmp_db.add_content(news_id=news_high, content_type="story", summary_text="s")
    tmp_db.update_content_media(high_story, media_path="/fake/high_story.png")
    high_post = tmp_db.add_content(news_id=news_high, content_type="post", caption="c")
    tmp_db.update_content_media(high_post, media_path="/fake/high_post.png")

    result = tmp_db.get_content_pending_telegram_notification(limit=20)
    ordered_ids = [r["id"] for r in result]

    # Yüksek skorlu haberin içerikleri (post, story herhangi bir ekleme
    # sırasında olsa da) tamamen önce, düşük skorlunun içerikleri sonra.
    assert ordered_ids == [high_post, high_story, low_post, low_story]


def test_update_content_carousel_sets_paths_and_cover_media_path(tmp_db):
    """
    Carousel'in ilk slaydı media_path'e de yazılmalı ki tekil-görsel varsayan
    eski kod yolları (ör. Telegram onay önizlemesi) değişmeden çalışsın.
    """
    news_id = tmp_db.add_news(title="Liste Haberi", url="https://example.com/carousel-db", category="gaming")
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")

    tmp_db.update_content_carousel(content_id, ["/fake/slide1.png", "/fake/slide2.png", "/fake/slide3.png"])

    content = tmp_db.get_content_by_id(content_id)
    assert content["media_path"] == "/fake/slide1.png"
    assert content["carousel_paths"] == ["/fake/slide1.png", "/fake/slide2.png", "/fake/slide3.png"]


def test_update_content_media_clears_stale_carousel_paths(tmp_db):
    """
    Gerçek risk: carousel üretilmiş bir içerik için sonradan manuel/tekil bir
    görsel set edilirse (ör. Telegram'dan "Farklı Görsel İste"), eski
    carousel_paths temizlenmezse publish_scheduled() hâlâ eski (artık kapağı
    değişmiş) carousel'i yayınlamaya çalışabilir.
    """
    news_id = tmp_db.add_news(title="Liste Haberi 2", url="https://example.com/carousel-clear", category="gaming")
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.update_content_carousel(content_id, ["/fake/slide1.png", "/fake/slide2.png"])

    tmp_db.update_content_media(content_id, media_path="/fake/new_single.png")

    content = tmp_db.get_content_by_id(content_id)
    assert content["media_path"] == "/fake/new_single.png"
    assert content["carousel_paths"] is None


def test_schedule_post_and_pending_window(tmp_db):
    news_id = tmp_db.add_news(title="Haber 3", url="https://example.com/3", category="ai")
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="test")

    past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    future_time = (datetime.now() + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")

    tmp_db.schedule_post(content_id, past_time, "post", source="auto")
    tmp_db.schedule_post(content_id, future_time, "post", source="manual")

    pending = tmp_db.get_pending_scheduled()
    assert len(pending) == 1
    assert pending[0]["scheduled_time"] == past_time


def test_get_scheduled_count_today(tmp_db):
    news_id = tmp_db.add_news(title="Haber 4", url="https://example.com/4", category="ai")
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="test")

    today_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp_db.schedule_post(content_id, today_time, "post", source="auto")

    assert tmp_db.get_scheduled_count_today("post") == 1
    assert tmp_db.get_scheduled_count_today("story") == 0


def test_app_settings_get_set(tmp_db):
    assert tmp_db.get_setting("missing_key") is None
    assert tmp_db.get_setting("missing_key", default="fallback") == "fallback"

    tmp_db.set_setting("instagram_access_token", "token-v1")
    assert tmp_db.get_setting("instagram_access_token") == "token-v1"

    tmp_db.set_setting("instagram_access_token", "token-v2")
    assert tmp_db.get_setting("instagram_access_token") == "token-v2"


def test_add_and_get_external_research(tmp_db):
    research_id = tmp_db.add_external_research(
        source="gemini_spark", title="Ekonomi Bülteni",
        summary="Kısa özet", topic="ekonomi", source_url="https://example.com/r1"
    )
    assert research_id is not None

    findings = tmp_db.get_external_research()
    assert len(findings) == 1
    assert findings[0]["title"] == "Ekonomi Bülteni"
    assert findings[0]["promoted_news_id"] is None


def test_promote_research_to_news(tmp_db):
    research_id = tmp_db.add_external_research(
        source="gemini_spark", title="Yeni bir AI modeli duyuruldu",
        summary="Detaylı özet burada", topic="ai", source_url="https://example.com/r2"
    )

    news_id = tmp_db.promote_research_to_news(research_id, category="ai")
    assert news_id is not None

    findings = tmp_db.get_external_research()
    assert findings[0]["promoted_news_id"] == news_id

    unprocessed = tmp_db.get_unprocessed_news(category="ai")
    assert any(n["id"] == news_id for n in unprocessed)


def test_promote_research_to_news_is_idempotent(tmp_db):
    research_id = tmp_db.add_external_research(
        source="gemini_spark", title="Tekrar aktarılmaya çalışılan bulgu",
        summary="özet"
    )

    first = tmp_db.promote_research_to_news(research_id, category="gaming")
    second = tmp_db.promote_research_to_news(research_id, category="gaming")

    assert first is not None
    assert second is None


def test_promote_research_without_source_url_uses_synthetic_url(tmp_db):
    research_id = tmp_db.add_external_research(
        source="gemini_spark", title="Kaynak URL'si olmayan bulgu", summary="özet"
    )
    news_id = tmp_db.promote_research_to_news(research_id, category="ai")
    assert news_id is not None


def test_promote_research_credits_publication_not_tool(tmp_db):
    """
    Kaynak adı, bulguyu BULAN araçtan değil haberin GERÇEK yayınından gelir.

    Tarihçe: önce source_name her zaman sabit "Gemini Spark" yazılıyordu,
    sonra araca göre ("Claude Research" / "Gemini Spark") etiketlenmeye
    başlandı. İkisi de yanlıştı — Claude bir yayın organı değil, haberi bulan
    araç; görselin üzerine "📰 Kaynak: Claude Research" basmak okuyucuya
    yanlış kaynak gösteriyordu ve gerçek yayının hakkını yiyordu
    (kullanıcı itirazı, 4 Ağustos 2026).
    """
    research_id = tmp_db.add_external_research(
        source="claude_research", title="Claude'un bulduğu bir oyun haberi",
        summary="özet", source_url="https://screenrant.com/bir-haber/",
    )
    news_id = tmp_db.promote_research_to_news(research_id, category="gaming")
    unprocessed = tmp_db.get_unprocessed_news(category="gaming")
    match = next(n for n in unprocessed if n["id"] == news_id)

    assert match["source_name"] == "screenrant.com"
    assert "Claude" not in match["source_name"]


def test_manual_video_prompt_round_trip(tmp_db):
    news_id = tmp_db.add_news(title="Reels Haberi", url="https://example.com/reels", category="gaming")
    content_id = tmp_db.add_content(news_id=news_id, content_type="reels", caption="test")

    assert tmp_db.get_content_by_manual_video_prompt_message(555) is None

    tmp_db.set_manual_video_prompt(content_id, 555)

    found = tmp_db.get_content_by_manual_video_prompt_message(555)
    assert found is not None
    assert found["id"] == content_id


def test_init_db_idempotent(tmp_db):
    # _init_db zaten __init__ içinde bir kez çalıştı; tekrar çağrılabilmeli.
    tmp_db._init_db()
    tmp_db._migrate_schema()

    stats = tmp_db.get_stats()
    assert stats["total_news"] == 0


def test_get_stats_includes_category_and_type_breakdown(tmp_db):
    ai_news = tmp_db.add_news(title="AI Haberi", url="https://example.com/ai1", category="ai")
    gaming_news = tmp_db.add_news(title="Gaming Haberi", url="https://example.com/g1", category="gaming")
    tmp_db.add_content(news_id=ai_news, content_type="post", caption="test")
    tmp_db.add_content(news_id=gaming_news, content_type="story", caption="test")

    stats = tmp_db.get_stats()

    assert stats["news_by_category"] == {"ai": 1, "gaming": 1}
    assert stats["draft_by_type"] == {"post": 1, "story": 1}



def test_get_daily_publish_counts_fills_gaps_with_zero(tmp_db):
    """
    Dashboard'daki yayın trendi grafiği için: veri olmayan günler de dizide
    0 olarak yer almalı, aksi halde grafik boşluklu/yanlış hizalı görünür.
    """
    news_id = tmp_db.add_news(title="Test Haberi", url="https://example.com/trend", category="ai")
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")

    today = datetime.now().strftime("%Y-%m-%d")
    three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    with tmp_db._get_connection() as conn:
        conn.execute(
            "INSERT INTO publish_history (content_id, post_type, status, published_at) VALUES (?, ?, ?, ?)",
            (content_id, "post", "success", f"{today} 10:00:00"),
        )
        conn.execute(
            "INSERT INTO publish_history (content_id, post_type, status, published_at) VALUES (?, ?, ?, ?)",
            (content_id, "post", "success", f"{three_days_ago} 09:00:00"),
        )
        # Başarısız bir paylaşım sayılmamalı
        conn.execute(
            "INSERT INTO publish_history (content_id, post_type, status, published_at) VALUES (?, ?, ?, ?)",
            (content_id, "post", "failed", f"{today} 11:00:00"),
        )

    result = tmp_db.get_daily_publish_counts(days=7)

    assert len(result) == 7
    assert result[-1]["day"] == today
    assert result[-1]["count"] == 1
    assert result[-4]["day"] == three_days_ago
    assert result[-4]["count"] == 1
    # Aradaki günler (veri yok) 0 olarak dolu olmalı
    assert result[0]["count"] == 0


def test_pending_telegram_notification_excludes_stale_content(tmp_db):
    """
    Gerçek kullanıcı şikayeti: "eski içeriklerden gönderdi". Kotalar dolduğu
    için bildirilememiş taslaklar kuyrukta birikip günler sonra, değeri
    kalmamışken Telegram'a gidiyordu. max_age_days verildiğinde bunlar
    kuyruğa hiç alınmamalı.

    ÖLÇÜ DEĞİŞTİ (8 Ağustos 2026): bu test eskiden HABERİ yaşlandırıyordu ve
    filtre de `ni.collected_at`'e bakıyordu. Vekil ölçü gönderi/hikâye için
    işe yarıyordu ama REELS'te yanlıştı: reels bir DERLEME ve `news_id`
    yalnızca ilk habere işaret ediyor. Üretimde bekleyen 4 reels'in 2'si bu
    yüzden eleniyordu — içerikler 1 günlük, ilk haberleri 4 ve 5 günlük.
    Yani dün üretilmiş bir reels "bayat" sayılıp hiç gönderilmiyordu.

    Kuralın amacı zaten KUYRUKTA BEKLEYİP bayatlamış içeriği elemekti;
    artık doğrudan `pc.created_at` ölçülüyor.
    Bkz. tests/test_notification_freshness.py
    """
    fresh_id = tmp_db.add_news(title="Taze Haber", url="https://example.com/fresh", category="ai")
    stale_id = tmp_db.add_news(title="Bayat Haber", url="https://example.com/stale", category="ai")
    stale_cid = None
    for news_id in (fresh_id, stale_id):
        tmp_db.mark_news_processed(news_id, relevance_score=0.9)
        cid = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
        tmp_db.update_content_media(cid, media_path=f"/fake/{news_id}.png")
        if news_id == stale_id:
            stale_cid = cid

    with tmp_db._get_connection() as conn:
        # Gerçekçi senaryo: hem haber hem de ondan üretilen içerik eski.
        conn.execute(
            "UPDATE news_items SET collected_at = datetime('now', '-10 days') WHERE id = ?",
            (stale_id,),
        )
        conn.execute(
            "UPDATE processed_content SET created_at = datetime('now', '-10 days') WHERE id = ?",
            (stale_cid,),
        )

    titles = {c["news_title"] for c
              in tmp_db.get_content_pending_telegram_notification(limit=20, max_age_days=3)}
    assert titles == {"Taze Haber"}

    # Sınır verilmezse eski davranış korunur (ikisi de gelir)
    all_titles = {c["news_title"] for c
                  in tmp_db.get_content_pending_telegram_notification(limit=20)}
    assert all_titles == {"Taze Haber", "Bayat Haber"}


def _make_sent_draft(db, title, url, telegram_message_id=100, hours_ago=None,
                      used_in_roundup=0):
    """get_stale_pending_approvals testleri için: ZATEN gönderilmiş
    (telegram_message_id dolu) bir taslak oluşturur."""
    news_id = db.add_news(title=title, url=url, category="ai")
    db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = db.add_content(news_id=news_id, content_type="post", caption="c")
    db.update_content_media(content_id, media_path=f"/fake/{news_id}.png")
    db.set_content_telegram_message(content_id, telegram_message_id)
    with db._get_connection() as conn:
        if hours_ago is not None:
            conn.execute(
                "UPDATE processed_content SET last_notified_at = datetime('now', ?) WHERE id = ?",
                (f"-{hours_ago} hours", content_id),
            )
        if used_in_roundup:
            conn.execute(
                "UPDATE processed_content SET used_in_roundup = 1 WHERE id = ?",
                (content_id,),
            )
    return content_id


def test_set_content_telegram_message_stamps_last_notified_at(tmp_db):
    """İlk gönderimde de last_notified_at doluyor olmalı — hatırlatma eşiği
    bunu baz alıyor (bkz. get_stale_pending_approvals)."""
    content_id = _make_sent_draft(tmp_db, "Haber", "https://example.com/stamp")
    row = tmp_db.get_content_by_id(content_id)
    assert row["last_notified_at"] is not None


def test_get_stale_pending_approvals_finds_old_unanswered(tmp_db):
    """25 saattir cevapsız kalan (eşik 24 saat), zaten gönderilmiş bir
    taslak hatırlatma adayı olmalı."""
    old_id = _make_sent_draft(tmp_db, "Eski Haber", "https://example.com/old", hours_ago=25)

    stale = tmp_db.get_stale_pending_approvals(hours=24, limit=10)

    assert {c["id"] for c in stale} == {old_id}


def test_get_stale_pending_approvals_excludes_recently_notified(tmp_db):
    """1 saat önce gönderilmiş bir taslak, 24 saatlik eşiği henüz
    doldurmadığı için hatırlatma adayı OLMAMALI."""
    _make_sent_draft(tmp_db, "Taze Gönderim", "https://example.com/recent", hours_ago=1)

    stale = tmp_db.get_stale_pending_approvals(hours=24, limit=10)

    assert stale == []


def test_get_stale_pending_approvals_excludes_never_sent(tmp_db):
    """telegram_message_id hiç set edilmemiş (hiç gönderilmemiş) bir taslak
    hatırlatma adayı olamaz — o notify_pending_approvals'ın işi."""
    news_id = tmp_db.add_news(title="Hiç Gönderilmemiş", url="https://example.com/never", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.update_content_media(content_id, media_path="/fake/never.png")

    stale = tmp_db.get_stale_pending_approvals(hours=24, limit=10)

    assert content_id not in {c["id"] for c in stale}


def test_get_stale_pending_approvals_excludes_used_in_roundup(tmp_db):
    """Günlük derlemeye dahil edilmiş bir taslak tekil olarak hatırlatılmaz
    — kullanıcı onu derleme gönderisi üzerinden onaylar/reddeder."""
    _make_sent_draft(tmp_db, "Derlemede", "https://example.com/roundup",
                      hours_ago=25, used_in_roundup=1)

    stale = tmp_db.get_stale_pending_approvals(hours=24, limit=10)

    assert stale == []


def test_get_stale_pending_approvals_treats_missing_last_notified_at_as_old(tmp_db):
    """Migrasyondan önce gönderilmiş satırların last_notified_at'i NULL'dır
    (created_at'e düşülür) — sonsuza dek hatırlatma dışı kalmamalı."""
    news_id = tmp_db.add_news(title="Eski Migrasyon Öncesi", url="https://example.com/pre-migration", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(news_id=news_id, content_type="post", caption="c")
    tmp_db.update_content_media(content_id, media_path="/fake/pre.png")
    with tmp_db._get_connection() as conn:
        conn.execute(
            "UPDATE processed_content SET telegram_message_id = 555, "
            "created_at = datetime('now', '-30 hours') WHERE id = ?",
            (content_id,),
        )

    stale = tmp_db.get_stale_pending_approvals(hours=24, limit=10)

    assert content_id in {c["id"] for c in stale}


def test_get_stale_pending_approvals_respects_limit_oldest_first(tmp_db):
    older = _make_sent_draft(tmp_db, "Daha Eski", "https://example.com/older", hours_ago=48)
    newer = _make_sent_draft(tmp_db, "Daha Yeni", "https://example.com/newer", hours_ago=25)

    stale = tmp_db.get_stale_pending_approvals(hours=24, limit=1)

    assert [c["id"] for c in stale] == [older]
