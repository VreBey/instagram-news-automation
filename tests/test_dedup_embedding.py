"""find_similar_recent'in embedding ikinci basamağı.

SequenceMatcher'ın kaçırdığı paraphrase'leri yakalayan yol. Ölçüm
(10 Ağustos 2026): aynı olayın farklı ifadeleri SequenceMatcher'da
0.40-0.49 (eşiğin 0.82'nin çok altında), embedding'de 0.71-0.84 skorluyordu.
"""

import json

import src.nvidia_embed as nvembed


def test_sequence_matcher_alone_misses_the_paraphrase(tmp_db):
    """
    Önce kanıt: bu iki başlık GERÇEKTEN SequenceMatcher'ın eşiğinin altında
    kalıyor (embedding'e neden ihtiyaç olduğunun kanıtı).
    """
    from src.dedup import title_similarity

    a = "OpenAI GPT-6'yı Duyurdu: Akıl Yürütmede Büyük Sıçrama"
    b = "Yeni Yapay Zeka Modeli Geldi: GPT-6, OpenAI'dan Mantık Yürütmede Dev Adım"
    assert title_similarity(a, b) < 0.82


def test_embedding_catches_what_sequence_matcher_misses(tmp_db, monkeypatch):
    """ASIL ÖZELLİK: embedding, SequenceMatcher'ın kaçırdığı dubleyi yakalar."""
    # Var olan haber, embedding'i ÖNCEDEN önbelleklenmiş olarak eklendi
    # (tıpkı add_news(embedding=...) ile gerçekte olacağı gibi).
    var_olan_vektor = [1.0, 0.0, 0.0]
    news_id = tmp_db.add_news(
        title="OpenAI GPT-6'yı Duyurdu: Akıl Yürütmede Büyük Sıçrama",
        url="https://example.com/gpt6-a", category="ai",
        embedding=var_olan_vektor,
    )

    # Yeni aday, PARAFRAZ ama SequenceMatcher'ı geçemeyecek kadar farklı
    # cümle yapısında. embed_text bu adayın vektörünü döndürüyor —
    # var olanla neredeyse aynı yönde (yüksek kosinüs benzerliği).
    monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: [0.9, 0.1, 0.1])

    dup_id, embedding = tmp_db.find_similar_recent(
        "Yeni Yapay Zeka Modeli Geldi: GPT-6, OpenAI'dan Mantık Yürütmede Dev Adım",
        category="ai", window_hours=72, threshold=0.82,
    )

    assert dup_id == news_id
    # Eşleşme bulundu, aday zaten eklenmeyecek — embedding'i saklamaya
    # gerek yok.
    assert embedding is None


def test_embedding_correctly_rejects_a_different_story(tmp_db, monkeypatch):
    """Negatif kontrol: gerçekten farklı bir haberi 'aynı' saymamalı."""
    tmp_db.add_news(
        title="Assassin's Creed Hexe bir yıldan önce çıkmayacak",
        url="https://example.com/ac-hexe", category="gaming",
        embedding=[1.0, 0.0, 0.0],
    )
    # Ortogonal vektör: kosinüs benzerliği 0, eşiğin (0.65) çok altında.
    monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: [0.0, 1.0, 0.0])

    dup_id, embedding = tmp_db.find_similar_recent(
        "Jharkhand'a CAMPA Fonu'ndan 5 yılda 3.310 crore aktarıldı",
        category="gaming", window_hours=72, threshold=0.82,
    )

    assert dup_id is None
    # Eşleşme yok, aday eklenecek — embedding SAKLANMAK üzere döner.
    assert embedding == [0.0, 1.0, 0.0]


def test_embedding_step_is_skipped_when_api_unavailable(tmp_db, monkeypatch):
    """
    NVIDIA API yanıt vermezse (varsayılan test durumu) tekilleştirme
    SADECE SequenceMatcher'a döner — pipeline durmaz, hata fırlamaz.
    """
    monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: None)
    tmp_db.add_news(
        title="Tamamen farklı bir haber",
        url="https://example.com/farkli", category="ai",
    )

    dup_id, embedding = tmp_db.find_similar_recent(
        "Alakasız başka bir başlık", category="ai",
        window_hours=72, threshold=0.82,
    )

    assert dup_id is None
    assert embedding is None


def test_embedding_step_skips_rows_without_cached_embedding(tmp_db, monkeypatch):
    """
    Eski satırların embedding'i NULL — bunlarla karşılaştırma yapılmaya
    çalışılıp çökmemeli, sadece atlanmalı.
    """
    tmp_db.add_news(
        title="Embedsiz eski bir haber", url="https://example.com/eski",
        category="ai",
    )
    monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: [1.0, 0.0])

    dup_id, embedding = tmp_db.find_similar_recent(
        "Yeni bir haber", category="ai", window_hours=72, threshold=0.82,
    )

    assert dup_id is None
    assert embedding == [1.0, 0.0]


def test_try_embedding_false_skips_the_second_pass(tmp_db, monkeypatch):
    """`try_embedding=False` embedding çağrısını hiç yapmamalı."""
    cagrildi = []
    monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: cagrildi.append(1))

    dup_id, embedding = tmp_db.find_similar_recent(
        "herhangi bir başlık", category="ai",
        window_hours=72, threshold=0.82, try_embedding=False,
    )

    assert dup_id is None
    assert embedding is None
    assert cagrildi == []


def test_add_news_stores_the_embedding(tmp_db):
    vektor = [0.1, 0.2, 0.3]
    news_id = tmp_db.add_news(
        title="Embedli haber", url="https://example.com/embedli",
        category="ai", embedding=vektor,
    )
    with tmp_db._get_connection() as c:
        ham = c.execute("SELECT embedding FROM news_items WHERE id=?",
                        (news_id,)).fetchone()[0]
    assert json.loads(ham) == vektor


def test_add_news_without_embedding_leaves_it_null(tmp_db):
    news_id = tmp_db.add_news(
        title="Embedsiz haber", url="https://example.com/embedsiz",
        category="ai",
    )
    with tmp_db._get_connection() as c:
        ham = c.execute("SELECT embedding FROM news_items WHERE id=?",
                        (news_id,)).fetchone()[0]
    assert ham is None


def test_is_duplicate_passes_embedding_through_for_new_items(tmp_db, monkeypatch):
    """
    news_collector._is_duplicate, YENİ (dubleks olmayan) bir aday için
    hesaplanan embedding'i çağırana geri döndürmeli — add_news'e geçirilip
    ikinci bir NVIDIA çağrısından kaçınılsın diye.
    """
    from src.news_collector import NewsCollector

    monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: [0.5, 0.5])
    nc = NewsCollector.__new__(NewsCollector)
    nc.db = tmp_db

    is_dup, embedding = nc._is_duplicate("Yeni bir haber başlığı", "ai")

    assert is_dup is False
    assert embedding == [0.5, 0.5]


def test_is_duplicate_returns_no_embedding_for_matched_items(tmp_db, monkeypatch):
    """Dubleks bulunduysa embedding hesabı israf — None dönmeli."""
    from src.news_collector import NewsCollector

    tmp_db.add_news(title="Var olan haber", url="https://example.com/var",
                    category="ai")
    nc = NewsCollector.__new__(NewsCollector)
    nc.db = tmp_db

    is_dup, embedding = nc._is_duplicate("Var olan haber", "ai")

    assert is_dup is True
    assert embedding is None
