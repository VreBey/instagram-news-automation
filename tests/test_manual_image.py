"""
src.manual_image birim testleri. Gerçek görsel render (ImageGenerator/
StoryGenerator) mock'lanır; MANUAL_IMAGE_CACHE_DIR pytest'in tmp_path'ine
yönlendirilerek gerçek assets/manual_cache/ dizinine yazılması önlenir.
"""

import src.manual_image as manual_image_module
from src.manual_image import apply_manual_image


def _make_content(db, content_type="post"):
    news_id = db.add_news(title="Test Haberi", url=f"https://example.com/{content_type}", category="ai")
    db.mark_news_processed(news_id, relevance_score=0.9)
    return db.add_content(news_id=news_id, content_type=content_type, caption="test")


def test_apply_manual_image_writes_file_and_updates_path(tmp_db, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(manual_image_module, "MANUAL_IMAGE_CACHE_DIR", tmp_path)
    content_id = _make_content(tmp_db, content_type="post")

    img_gen = mocker.Mock()
    img_gen.generate_post_image.return_value = "/fake/regenerated_post.png"
    story_gen = mocker.Mock()

    result = apply_manual_image(content_id, b"sahte-gorsel-verisi", tmp_db, img_gen, story_gen)

    assert result == "/fake/regenerated_post.png"
    dest_file = tmp_path / f"{content_id}.jpg"
    assert dest_file.exists()
    assert dest_file.read_bytes() == b"sahte-gorsel-verisi"

    content = tmp_db.get_content_by_id(content_id)
    assert content["manual_image_path"] == str(dest_file)


def test_apply_manual_image_regenerates_post_for_post_content(tmp_db, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(manual_image_module, "MANUAL_IMAGE_CACHE_DIR", tmp_path)
    content_id = _make_content(tmp_db, content_type="post")

    img_gen = mocker.Mock()
    img_gen.generate_post_image.return_value = "/fake/post.png"
    story_gen = mocker.Mock()

    apply_manual_image(content_id, b"data", tmp_db, img_gen, story_gen)

    img_gen.generate_post_image.assert_called_once_with(content_id=content_id)
    story_gen.generate_story_image.assert_not_called()


def test_apply_manual_image_regenerates_story_for_story_content(tmp_db, monkeypatch, tmp_path, mocker):
    """
    Gerçek olay riski: content_type kontrolü ('== "story"' değilse post
    varsayılır) yanlış üretim fonksiyonunu çağırabilir. Story içerik için
    generate_post_image DEĞİL generate_story_image çağrılmalı.
    """
    monkeypatch.setattr(manual_image_module, "MANUAL_IMAGE_CACHE_DIR", tmp_path)
    content_id = _make_content(tmp_db, content_type="story")

    img_gen = mocker.Mock()
    story_gen = mocker.Mock()
    story_gen.generate_story_image.return_value = "/fake/story.png"

    result = apply_manual_image(content_id, b"data", tmp_db, img_gen, story_gen)

    assert result == "/fake/story.png"
    story_gen.generate_story_image.assert_called_once_with(content_id=content_id)
    img_gen.generate_post_image.assert_not_called()


def test_apply_manual_image_returns_none_for_missing_content(tmp_db, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(manual_image_module, "MANUAL_IMAGE_CACHE_DIR", tmp_path)
    img_gen = mocker.Mock()
    story_gen = mocker.Mock()

    result = apply_manual_image(99999, b"data", tmp_db, img_gen, story_gen)

    assert result is None
    img_gen.generate_post_image.assert_not_called()
    story_gen.generate_story_image.assert_not_called()


def test_apply_manual_image_creates_cache_dir_if_missing(tmp_db, monkeypatch, tmp_path, mocker):
    cache_dir = tmp_path / "nested" / "manual_cache"
    monkeypatch.setattr(manual_image_module, "MANUAL_IMAGE_CACHE_DIR", cache_dir)
    content_id = _make_content(tmp_db, content_type="post")

    img_gen = mocker.Mock()
    img_gen.generate_post_image.return_value = "/fake/post.png"
    story_gen = mocker.Mock()

    apply_manual_image(content_id, b"data", tmp_db, img_gen, story_gen)

    assert (cache_dir / f"{content_id}.jpg").exists()
