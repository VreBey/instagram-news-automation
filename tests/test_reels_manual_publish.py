"""Sessiz reels + elle yayınlama akışı (C seçeneği).

Karar (5 Ağustos 2026): Instagram'ın lisanslı müzik kütüphanesi Graph
API'den erişilemiyor — Meta referansında reels'in tek ses parametresi
`audio_name` ve o da yalnızca videodaki MEVCUT sesi yeniden adlandırıyor.
Yani "otomatik yayın" ile "Instagram müziği" birbirini dışlıyor.

Müzik tercih edildi: video otomatik ve SESSİZ üretilir, Telegram'a gönderilir,
kullanıcı uygulamada müziği ekleyip elle paylaşır.

Önceki sürüm videoyu da kullanıcıya ürettiriyordu (Gemini/Veo promptu) ve
sonuç 20 taslak / 0 yayın oldu — her seferinde manuel iş istediği için hiç
yapılmadı. Artık yalnızca son adım manuel.
"""

import pytest


# =============================================
# Video sessiz üretilmeli
# =============================================

def test_tts_is_skipped_when_silent(tmp_db, monkeypatch):
    """
    Videoda ses olsaydı uygulamada müzik eklenirken altta kalıp çakışırdı.
    """
    from src import video_generator as vg

    monkeypatch.setattr(vg, "REELS_SILENT", True)
    monkeypatch.setattr(vg, "TTS_AVAILABLE", True)

    tts_cagrildi = []
    gen = vg.VideoGenerator(db=tmp_db)
    monkeypatch.setattr(gen, "_create_slide_images", lambda s, c: ["/tmp/a.png", "/tmp/b.png"])
    monkeypatch.setattr(gen, "_generate_tts_audio",
                        lambda s: tts_cagrildi.append(1) or "/tmp/ses.mp3")
    monkeypatch.setattr(gen, "_compose_video",
                        lambda slides, audio, cat: "/tmp/video.mp4")

    gen.generate_reels(reels_data={"script": {"segments": ["a"]}, "category": "gaming"})

    assert tts_cagrildi == [], "sessiz modda TTS hic cagrilmamali"


def test_compose_receives_no_audio_when_silent(tmp_db, monkeypatch):
    from src import video_generator as vg

    monkeypatch.setattr(vg, "REELS_SILENT", True)
    monkeypatch.setattr(vg, "TTS_AVAILABLE", True)
    gecen_ses = []

    gen = vg.VideoGenerator(db=tmp_db)
    monkeypatch.setattr(gen, "_create_slide_images", lambda s, c: ["/tmp/a.png", "/tmp/b.png"])
    monkeypatch.setattr(gen, "_compose_video",
                        lambda slides, audio, cat: gecen_ses.append(audio) or "/tmp/v.mp4")

    gen.generate_reels(reels_data={"script": {"segments": ["a"]}, "category": "ai"})

    assert gecen_ses == [None]


def test_tts_still_used_when_not_silent(tmp_db, monkeypatch):
    """Ayar kapatılırsa eski davranış geri gelmeli."""
    from src import video_generator as vg

    monkeypatch.setattr(vg, "REELS_SILENT", False)
    monkeypatch.setattr(vg, "TTS_AVAILABLE", True)
    tts_cagrildi = []

    gen = vg.VideoGenerator(db=tmp_db)
    monkeypatch.setattr(gen, "_create_slide_images", lambda s, c: ["/tmp/a.png", "/tmp/b.png"])
    monkeypatch.setattr(gen, "_generate_tts_audio",
                        lambda s: tts_cagrildi.append(1) or "/tmp/ses.mp3")
    monkeypatch.setattr(gen, "_compose_video", lambda slides, audio, cat: "/tmp/v.mp4")

    gen.generate_reels(reels_data={"script": {"segments": ["a"]}, "category": "ai"})

    assert tts_cagrildi == [1]


# =============================================
# API ile yayınlanmamalı — asıl koruma
# =============================================

@pytest.fixture
def scheduler(tmp_db, monkeypatch):
    from src.scheduler import Scheduler
    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    return s


def _reels_taslagi(db, skor=0.95):
    news_id = db.add_news(title="Reels haberi", url="https://example.com/r", category="gaming")
    db.mark_news_processed(news_id, relevance_score=skor)
    cid = db.add_content(news_id=news_id, content_type="reels", caption="c",
                         summary_text="Türkçe reels özeti")
    db.update_content_media(cid, "/tmp/reels.mp4")
    return cid


