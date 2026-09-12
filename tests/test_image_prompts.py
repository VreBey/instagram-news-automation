"""src.image_prompts birim testleri."""

from src.image_prompts import build_image_prompt


def test_build_image_prompt_uses_ai_style_for_ai_category():
    content = {"category": "ai", "news_title": "Yeni bir yapay zeka modeli duyuruldu"}
    prompt = build_image_prompt(content)

    assert "artificial-intelligence" in prompt
    assert "Yeni bir yapay zeka modeli duyuruldu" in prompt


def test_build_image_prompt_uses_gaming_style_for_gaming_category():
    content = {"category": "gaming", "news_title": "Yeni bir oyun duyuruldu"}
    prompt = build_image_prompt(content)

    assert "video-game-culture" in prompt
    assert "Yeni bir oyun duyuruldu" in prompt


def test_build_image_prompt_falls_back_to_ai_style_for_unknown_category():
    content = {"category": "unknown-category", "news_title": "Başlık"}
    prompt = build_image_prompt(content)

    assert "artificial-intelligence" in prompt


def test_build_image_prompt_defaults_to_ai_when_category_missing():
    content = {"news_title": "Kategori olmadan başlık"}
    prompt = build_image_prompt(content)

    assert "artificial-intelligence" in prompt


def test_build_image_prompt_prefers_summary_text_over_news_title():
    content = {
        "category": "gaming",
        "news_title": "İngilizce ham başlık",
        "summary_text": "Türkçe özet metni",
    }
    prompt = build_image_prompt(content)

    assert "Türkçe özet metni" in prompt
    assert "İngilizce ham başlık" not in prompt


def test_build_image_prompt_falls_back_to_news_title_without_summary():
    content = {"category": "gaming", "news_title": "İngilizce ham başlık"}
    prompt = build_image_prompt(content)

    assert "İngilizce ham başlık" in prompt


def test_build_image_prompt_always_forbids_real_logos_and_people():
    """
    Modülün tasarım amacı (telif/haklar riskini azaltmak): gerçek logo/marka/
    kişi asla istenmemeli, sadece özgün/stilize bir illüstrasyon. Bu kısıtlama
    her kategori için her zaman metinde yer almalı.
    """
    for category in ("ai", "gaming", "diger"):
        prompt = build_image_prompt({"category": category, "news_title": "x"})
        assert "real company logos" in prompt
        assert "real people" in prompt
        assert "No text or watermarks" in prompt
