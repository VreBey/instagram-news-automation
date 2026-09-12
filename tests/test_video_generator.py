"""
VideoGenerator için testler. Hızlı doğrulama testleri _create_slide_images/
_generate_tts_audio/_compose_video'yu mock'lar; tek bir "gerçek üretim"
duman testi ise image_generator/story_generator testleriyle aynı felsefeyle
gerçek bir .mp4 dosyası üretip (moviepy + edge-tts, yavaş ama gerçek) kod
yolunun uçtan uca çalıştığını doğrular.
"""

import pytest

from src.video_generator import VideoGenerator


@pytest.fixture
def video_gen(tmp_db):
    return VideoGenerator(db=tmp_db)


def _make_reels_content(db, script):
    news_id = db.add_news(title="Test Haberi", url="https://example.com/reels-test", category="ai")
    db.mark_news_processed(news_id, relevance_score=0.8)
    return db.add_content(news_id=news_id, content_type="reels", reels_script=script)


# =============================================
# Girdi doğrulama (hızlı, encoding yapılmaz)
# =============================================

def test_generate_reels_returns_none_without_content_id_or_data(video_gen):
    assert video_gen.generate_reels() is None


def test_generate_reels_returns_none_for_unknown_content_id(video_gen):
    assert video_gen.generate_reels(content_id=99999) is None


def test_generate_reels_returns_none_when_script_has_no_segments(video_gen):
    assert video_gen.generate_reels(reels_data={"script": {"intro": "Merhaba", "segments": []}}) is None


def test_generate_reels_returns_none_when_moviepy_unavailable(video_gen, monkeypatch):
    monkeypatch.setattr("src.video_generator.MOVIEPY_AVAILABLE", False)
    reels_data = {"intro": "x", "segments": ["a", "b"], "news_titles": ["A", "B"], "category": "ai"}
    assert video_gen.generate_reels(reels_data=reels_data) is None


# =============================================
# Orkestrasyon (mock'lanmış slide/audio/compose adımlarıyla hızlı)
# =============================================

def test_generate_reels_updates_content_media_on_success(tmp_db, video_gen, mocker, tmp_path):
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"fake-mp4-bytes")
    fake_slide_1 = tmp_path / "slide1.png"
    fake_slide_2 = tmp_path / "slide2.png"
    fake_slide_1.write_bytes(b"fake-png")
    fake_slide_2.write_bytes(b"fake-png")

    mocker.patch.object(video_gen, "_create_slide_images", return_value=[str(fake_slide_1), str(fake_slide_2)])
    mocker.patch.object(video_gen, "_generate_tts_audio", return_value=None)
    mocker.patch.object(video_gen, "_compose_video", return_value=str(fake_video))

    script = {"intro": "Merhaba", "segments": ["Haber 1"], "news_titles": ["Haber 1"]}
    content_id = _make_reels_content(tmp_db, script)

    result = video_gen.generate_reels(content_id=content_id)

    assert result == str(fake_video)
    content = tmp_db.get_content_by_id(content_id)
    assert content["media_path"] == str(fake_video)
    # gecici slide dosyalari temizlenmis olmali
    assert not fake_slide_1.exists()
    assert not fake_slide_2.exists()


def test_generate_reels_returns_none_when_slides_fail(video_gen, mocker):
    mocker.patch.object(video_gen, "_create_slide_images", return_value=[])

    reels_data = {"intro": "x", "segments": ["a", "b"], "news_titles": ["A", "B"], "category": "ai"}
    assert video_gen.generate_reels(reels_data=reels_data) is None


# =============================================
# Gerçek uçtan uca üretim (yavaş — moviepy + edge-tts)
# =============================================

def test_generate_reels_real_end_to_end_creates_valid_mp4(video_gen):
    reels_data = {
        "intro": "Bugünün en önemli haberleri!",
        "segments": [
            "OpenAI yeni GPT-5 modelini duyurdu.",
            "GTA 6 çıkış tarihi resmi olarak açıklandı.",
        ],
        "news_titles": ["OpenAI GPT-5 Duyurdu", "GTA 6 Çıkış Tarihi"],
        "category": "ai",
    }

    path = video_gen.generate_reels(reels_data=reels_data)

    assert path is not None
    from pathlib import Path
    assert Path(path).exists()
    assert Path(path).suffix == ".mp4"
    assert Path(path).stat().st_size > 0
