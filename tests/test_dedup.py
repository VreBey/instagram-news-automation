"""Haber tekilleştirme (dedup) yardımcıları için birim testleri."""

from src.dedup import normalize_title, title_similarity


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("OpenAI, GPT-5'i Duyurdu!") == "openai gpt 5 i duyurdu"
    assert normalize_title("  Çoklu   Boşluk  ") == "çoklu boşluk"


def test_title_similarity_near_duplicate():
    a = "OpenAI GPT-5 Modelini Duyurdu"
    b = "OpenAI, GPT-5 modelini duyurdu!"
    assert title_similarity(a, b) > 0.9


def test_title_similarity_distinct_titles():
    a = "OpenAI GPT-5 Modelini Duyurdu"
    b = "GTA 6 Çıkış Tarihi Açıklandı"
    assert title_similarity(a, b) < 0.5


def test_find_similar_recent_detects_duplicate(tmp_db):
    tmp_db.add_news(
        title="OpenAI GPT-5 Modelini Duyurdu",
        url="https://example.com/a", category="ai"
    )

    dup_id, embedding = tmp_db.find_similar_recent(
        "OpenAI, GPT-5 modelini duyurdu!", category="ai",
        window_hours=72, threshold=0.82
    )
    assert dup_id is not None
    # Eşleşme SequenceMatcher basamağında bulundu — embedding hesaba hiç
    # gerek kalmadan hesaplanmamalı (gereksiz NVIDIA çağrısı olurdu).
    assert embedding is None


def test_find_similar_recent_no_match_for_distinct_title(tmp_db):
    tmp_db.add_news(
        title="OpenAI GPT-5 Modelini Duyurdu",
        url="https://example.com/b", category="ai"
    )

    # `try_embedding=False`: bu test SequenceMatcher basamağını ölçüyor,
    # ağa çıkmasın (autouse ağ engeli zaten None döndürür ama niyeti açık
    # yazmak daha doğru).
    dup_id, _ = tmp_db.find_similar_recent(
        "GTA 6 Çıkış Tarihi Açıklandı", category="ai",
        window_hours=72, threshold=0.82, try_embedding=False,
    )
    assert dup_id is None


def test_find_similar_recent_ignores_other_category(tmp_db):
    tmp_db.add_news(
        title="OpenAI GPT-5 Modelini Duyurdu",
        url="https://example.com/c", category="ai"
    )

    dup_id, _ = tmp_db.find_similar_recent(
        "OpenAI GPT-5 Modelini Duyurdu", category="gaming",
        window_hours=72, threshold=0.82
    )
    assert dup_id is None
