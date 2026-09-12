"""
StoryGenerator için duman (smoke) testleri. ImageGenerator testleriyle aynı
felsefe: piksel-mükemmel doğrulama yok, sadece dosyanın oluştuğu, doğru
boyutta olduğu ve DB güncellemelerinin doğru tetiklendiği kontrol edilir.
generate_story_image() gerçek output/stories/ dizinine yazar.
"""

import pytest
from PIL import Image

from config import STORY_SIZE
from src.story_generator import StoryGenerator


@pytest.fixture
def story_gen(tmp_db):
    return StoryGenerator(db=tmp_db)


def _make_draft_content(db, content_type="story", category="ai"):
    news_id = db.add_news(title="Test Haberi", url=f"https://example.com/{content_type}", category=category)
    db.mark_news_processed(news_id, relevance_score=0.8)
    return db.add_content(news_id=news_id, content_type=content_type, summary_text="Kısa özet metni")


def test_generate_story_image_from_news_data_creates_valid_file(story_gen):
    news_data = {"category": "gaming", "summary_text": "GTA 6 Çıkış Tarihi", "source_name": "IGN"}
    path = story_gen.generate_story_image(news_data=news_data)

    assert path is not None
    with Image.open(path) as img:
        assert img.size == STORY_SIZE


def test_generate_story_image_draws_brand_logo_when_available(story_gen, mocker):
    fake_logo = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
    logo_mock = mocker.patch.object(story_gen.img_gen, "_get_logo", return_value=fake_logo)

    news_data = {"category": "gaming", "summary_text": "GTA 6 Çıkış Tarihi", "source_name": "IGN"}
    path = story_gen.generate_story_image(news_data=news_data)

    assert path is not None
    logo_mock.assert_any_call("horizontal")


def test_generate_story_image_returns_none_without_content_id_or_news_data(story_gen):
    assert story_gen.generate_story_image() is None


def test_generate_story_image_returns_none_for_unknown_content_id(story_gen):
    assert story_gen.generate_story_image(content_id=99999) is None


def test_generate_story_image_updates_content_media_path(tmp_db, story_gen):
    content_id = _make_draft_content(tmp_db)

    path = story_gen.generate_story_image(content_id=content_id)

    assert path is not None
    content = tmp_db.get_content_by_id(content_id)
    assert content["media_path"] == path


def test_generate_story_image_records_background_source(tmp_db, story_gen):
    """
    Ölçüm (11 Ağustos 2026): `generate_post_image` bunu her zaman
    yazıyordu, story tarafında EKSİKTİ — background_source hiç
    kaydedilmediği için "story'lerde gerçek görsel oranı nedir" sorusu
    hiç ölçülemiyordu (yayınlanmış 6 story'nin 6'sında da alan NULL'dı).
    """
    content_id = _make_draft_content(tmp_db)

    story_gen.generate_story_image(content_id=content_id)

    content = tmp_db.get_content_by_id(content_id)
    assert content["background_source"] is not None
    assert content["background_source"] != "bilinmiyor"


def test_generate_story_image_without_content_id_does_not_crash(story_gen):
    """news_data yolunda content_id yok — DB'ye yazma denenmemeli."""
    news_data = {"category": "gaming", "summary_text": "Test", "source_name": "IGN"}
    path = story_gen.generate_story_image(news_data=news_data)
    assert path is not None


def test_generate_daily_summary_stories_creates_cover_news_and_cta_slides(story_gen):
    news_list = [
        {"category": "ai", "title": "Haber 1", "summary_text": "Özet 1", "source_name": "TechCrunch"},
        {"category": "gaming", "title": "Haber 2", "summary_text": "Özet 2", "source_name": "IGN"},
    ]

    paths = story_gen.generate_daily_summary_stories(news_list)

    # kapak (1) + haber slaytları (2) + CTA (1) = 4
    assert len(paths) == 4
    for path in paths:
        with Image.open(path) as img:
            assert img.size == STORY_SIZE


def test_generate_daily_summary_stories_caps_at_five_news_slides(story_gen):
    news_list = [
        {"category": "ai", "title": f"Haber {i}", "summary_text": f"Özet {i}", "source_name": "Src"}
        for i in range(8)
    ]

    paths = story_gen.generate_daily_summary_stories(news_list)

    # kapak (1) + en fazla 5 haber slaytı + CTA (1) = 7
    assert len(paths) == 7