def test_silent_reels_are_not_auto_scheduled(scheduler, tmp_db, monkeypatch):
    """
    Otomatik zamanlama reels'i alsaydı API'den MÜZİKSİZ yayınlanırdı —
    tüm tasarımın amacını bozar.
    """
    monkeypatch.setattr("src.scheduler.REELS_SILENT", True)
    monkeypatch.setattr("src.scheduler.AUTO_PUBLISH_THRESHOLD", 0.5)
    _reels_taslagi(tmp_db)

    sonuc = scheduler.auto_schedule_content()

    assert sonuc["reels"] == 0
    with tmp_db._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM scheduled_posts").fetchone()[0]
    assert n == 0, "sessiz reels zamanlanmamali"


def test_silent_reels_skip_approval_queue(scheduler, tmp_db, monkeypatch):
    """
    Onay butonları API yayınını tetikliyor; reels o kuyruğa girmemeli.
    Videosu zaten ayrı bir mesajla gönderiliyor.
    """
    monkeypatch.setattr("src.scheduler.REELS_SILENT", True)
    monkeypatch.setattr("src.scheduler.APPROVAL_MODE", "telegram")
    monkeypatch.setattr("src.scheduler.TELEGRAM_NOTIFY_DELAY_SECONDS", 0)
    _reels_taslagi(tmp_db)

    gonderilen = []
    monkeypatch.setattr("src.telegram_bot.send_approval_request",
                        lambda c: gonderilen.append(c) or 1)

    sonuc = scheduler.notify_pending_approvals()

    assert sonuc["reels"] == 0
    assert all(c.get("content_type") != "reels" for c in gonderilen)


def test_posts_and_stories_still_scheduled(scheduler, tmp_db, monkeypatch):
    """Kapatma yalnızca reels'e özel olmalı."""
    monkeypatch.setattr("src.scheduler.REELS_SILENT", True)
    monkeypatch.setattr("src.scheduler.AUTO_PUBLISH_THRESHOLD", 0.5)

    news_id = tmp_db.add_news(title="Post haberi", url="https://example.com/p", category="ai")
    tmp_db.mark_news_processed(news_id, relevance_score=0.95)
    cid = tmp_db.add_content(news_id=news_id, content_type="post", caption="c",
                             summary_text="Türkçe özet")
    tmp_db.update_content_media(cid, "/tmp/post.png")

    sonuc = scheduler.auto_schedule_content()

    assert sonuc["post"] >= 1


def test_reels_publish_again_when_silent_disabled(scheduler, tmp_db, monkeypatch):
    """Ayar kapatılırsa reels normal API akışına geri döner."""
    monkeypatch.setattr("src.scheduler.REELS_SILENT", False)
    monkeypatch.setattr("src.scheduler.AUTO_PUBLISH_THRESHOLD", 0.5)
    _reels_taslagi(tmp_db)

    sonuc = scheduler.auto_schedule_content()

    assert sonuc["reels"] >= 1


# =============================================
# Telegram gönderimi
# =============================================

def test_missing_video_file_is_reported(monkeypatch):
    from src import telegram_bot

    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "1")

    sonuc = telegram_bot.send_reels_for_manual_publish(
        {"category": "gaming", "caption": "c"}, "/olmayan/video.mp4"
    )
    assert sonuc is None


def test_instructions_mention_manual_music(tmp_path, monkeypatch):
    """
    Mesaj ne yapılacağını söylemeli — video sessiz geldiği için kullanıcı
    aksi halde eksik sanabilir.
    """
    from src import telegram_bot

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    gonderilen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "result": {"message_id": 7}}

    def _sahte(method, url, data=None, files=None, **k):
        gonderilen.update(data or {})
        return _Resp()

    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(telegram_bot, "_request_with_retry", _sahte)
    monkeypatch.setattr(telegram_bot, "_send_text", lambda *a, **k: 1)

    mid = telegram_bot.send_reels_for_manual_publish(
        {"category": "gaming", "caption": "Açıklama", "hashtags": ["#oyun"]}, str(video)
    )

    assert mid == 7
    assert "müzi" in gonderilen["caption"].lower()
    assert "sessiz" in gonderilen["caption"].lower()
