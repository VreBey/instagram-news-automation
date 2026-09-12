"""
ContentProcessor için birim testleri. Gerçek Gemini API'ye hiçbir istek
atılmaz — GEMINI_API_KEY burada boşa zorlanıyor (geliştiricinin gerçek .env
dosyasında dolu bir anahtar olsa bile), böylece ContentProcessor her zaman
fallback (AI'sız) yolu kullanır. Bu olmadan test suite'i çalıştıran herkesin
gerçek Gemini kotasını harcadığı ve testlerin ağa bağımlı/kararsız hale
geldiği tespit edildi — bkz. logs/app.log'daki toplu 429 hataları.
"""

import pytest

import src.content_processor as content_processor_module
from src.content_processor import ContentProcessor


@pytest.fixture(autouse=True)
def _no_real_gemini_client(monkeypatch):
    monkeypatch.setattr(content_processor_module, "GEMINI_API_KEY", "")


def _news(title, description="", image_url=None, mention_count=1):
    return {
        "id": 1,
        "title": title,
        "description": description,
        "image_url": image_url,
        "mention_count": mention_count,
        "category": "ai",
        "source_name": "Test Kaynağı",
    }


def test_calculate_relevance_baseline(tmp_db):
    processor = ContentProcessor(db=tmp_db)
    score = processor._calculate_relevance(_news("Sıradan bir başlık"))
    assert 0.45 <= score <= 0.55


def test_calculate_relevance_high_interest_keywords_boost_score(tmp_db):
    processor = ContentProcessor(db=tmp_db)
    score = processor._calculate_relevance(_news("OpenAI yeni GPT-5 modelini duyurdu, çığır açan bir gelişme"))
    assert score > 0.5


def test_calculate_relevance_low_interest_keywords_reduce_score(tmp_db):
    processor = ContentProcessor(db=tmp_db)
    score = processor._calculate_relevance(
        _news("Opinion: Editorial review of an old rumor, sponsored ad")
    )
    assert score < 0.35


def test_calculate_relevance_mention_count_bonus(tmp_db):
    processor = ContentProcessor(db=tmp_db)
    base_score = processor._calculate_relevance(_news("Sıradan bir başlık", mention_count=1))
    boosted_score = processor._calculate_relevance(_news("Sıradan bir başlık", mention_count=4))
    assert boosted_score > base_score
    assert round(boosted_score - base_score, 2) == 0.15  # 3 kaynak sınırında capped bonus


def test_calculate_relevance_clamped_to_unit_interval(tmp_db):
    processor = ContentProcessor(db=tmp_db)
    title = "breakthrough launch release announce reveal exclusive first new major record"
    score = processor._calculate_relevance(_news(title, description="x" * 60, image_url="http://x", mention_count=10))
    assert 0.0 <= score <= 1.0


def test_process_all_news_skips_content_generation_below_min_score(tmp_db):
    tmp_db.add_news(
        title="Opinion: Editorial review of an old rumor, sponsored ad",
        url="https://example.com/low-score",
        category="ai",
    )

    processor = ContentProcessor(db=tmp_db)
    stats = processor.process_all_news()

    assert stats["processed"] == 1
    assert stats["posts"] == 0
    assert stats["stories"] == 0

    unprocessed = tmp_db.get_unprocessed_news()
    assert unprocessed == []


def _fake_response(text):
    resp = type("FakeResponse", (), {})()
    resp.text = text
    return resp


def test_generate_with_retry_returns_text_on_success(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(content_processor_module, "GEMINI_API_KEY", "fake-key")
    processor = ContentProcessor(db=tmp_db)
    processor.client = mocker.Mock()
    processor.client.models.generate_content.return_value = _fake_response("  merhaba  ")
    mocker.patch("src.content_processor.time.sleep")

    result = processor._generate_with_retry("prompt")

    assert result == "merhaba"
    processor.client.models.generate_content.assert_called_once_with(
        model=content_processor_module.GEMINI_MODEL_NAME, contents=["prompt"]
    )


def test_generate_with_retry_retries_then_succeeds_on_429(tmp_db, monkeypatch, mocker):
    from google.genai import errors as genai_errors

    monkeypatch.setattr(content_processor_module, "GEMINI_API_KEY", "fake-key")
    processor = ContentProcessor(db=tmp_db)
    processor.client = mocker.Mock()
    quota_error = genai_errors.ClientError(429, {"error": {"message": "quota"}})
    processor.client.models.generate_content.side_effect = [quota_error, _fake_response("ok")]
    mocker.patch("src.content_processor.time.sleep")

    result = processor._generate_with_retry("prompt", max_retries=2)

    assert result == "ok"
    assert processor.client.models.generate_content.call_count == 2


def test_generate_with_retry_gives_up_after_max_retries_on_429(tmp_db, monkeypatch, mocker):
    from google.genai import errors as genai_errors

    monkeypatch.setattr(content_processor_module, "GEMINI_API_KEY", "fake-key")
    processor = ContentProcessor(db=tmp_db)
    processor.client = mocker.Mock()
    quota_error = genai_errors.ClientError(429, {"error": {"message": "quota"}})
    processor.client.models.generate_content.side_effect = quota_error
    mocker.patch("src.content_processor.time.sleep")

    result = processor._generate_with_retry("prompt", max_retries=1)

    assert result is None
    assert processor.client.models.generate_content.call_count == 2  # ilk deneme + 1 retry


def test_generate_with_retry_retries_then_succeeds_on_503_server_error(tmp_db, monkeypatch, mocker):
    from google.genai import errors as genai_errors

    monkeypatch.setattr(content_processor_module, "GEMINI_API_KEY", "fake-key")
    processor = ContentProcessor(db=tmp_db)
    processor.client = mocker.Mock()
    server_error = genai_errors.ServerError(503, {"error": {"message": "unavailable"}})
    processor.client.models.generate_content.side_effect = [server_error, _fake_response("ok")]
    mocker.patch("src.content_processor.time.sleep")

    result = processor._generate_with_retry("prompt", max_retries=2)

    assert result == "ok"
    assert processor.client.models.generate_content.call_count == 2


def test_generate_with_retry_returns_none_on_non_quota_client_error(tmp_db, monkeypatch, mocker):
    from google.genai import errors as genai_errors

    monkeypatch.setattr(content_processor_module, "GEMINI_API_KEY", "fake-key")
    processor = ContentProcessor(db=tmp_db)
    processor.client = mocker.Mock()
    bad_request = genai_errors.ClientError(400, {"error": {"message": "invalid"}})
    processor.client.models.generate_content.side_effect = bad_request
    mocker.patch("src.content_processor.time.sleep")

    result = processor._generate_with_retry("prompt")

    assert result is None
    processor.client.models.generate_content.assert_called_once()


def test_process_all_news_without_gemini_produces_nothing(tmp_db):
    """
    Gemini yapılandırılmamışken HİÇBİR ŞEY üretilmez — ne gönderi ne hikâye.

    Haber yine de İŞLENİR (puanlanır, `is_processed` işaretlenir); üretilmeyen
    şey içerik.

    Üç şablon üreticisi de kaldırıldı (8 Ağustos 2026) çünkü üçü de ham
    `news["title"]` alanını kullanıcıya gösterilecek metne koyuyordu ve o
    başlık kaynak dilinde: havuzdaki 200 haberin 153'ü (%76) Türkçe değil.

    Hikâye şablonu emoji önekiyle dil kapısını GEÇİYORDU (canlıda 78 vaka,
    hepsi elle reddedilmişti). Gönderi şablonu geçmiyordu ama boşuna satır
    üretiyordu: son 14 günde özet taşıyan 883 içeriğin 194'ü (%22) kapıdan
    geçemedi ve atıldı, 35'i için görsel bile üretilmişti.
    """
    tmp_db.add_news(
        title="OpenAI GPT-5 Modelini Duyurdu: Yeni Bir Çığır",
        url="https://example.com/high-score",
        category="ai",
        description="Uzun ve detaylı bir açıklama metni burada yer alıyor.",
    )

    processor = ContentProcessor(db=tmp_db)
    assert processor.client is None, "bu test Gemini'siz yolu ölçüyor"
    stats = processor.process_all_news()

    assert stats["processed"] == 1
    assert stats["posts"] == 0
    assert stats["stories"] == 0



def test_parse_caption_response_handles_plain_format():
    text = "CAPTION:\nMerhaba dunya\nHASHTAGS: #a #b"
    result = ContentProcessor._parse_caption_response(text)
    assert result == {"caption": "Merhaba dunya", "hashtags": ["#a", "#b"]}


def test_parse_caption_response_handles_quotes_and_apostrophes_unescaped():
    text = ('CAPTION:\nOzellikle "day-one oyunlar artik bitiyor mu" tartismalari '
            "Sony'nin aciklamasiyla basladi.\nHASHTAGS: #xbox #sony")
    result = ContentProcessor._parse_caption_response(text)
    assert result is not None
    assert 'day-one oyunlar artik bitiyor mu' in result["caption"]
    assert "Sony'nin" in result["caption"]
    assert result["hashtags"] == ["#xbox", "#sony"]


def test_parse_caption_response_recovers_caption_without_hashtags_line():
    result = ContentProcessor._parse_caption_response("CAPTION:\nSadece caption metni burada")
    assert result == {"caption": "Sadece caption metni burada", "hashtags": []}


def test_parse_caption_response_returns_none_for_garbage():
    assert ContentProcessor._parse_caption_response("bu hic uygun format degil") is None


# --- Hashtag stratejisi (Instagram 2026: 3-5 nis etiket) ---

def test_filter_hashtags_caps_at_five():
    tags = [f"#tag{i}" for i in range(12)]
    assert len(ContentProcessor._filter_hashtags(tags)) == 5


def test_filter_hashtags_drops_engagement_bait():
    """
    "#kesfet", "#takipet", "#viral" gibi etiketler erisime katki saglamiyor
    ve hesabi spam gibi gosteriyor — prompt yasaklasa da model uretirse
    kodda ayiklanmali.
    """
    tags = ["#EldenRing", "#kesfet", "#takipet", "#viral", "#FromSoftware"]
    assert ContentProcessor._filter_hashtags(tags) == ["#EldenRing", "#FromSoftware"]


def test_filter_hashtags_removes_case_insensitive_duplicates():
    tags = ["#Gaming", "#gaming", "#GAMING", "#oyun"]
    assert ContentProcessor._filter_hashtags(tags) == ["#Gaming", "#oyun"]


def test_parse_caption_response_applies_hashtag_limit():
    text = "CAPTION:\nMetin\nHASHTAGS: #a #b #c #d #e #f #g #kesfet"
    result = ContentProcessor._parse_caption_response(text)
    assert result["hashtags"] == ["#a", "#b", "#c", "#d", "#e"]


def test_default_hashtags_stay_within_limit_and_are_clean():
    for category in ("ai", "gaming"):
        for is_reels in (False, True):
            tags = ContentProcessor._default_hashtags(category, is_reels=is_reels)
            assert len(tags) <= ContentProcessor.MAX_HASHTAGS
            lowered = {t.lstrip("#").lower() for t in tags}
            assert not (lowered & ContentProcessor._BANNED_HASHTAGS)


def test_parse_reels_script_response_extracts_intro_segments_outro():
    text = (
        "INTRO: Bugunun en onemli haberleri!\n"
        "SEGMENT 1: Haber bir metni.\n"
        "SEGMENT 2: Haber iki metni.\n"
        "OUTRO: Takip et!"
    )
    result = ContentProcessor._parse_reels_script_response(text)
    # Sözlüğün TAMAMI değil ilgili alanlar karşılaştırılıyor: parser ayrıca
    # `segment_indices` döndürüyor (segmentin hangi habere ait olduğu) ve tam
    # eşitlik her yeni alanda bu testi sebepsiz kırardı.
    assert result["intro"] == "Bugunun en onemli haberleri!"
    assert result["segments"] == ["Haber bir metni.", "Haber iki metni."]
    assert result["outro"] == "Takip et!"


def test_parse_reels_script_response_returns_none_without_segments():
    assert ContentProcessor._parse_reels_script_response("INTRO: sadece giris var") is None


def test_parse_list_content_response_extracts_cover_and_items():
    text = (
        "LISTE: EVET\n"
        "KAPAK: Ağustos'un en büyük oyunları!\n"
        "OGE 1: Beast of Reincarnation | 5 Ağustos'ta PS5 ve PC'de çıkıyor\n"
        "OGE 2: Star Wars: Zero Company | 6 Ağustos'ta duyuruldu\n"
    )
    result = ContentProcessor._parse_list_content_response(text)

    assert result == {
        "cover": "Ağustos'un en büyük oyunları!",
        "items": [
            {"name": "Beast of Reincarnation", "detail": "5 Ağustos'ta PS5 ve PC'de çıkıyor"},
            {"name": "Star Wars: Zero Company", "detail": "6 Ağustos'ta duyuruldu"},
        ],
    }


def test_parse_list_content_response_returns_none_when_not_a_list():
    assert ContentProcessor._parse_list_content_response("LISTE: HAYIR") is None


def test_parse_list_content_response_returns_none_with_fewer_than_two_items():
    text = "LISTE: EVET\nKAPAK: Tek haber\nOGE 1: Tek öge | tek detay\n"
    assert ContentProcessor._parse_list_content_response(text) is None


def test_ai_detect_list_content_returns_none_when_gemini_unavailable(tmp_db, mocker):
    """client=None (GEMINI_API_KEY boş) durumunda _generate_with_retry zaten
    None döner — liste tespiti sessizce devre dışı kalmalı, hata fırlatmamalı."""
    processor = ContentProcessor(db=tmp_db)
    assert processor.client is None

    result = processor._ai_detect_list_content(_news("Herhangi bir haber"))

    assert result is None



def test_ai_generate_story_applies_same_headline_normalization_as_posts(tmp_db, monkeypatch, mocker):
    """
    Gerçek kalite kusuru (21 Ağustos 2026): hikaye başlığı, post manşetiyle
    (_ai_summarize) TAM AYNI görsel rolü oynuyor (görselin üzerine basılan
    TEK başlık) ama kendi gevşek "140 karakter" kuralını kullanıyordu.
    Canlı ölçüm: aynı turda üretilen 6 story başlığının HEPSİ 83-107
    karakterdi (post'lar 37-57 karakterle hedefin içindeydi). Artık
    _normalize_manset'ten (post'la AYNI 60/100 karakter hedefi) geçmeli.
    """
    monkeypatch.setattr(content_processor_module, "GEMINI_API_KEY", "fake-key")
    processor = ContentProcessor(db=tmp_db)
    processor.client = mocker.Mock()
    long_text = (
        "Ağustos 2026'da Beast of Reincarnation duyuruldu! Marvel Tokon "
        "Fighting Souls ve Star Wars Zero Company gibi dev yapımlar da "
        "art arda çıkıyor, bu ay PS5 ve Switch 2 için kaçırılmamalı."
    )
    processor.client.models.generate_content.return_value = _fake_response(long_text)
    mocker.patch("src.content_processor.time.sleep")

    result = processor._ai_generate_story(_news("Ağustos 2026 Oyunları"))

    assert result == "Ağustos 2026'da Beast of Reincarnation duyuruldu!"
    assert len(result) <= ContentProcessor._MANSET_TAVAN


def test_process_all_news_limit_derives_from_media_quota(tmp_db, mocker, monkeypatch):
    """
    Canlı ölçüm: 657 içerik üretilmiş ama yalnızca %30'unun medyası
    hazırlanabilmiş. Sabit limit 30 iken medya kotasının kapsayamayacağı
    haberler için de Gemini'ye caption ürettiriliyordu — boşa harcanan kota.
    İşlenecek haber sayısı artık kotadan türetilmeli.
    """
    monkeypatch.setattr("src.content_processor.DAILY_POST_LIMIT", 2)
    monkeypatch.setattr("src.content_processor.DAILY_STORY_LIMIT", 5)
    monkeypatch.setattr("src.content_processor.MEDIA_GENERATION_MULTIPLIER", 3)

    cp = ContentProcessor(db=tmp_db)
    spy = mocker.patch.object(tmp_db, "get_unprocessed_news", return_value=[])

    cp.process_all_news()

    assert spy.call_args.kwargs["limit"] == 15  # max(2*3, 5*3)
