"""
src.news_collector birim testleri. Gerçek RSS/NewsAPI/Currents ağ çağrısı
yapılmaz — feedparser.parse ve requests.get/session.get mock'lanır.
"""

import src.news_collector as news_collector_module
from src.news_collector import NewsCollector


class _Entry(dict):
    """feedparser'ın FeedParserDict'i gibi hem .get() hem öznitelik erişimini
    destekleyen basit bir test çifti (hasattr(entry, 'media_content') vb.
    kontroller için gerekli)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class _Feed:
    def __init__(self, entries, bozo=False):
        self.entries = entries
        self.bozo = bozo


def _fake_response(mocker, json_data, status=None, raise_exc=None):
    resp = mocker.Mock()
    if raise_exc:
        resp.raise_for_status.side_effect = raise_exc
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


def _fake_rss_response(mocker, status_code=200, headers=None):
    """_parse_rss_feed artık feed'i feedparser.parse(url) yerine önce
    session.get() ile çekip içeriği feedparser'a veriyor (bkz. news_collector.py
    — böylece doğru User-Agent/timeout/koşullu-GET uygulanabiliyor). Testler bu
    yüzden feedparser.parse'ın YANI SIRA session.get'i de mock'lamalı."""
    resp = mocker.Mock()
    resp.status_code = status_code
    resp.content = b"<rss></rss>"
    resp.headers = headers or {}
    return resp


# =============================================
# _clean_text / _strip_html / _extract_image
# =============================================

def test_clean_text_collapses_whitespace():
    assert NewsCollector._clean_text("  bir   haber   \n başlığı  ") == "bir haber başlığı"


def test_clean_text_handles_empty():
    assert NewsCollector._clean_text("") == ""
    assert NewsCollector._clean_text(None) == ""


def test_clean_source_name_strips_css_artifacts():
    # Gerçek olay: Currents API bir Flipboard makalesinde "author" alanına
    # kırık bir inline CSS parçası koymuştu.
    assert NewsCollector._clean_source_name("Css-; Object-Fitcover; Border-Radius; Border; 0") == ""


def test_clean_source_name_keeps_real_names():
    assert NewsCollector._clean_source_name("TechCrunch") == "TechCrunch"
    assert NewsCollector._clean_source_name("") == ""


def test_strip_html_removes_tags_and_entities():
    raw = "<p>Merhaba &amp; ho&#39;geldin &nbsp;dünya</p>"
    assert NewsCollector._strip_html(raw) == "Merhaba & ho'geldin  dünya"


def test_extract_image_from_media_thumbnail():
    entry = _Entry(media_thumbnail=[{"url": "https://example.com/thumb.jpg"}])
    assert NewsCollector._extract_image(entry) == "https://example.com/thumb.jpg"


def test_extract_image_from_enclosures():
    entry = _Entry(enclosures=[{"type": "image/jpeg", "href": "https://example.com/enc.jpg"}])
    assert NewsCollector._extract_image(entry) == "https://example.com/enc.jpg"


def test_extract_image_returns_none_when_missing():
    entry = _Entry()
    assert NewsCollector._extract_image(entry) is None


# =============================================
# _is_gaming_relevant
# =============================================

def test_is_gaming_relevant_true_for_platform_keyword():
    assert NewsCollector._is_gaming_relevant("New PlayStation exclusive announced") is True


def test_is_gaming_relevant_true_for_known_franchise_in_description():
    assert NewsCollector._is_gaming_relevant("Big update coming", "Fortnite adds a new map") is True


def test_is_gaming_relevant_false_for_sports_or_tv_content():
    assert NewsCollector._is_gaming_relevant("Calgary Surge defeat Niagara River in season finale") is False
    assert NewsCollector._is_gaming_relevant("The Legend of Vox Machina Star Reveals Season 4 Details") is False


# =============================================
# _is_ai_relevant
# =============================================

def test_is_ai_relevant_true_for_standalone_ai_word():
    assert NewsCollector._is_ai_relevant("IIT Kanpur launches course in AI, machine learning") is True


def test_is_ai_relevant_true_for_known_product_keyword():
    assert NewsCollector._is_ai_relevant("OpenAI cuts prices on smaller models") is True


def test_is_ai_relevant_true_for_ai_security_terms():
    # Ars Technica gibi kaynaklar genel siber güvenlik haberlerini de "AI"
    # etiketiyle veriyor; çoğu alakasız ama "prompt injection" gibi LLM'e
    # özel terimler gerçekten AI güvenliğiyle ilgili, kaçırılmamalı.
    assert NewsCollector._is_ai_relevant("Now, defenders are embracing the prompt injection, too") is True


def test_is_ai_relevant_false_for_generic_security_news_mislabeled_ai():
    assert NewsCollector._is_ai_relevant(
        "The US government warns that Russia state hackers are coming after your router"
    ) is False


def test_is_ai_relevant_false_for_unrelated_sports_content():
    assert NewsCollector._is_ai_relevant("Barça give the green light, Ter Stegen joins Ajax") is False
    assert NewsCollector._is_ai_relevant("FIFA lose another ally, CONCACAF reject Infantino's plan") is False


def test_is_ai_relevant_does_not_false_positive_on_substring():
    # "fair", "again", "explain" gibi kelimeler "ai" alt dizisini içerir ama
    # kelime sınırı kontrolü sayesinde yanlış eşleşmemeli.
    assert NewsCollector._is_ai_relevant("It's fair to say this deal remains unclear again") is False


# =============================================
# _is_pr_or_ad_content
# =============================================

def test_is_pr_or_ad_content_true_for_wire_source():
    assert NewsCollector._is_pr_or_ad_content(
        "S99 PR Expands Premium Media Services", "", "GlobeNewswire"
    ) is True


def test_is_pr_or_ad_content_true_for_ad_phrase():
    assert NewsCollector._is_pr_or_ad_content(
        "Lifetime access to 20+ popular AI models is only $79", "", "SomeBlog"
    ) is True


def test_is_pr_or_ad_content_false_for_real_news():
    assert NewsCollector._is_pr_or_ad_content(
        "OpenAI releases new model with better reasoning", "", "TechCrunch"
    ) is False


def test_is_pr_or_ad_content_true_for_syndicated_wire_dateline():
    # PR bülteni başka bir sitece (source_name artık wire servisi değil)
    # alıntılanmış ama açıklamada hâlâ orijinal dateline var.
    assert NewsCollector._is_pr_or_ad_content(
        "Relief AI Inc. Provides Update on Subscription Receipts",
        "TORONTO, July 30, 2026 (GLOBE NEWSWIRE) — Relief AI Inc. announced...",
        "Financial Post",
    ) is True


def test_is_gaming_relevant_false_for_gambling_content_despite_gaming_word():
    assert NewsCollector._is_gaming_relevant(
        "Lightning Link High Stakes Lands at BetMGM Casino Alberta",
        "One of the most recognizable slot brands joins the iGaming operator",
    ) is False


def test_is_gaming_relevant_true_for_ps6_and_madden():
    assert NewsCollector._is_gaming_relevant("PS6 without discs: is Sony going too far?") is True
    assert NewsCollector._is_gaming_relevant("What are Giants player ratings in EA Sports Madden 27?") is True


def test_is_gaming_relevant_does_not_false_positive_on_bare_switch():
    # Bare "switch" kelimesi "switching contexts" gibi tamamen alakasız
    # metinlerde de eşleşiyordu (bkz. "Best Mac Launcher Apps" sızıntısı).
    assert NewsCollector._is_gaming_relevant(
        "Best Mac Launcher Apps for Productivity",
        "I realized how much time I was wasting just switching contexts.",
    ) is False


def test_is_gaming_relevant_true_for_switch_2_with_context():
    assert NewsCollector._is_gaming_relevant("Switch 2 officially has 5 must-play new games releasing in August") is True


def test_is_gaming_relevant_true_for_publisher_and_platform_names():
    # Belirli oyun başlığı içermeyen ama gerçek oyun gazeteciliği olan
    # haberler — yayıncı/platform adıyla kurtarılıyor.
    assert NewsCollector._is_gaming_relevant(
        "Ubisoft's NFT tactics game, a thing that still exists, is shutting down"
    ) is True
    assert NewsCollector._is_gaming_relevant(
        "Twitch CEO on Why Community and Longer Videos Are Critical"
    ) is True


def test_is_gaming_relevant_true_for_genre_terms_without_known_franchise():
    """
    Gerçek kullanıcı şikayeti: "yeni çıkacak bir oyun", "popüler bir MMORPG/
    FPS oyununa gelen güncelleme", "yeni çıkacak rol yapma/simülasyon
    oyunları" gibi içerikler hiç görünmüyordu. Eskiden filtre SABİT bir
    franchise listesine dayanıyordu — listede olmayan (ör. yeni duyurulan)
    bir MMORPG/FPS/simülasyon oyunu, tür adı içermesine rağmen eleniyordu.
    """
    assert NewsCollector._is_gaming_relevant(
        "New MMORPG announced for 2027, developer promises massive open world"
    ) is True
    assert NewsCollector._is_gaming_relevant(
        "Upcoming FPS game gets first gameplay trailer ahead of release date"
    ) is True
    assert NewsCollector._is_gaming_relevant(
        "New life sim announced, a farming simulator with city builder elements"
    ) is True
    assert NewsCollector._is_gaming_relevant(
        "New mobile game tops charts within days of launch on iOS and Android"
    ) is True


def test_is_gaming_relevant_true_for_mmorpg_and_fps_franchise_updates():
    assert NewsCollector._is_gaming_relevant("Final Fantasy XIV gets a big new patch this week") is True
    assert NewsCollector._is_gaming_relevant("Battlefield update fixes major netcode issues") is True
    assert NewsCollector._is_gaming_relevant("Escape from Tarkov wipe hits servers next week") is True


def test_is_gaming_relevant_false_for_unrelated_rpg_and_fallout_word_collisions():
    """
    "RPG" askeri bağlamda "rocket-propelled grenade" kısaltması, "fallout"
    ise gündelik İngilizcede "sonuç/yansıma" anlamında kullanılıyor — bu
    yüzden bilerek yalın olarak listeye eklenmedi (bkz. "switch" hatası).
    """
    assert NewsCollector._is_gaming_relevant(
        "Soldiers reportedly fired an RPG at the convoy during the ambush"
    ) is False
    assert NewsCollector._is_gaming_relevant(
        "Political fallout continues after the minister's resignation"
    ) is False


def test_is_pr_or_ad_content_true_for_slash_delimited_dateline():
    # PRNewswire her zaman parantez kullanmıyor — "/PRNewswire/" gibi eğik
    # çizgili dateline'lar da (bkz. "AccuFACE 2" basın bülteni) yakalanmalı.
    assert NewsCollector._is_pr_or_ad_content(
        "Reallusion Launches AccuFACE 2",
        "SAN JOSE, Calif., July 30, 2026 /PRNewswire/ -- Reallusion today announced...",
        "Some Aggregator Site",
    ) is True


# =============================================
# _is_financial_advice_content (Türkiye SPK/reklam mevzuatı riski)
# =============================================

def test_is_financial_advice_content_true_for_known_source():
    assert NewsCollector._is_financial_advice_content(
        "Bloom Energy Is Soaring: Is There a Better Way To Play The AI Power Boom?",
        "", "The Motley Fool",
    ) is True


def test_is_financial_advice_content_true_for_options_trading_keyword():
    assert NewsCollector._is_financial_advice_content(
        "Investors Purchase Large Volume of Call Options on Western Union", "", "MarketBeat",
    ) is True


def test_is_financial_advice_content_false_for_real_ai_news():
    assert NewsCollector._is_financial_advice_content(
        "OpenAI releases new model with better reasoning", "", "TechCrunch",
    ) is False


def test_is_financial_advice_content_true_for_investing_com_source():
    assert NewsCollector._is_financial_advice_content(
        "European stocks: Is the AI momentum unwind coming to an end?", "", "Investing.com",
    ) is True


def test_is_financial_advice_content_true_when_source_is_byline_in_title():
    # Currents API'de source_name genelde yazarın adı oluyor, yayıncı adı
    # metnin (başlığın) içine gömülü kalıyor — bkz. gerçek olay.
    assert NewsCollector._is_financial_advice_content(
        "European stocks: Is the AI momentum unwind coming to an end? By Investing.com",
        "European stocks: Is the AI momentum unwind coming to an end?",
        "Navamya Acharya",
    ) is True


def test_is_financial_advice_content_true_for_investor_move_framing():
    assert NewsCollector._is_financial_advice_content(
        "Why high-conviction investor Cathie Wood bought Nvidia after it erased billions",
        "", "The Times of India",
    ) is True


def test_is_financial_advice_content_true_for_stock_market_recap():
    assert NewsCollector._is_financial_advice_content(
        "Stock market today: Dow, S&P 500, Nasdaq gain as Big Tech AI spending", "", "Yahoo Entertainment",
    ) is True
    assert NewsCollector._is_financial_advice_content(
        "AI Isn't a Catch-All Trade for Stocks in This Earnings Season", "", "Bloomberg News",
    ) is True


def test_is_gaming_relevant_false_for_illegal_betting_brand_despite_gaming_word():
    assert NewsCollector._is_gaming_relevant(
        "Lightning Link High Stakes Lands at Casibom", "kaçak bahis sitesi gaming platformu",
    ) is False


# =============================================
# _is_unreliable_source (hiciv/yanlış bilgi kaynakları)
# =============================================

def test_is_unreliable_source_true_for_satire_site():
    # Gerçek olay: Thedailymash.co.uk'nin Elon Musk hakkında uydurma
    # "haberi" konu filtresini geçti, Gemini hiciv olduğunu anlamadan
    # uydurma detaylarla gerçek habermiş gibi Türkçe caption üretti.
    assert NewsCollector._is_unreliable_source("Thedailymash.co.uk") is True


def test_is_unreliable_source_true_for_misinformation_site():
    assert NewsCollector._is_unreliable_source("Naturalnews.com") is True


def test_is_unreliable_source_false_for_reputable_outlet():
    assert NewsCollector._is_unreliable_source("TechCrunch - AI") is False
    assert NewsCollector._is_unreliable_source("") is False


# =============================================
# _parse_rss_feed
# =============================================

def test_parse_rss_feed_adds_new_entries(tmp_db, mocker):
    collector = NewsCollector(db=tmp_db)
    entry = _Entry(
        title="OpenAI Yeni Model Duyurdu",
        link="https://example.com/haber-1",
        summary="<p>Kısa açıklama</p>",
    )
    mocker.patch.object(collector.session, "get", return_value=_fake_rss_response(mocker))
    mocker.patch("src.news_collector.feedparser.parse", return_value=_Feed([entry]))

    count = collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "ai")

    assert count == 1
    news = tmp_db.get_unprocessed_news(category="ai")
    assert len(news) == 1
    assert news[0]["title"] == "OpenAI Yeni Model Duyurdu"
    assert news[0]["description"] == "Kısa açıklama"


def test_parse_rss_feed_skips_entries_without_title_or_link(tmp_db, mocker):
    collector = NewsCollector(db=tmp_db)
    entries = [_Entry(title="", link="https://example.com/x"), _Entry(title="Başlık var", link="")]
    mocker.patch.object(collector.session, "get", return_value=_fake_rss_response(mocker))
    mocker.patch("src.news_collector.feedparser.parse", return_value=_Feed(entries))

    count = collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "ai")
    assert count == 0


def test_parse_rss_feed_returns_zero_on_bozo_with_no_entries(tmp_db, mocker):
    collector = NewsCollector(db=tmp_db)
    mocker.patch.object(collector.session, "get", return_value=_fake_rss_response(mocker))
    mocker.patch("src.news_collector.feedparser.parse", return_value=_Feed([], bozo=True))

    count = collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "ai")
    assert count == 0


def test_parse_rss_feed_skips_irrelevant_gaming_entries(tmp_db, mocker):
    collector = NewsCollector(db=tmp_db)
    entries = [
        _Entry(title="Calgary Surge defeat Niagara River in season finale", link="https://example.com/sports"),
        _Entry(title="New PlayStation 5 exclusive announced", link="https://example.com/gaming"),
    ]
    mocker.patch.object(collector.session, "get", return_value=_fake_rss_response(mocker))
    mocker.patch("src.news_collector.feedparser.parse", return_value=_Feed(entries))

    count = collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "gaming")

    assert count == 1
    news = tmp_db.get_unprocessed_news(category="gaming")
    assert len(news) == 1
    assert news[0]["title"] == "New PlayStation 5 exclusive announced"


def test_parse_rss_feed_skips_duplicates(tmp_db, mocker):
    collector = NewsCollector(db=tmp_db)
    tmp_db.add_news(title="OpenAI Yeni Model Duyurdu", url="https://example.com/original", category="ai")
    entry = _Entry(title="OpenAI Yeni Model Duyurdu", link="https://example.com/haber-1")
    mocker.patch.object(collector.session, "get", return_value=_fake_rss_response(mocker))
    mocker.patch("src.news_collector.feedparser.parse", return_value=_Feed([entry]))

    count = collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "ai")
    assert count == 0


def test_parse_rss_feed_sends_identifying_user_agent(tmp_db, mocker):
    """
    Gerçek kullanıcı sorusu: RSS kaynakları bizi bir saldırı gibi algılayıp
    engellemesin diye kimliğimizi açıkça belirten (+URL içeren) bir
    User-Agent gönderilmeli — kimliksiz/varsayılan bir bot dizesi WAF'lar
    tarafından şüpheli görülüp engellenmeye daha yatkın.

    Ad ve iletişim URL'si artık .env'den geliyor (BOT_NAME / BOT_CONTACT_URL);
    bu test marka adını değil DAVRANIŞI doğrular — kimlik yapılandırmadan
    okunmalı ve başlık iletişim kurulabilir bir +URL taşımalı.
    """
    from config import BOT_NAME, BOT_CONTACT_URL

    collector = NewsCollector(db=tmp_db)
    ua = collector.session.headers["User-Agent"]

    # Kimlik koda gömülü değil, yapılandırmadan geliyor
    assert BOT_NAME in ua
    assert BOT_CONTACT_URL in ua
    # Site yöneticisinin bize ulaşabileceği bir URL taşımalı
    assert "+http" in ua
    # Tarayıcı taklidi olmamalı — engellenme sebebi tam olarak budur
    assert "Mozilla" not in ua


def test_parse_rss_feed_skips_parsing_on_304_not_modified(tmp_db, mocker):
    """
    Koşullu GET (ETag/If-Modified-Since) sayesinde kaynak "304 Not Modified"
    dönerse feed hiç değişmemiş demektir — tekrar indirip ayrıştırmaya gerek
    yok. Bu hem bizim hem kaynağın yükünü azaltır, düzenli/öngörülebilir bir
    istemci gibi davranmamızı sağlar.
    """
    collector = NewsCollector(db=tmp_db)
    get_mock = mocker.patch.object(collector.session, "get", return_value=_fake_rss_response(mocker, status_code=304))
    parse_mock = mocker.patch("src.news_collector.feedparser.parse")

    count = collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "ai")

    assert count == 0
    parse_mock.assert_not_called()
    get_mock.assert_called_once()


def test_parse_rss_feed_backs_off_gracefully_on_403_and_429(tmp_db, mocker):
    """Kaynak bizi engelliyor/hız sınırı uyguluyorsa (403/429) sessizce
    atlanmalı — tekrar tekrar denemek "saldırgan" bir davranış izlenimi
    doğurabilir."""
    collector = NewsCollector(db=tmp_db)
    for status in (403, 429):
        mocker.patch.object(collector.session, "get", return_value=_fake_rss_response(mocker, status_code=status))
        parse_mock = mocker.patch("src.news_collector.feedparser.parse")

        count = collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "ai")

        assert count == 0
        parse_mock.assert_not_called()


def test_parse_rss_feed_stores_etag_for_future_conditional_requests(tmp_db, mocker):
    """Başarılı bir yanıttan gelen ETag, bir sonraki istekte If-None-Match
    olarak gönderilebilmesi için kalıcı olarak saklanmalı."""
    collector = NewsCollector(db=tmp_db)
    mocker.patch.object(
        collector.session, "get",
        return_value=_fake_rss_response(mocker, headers={"ETag": '"abc123"'})
    )
    mocker.patch("src.news_collector.feedparser.parse", return_value=_Feed([]))

    collector._parse_rss_feed("https://feed.example.com/rss", "TestKaynak", "ai")

    assert tmp_db.get_setting("rss_etag:https://feed.example.com/rss") == '"abc123"'


# =============================================
# _collect_from_newsapi
# =============================================

def test_collect_from_newsapi_returns_zero_without_key(tmp_db, monkeypatch):
    monkeypatch.setattr(news_collector_module, "NEWS_API_KEY", "")
    collector = NewsCollector(db=tmp_db)
    assert collector._collect_from_newsapi() == 0


def test_collect_from_newsapi_adds_articles(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(news_collector_module, "NEWS_API_KEY", "fake-key")
    monkeypatch.setattr(news_collector_module, "NEWS_API_QUERIES", {"ai": ["artificial intelligence"]})
    collector = NewsCollector(db=tmp_db)
    mocker.patch("src.news_collector.time.sleep")

    response_data = {
        "status": "ok",
        "articles": [
            {"title": "Yeni AI Modeli", "url": "https://example.com/a1",
             "description": "desc", "source": {"name": "TechSite"}, "urlToImage": None,
             "publishedAt": "2026-07-30T00:00:00Z"},
            {"title": "[Removed]", "url": "https://example.com/removed"},
        ],
    }
    mocker.patch.object(collector.session, "get", return_value=_fake_response(mocker, response_data))

    count = collector._collect_from_newsapi()

    assert count == 1
    news = tmp_db.get_unprocessed_news(category="ai")
    assert len(news) == 1
    assert news[0]["title"] == "Yeni AI Modeli"


def test_collect_from_newsapi_skips_irrelevant_gaming_articles(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(news_collector_module, "NEWS_API_KEY", "fake-key")
    monkeypatch.setattr(news_collector_module, "NEWS_API_QUERIES", {"gaming": ["esports"]})
    collector = NewsCollector(db=tmp_db)
    mocker.patch("src.news_collector.time.sleep")

    response_data = {
        "status": "ok",
        "articles": [
            {"title": "The Legend of Vox Machina Star Reveals Season 4 Details",
             "url": "https://example.com/tv1", "description": "", "source": {"name": "Polygon"}},
            {"title": "Fortnite Chapter 6 adds new map", "url": "https://example.com/g1",
             "description": "", "source": {"name": "IGN"}},
        ],
    }
    mocker.patch.object(collector.session, "get", return_value=_fake_response(mocker, response_data))

    count = collector._collect_from_newsapi()

    assert count == 1
    news = tmp_db.get_unprocessed_news(category="gaming")
    assert len(news) == 1
    assert news[0]["title"] == "Fortnite Chapter 6 adds new map"


def test_collect_from_newsapi_handles_non_ok_status(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(news_collector_module, "NEWS_API_KEY", "fake-key")
    monkeypatch.setattr(news_collector_module, "NEWS_API_QUERIES", {"ai": ["artificial intelligence"]})
    collector = NewsCollector(db=tmp_db)
    mocker.patch("src.news_collector.time.sleep")
    mocker.patch.object(
        collector.session, "get",
        return_value=_fake_response(mocker, {"status": "error", "message": "rate limited"}),
    )

    assert collector._collect_from_newsapi() == 0


def test_collect_from_newsapi_handles_request_exception(tmp_db, monkeypatch, mocker):
    import requests

    monkeypatch.setattr(news_collector_module, "NEWS_API_KEY", "fake-key")
    monkeypatch.setattr(news_collector_module, "NEWS_API_QUERIES", {"ai": ["artificial intelligence"]})
    collector = NewsCollector(db=tmp_db)
    mocker.patch.object(collector.session, "get", side_effect=requests.RequestException("boom"))

    assert collector._collect_from_newsapi() == 0


# =============================================
# _collect_from_currents
# =============================================

def test_collect_from_currents_returns_zero_without_key(tmp_db, monkeypatch):
    monkeypatch.setattr(news_collector_module, "CURRENTS_API_KEY", "")
    collector = NewsCollector(db=tmp_db)
    assert collector._collect_from_currents() == 0


def test_collect_from_currents_adds_articles(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(news_collector_module, "CURRENTS_API_KEY", "fake-key")
    collector = NewsCollector(db=tmp_db)
    mocker.patch("src.news_collector.time.sleep")

    response_data = {
        "news": [
            {"title": "GTA 6 Haberi", "url": "https://example.com/g1",
             "description": "desc", "author": "IGN", "image": None,
             "published": "2026-07-30 00:00:00 +0000", "language": "en"},
        ]
    }
    mocker.patch.object(collector.session, "get", return_value=_fake_response(mocker, response_data))

    count = collector._collect_from_currents()

    # "ai" ve "gaming" arama terimleri ayni mock response'u (ayni URL) dondurdugu
    # icin ikinci ekleme add_news()'in URL benzersizligine takilip None doner.
    assert count == 1


# =============================================
# collect_all
# =============================================

def test_collect_all_skips_newsapi_and_currents_without_keys(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(news_collector_module, "NEWS_API_KEY", "")
    monkeypatch.setattr(news_collector_module, "CURRENTS_API_KEY", "")
    collector = NewsCollector(db=tmp_db)
    mocker.patch.object(collector, "_collect_from_rss", return_value=3)
    newsapi_mock = mocker.patch.object(collector, "_collect_from_newsapi")
    currents_mock = mocker.patch.object(collector, "_collect_from_currents")

    stats = collector.collect_all()

    assert (stats["rss"], stats["newsapi"], stats["currents"], stats["total"]) == (3, 0, 0, 3)
    newsapi_mock.assert_not_called()
    currents_mock.assert_not_called()


def test_collect_all_calls_all_sources_when_keys_present(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(news_collector_module, "NEWS_API_KEY", "fake-key")
    monkeypatch.setattr(news_collector_module, "CURRENTS_API_KEY", "fake-key")
    collector = NewsCollector(db=tmp_db)
    mocker.patch.object(collector, "_collect_from_rss", return_value=2)
    mocker.patch.object(collector, "_collect_from_newsapi", return_value=1)
    mocker.patch.object(collector, "_collect_from_currents", return_value=4)

    stats = collector.collect_all()

    # Sözlüğün TAMAMI değil ilgili alanlar karşılaştırılıyor: collect_all
    # kaynak başına teşhis alanları da döndürüyor (bkz. failed_feeds) ve
    # tam eşitlik her yeni alanda bu testi sebepsiz kırardı.
    assert (stats["rss"], stats["newsapi"], stats["currents"], stats["total"]) == (2, 1, 4, 7)


def test_gaming_filter_rejects_what_to_watch_roundups():
    """
    Gerçek olay: Screen Rant'ten "3 Best Movies To Watch On Prime Video This
    Weekend" başlıklı film derlemesi, açıklamasında "a video game adventure
    adaptation" geçtiği için "video game" anahtar kelimesine takılıp OYUN
    haberi sayıldı ve hakkında gönderi üretildi.
    """
    assert NewsCollector._is_gaming_relevant(
        "3 Best Movies To Watch On Prime Video This Weekend (August 1-2)",
        "a new shark thriller, a video game adventure adaptation, and a spy thriller",
    ) is False
    assert NewsCollector._is_gaming_relevant(
        "What To Watch This Weekend on Netflix",
        "romantic comedies and documentaries",
    ) is False


def test_gaming_filter_keeps_game_adaptations():
    """
    Kullanıcı isteği: oyun uyarlaması film/dizi/animeler oyun dünyasının
    parçası, dahil edilmeli. Derleme filtresi bunları ELEMEMELİ — "prime
    video"/"tv series" gibi ifadeler başlıkta geçse bile.
    """
    for title, desc in [
        ("The Last of Us Season 3 begins filming",
         "HBO series based on the video game returns"),
        ("Fallout TV series renewed for season 3 on Prime Video",
         "The live-action adaptation of the video game"),
        ("Castlevania Nocturne anime gets new season",
         "The anime adaptation of the classic game series"),
        ("Silent Hill movie reboot announced",
         "based on the video game franchise"),
    ]:
        assert NewsCollector._is_gaming_relevant(title, desc) is True, title


def test_gaming_filter_keeps_game_news_that_mentions_a_tv_show():
    """
    Derleme filtresi YALNIZCA başlığa bakmalı: bir oyun haberinin
    açıklamasında dizi/platform adı geçmesi gayet normaldir.
    """
    assert NewsCollector._is_gaming_relevant(
        "Fallout game sales surge on Steam",
        "The Fallout TV series on Prime Video boosted the game's player count.",
    ) is True


def test_gaming_filter_detects_adaptations_by_franchise_name():
    """
    "Fallout Season 3 renewed" gibi kısa başlıklarda ne "adaptation" ifadesi
    ne de tanınan bir oyun terimi geçiyor. Bilinen uyarlama serisi adı +
    ekran-medyası bağlam kelimesi birlikte görülünce oyun haberi sayılmalı.
    """
    for title, desc in [
        ("Fallout Season 3 renewed", "Amazon confirms the show returns"),
        ("Arcane Season 2 finale breaks records", "The Netflix series concluded"),
        ("Twisted Metal season 2 trailer released", "Peacock show"),
    ]:
        assert NewsCollector._is_gaming_relevant(title, desc) is True, title


def test_gaming_filter_ignores_franchise_words_in_everyday_english():
    """
    Seri adı TEK BAŞINA yeterli olmamalı: "fallout", "halo", "arcane" gibi
    kelimeler günlük İngilizcede de kullanılıyor. Ekran-medyası bağlamı
    yoksa eşleşme sayılmaz.
    """
    assert NewsCollector._is_gaming_relevant(
        "Political fallout from the merger continues",
        "Regulators respond to the deal",
    ) is False
    assert NewsCollector._is_gaming_relevant(
        "Halo effect in consumer pricing studies",
        "Economists analyze brand perception",
    ) is False


def test_unreliable_sources_block_piracy_scraper_and_betting_sites():
    """
    Canlı görsel-kaynağı analizinde gerçek görseli olmayan içeriklerin çoğu
    aslında hiç yayınlanmaması gereken kaynaklardan geliyordu: Cgpersia
    (kırılmış Udemy kursu dağıtan korsan sitesi), Biztoc (içerik kazıyıcı),
    Mmapayout (bahis geliri sitesi).
    """
    for src in ["Cgpersia.com", "Biztoc.com", "Mmapayout.com", "nulled.to"]:
        assert NewsCollector._is_unreliable_source(src) is True, src


def test_unreliable_sources_keep_legitimate_outlets():
    for src in ["IGN", "Polygon", "TechCrunch", "MMOs.com", "Eurogamer"]:
        assert NewsCollector._is_unreliable_source(src) is False, src


# =============================================
# İçerik HTML'ine gömülü görsel
# =============================================

class _SahteEntry(dict):
    """feedparser entry'si hem sözlük hem öznitelik erişimi sunar."""
    def __getattr__(self, ad):
        try:
            return self[ad]
        except KeyError:
            raise AttributeError(ad)


def test_image_extracted_from_content_html():
    """
    Bazı beslemeler görseli hiçbir standart alanda vermiyor, doğrudan yazının
    HTML'ine gömüyor — massivelyop.com ve mmorpg.com bunu yapıyor. Bu alanlar
    kontrol edilmediği için o haberler görselsiz kaydediliyor ve sonra makale
    sayfası kazınmaya çalışılıp WAF 403 alınıyordu (5 Ağustos 2026).
    """
    entry = _SahteEntry(content=[{"value": '<p>x</p><img src="https://a.com/kapak.jpg"/>'}])
    assert NewsCollector._extract_image(entry) == "https://a.com/kapak.jpg"


def test_image_extracted_from_summary_when_no_content():
    entry = _SahteEntry(summary='<img src="https://a.com/ozet.png" alt="x"/>')
    assert NewsCollector._extract_image(entry) == "https://a.com/ozet.png"


def test_standard_fields_take_priority_over_content_html():
    """Standart alan varsa içerik HTML'ine düşülmemeli."""
    entry = _SahteEntry(
        media_thumbnail=[{"url": "https://a.com/standart.jpg"}],
        content=[{"value": '<img src="https://a.com/gomulu.jpg"/>'}],
    )
    assert NewsCollector._extract_image(entry) == "https://a.com/standart.jpg"


def test_no_image_anywhere_returns_none():
    assert NewsCollector._extract_image(_SahteEntry(summary="<p>görselsiz</p>")) is None


def test_malformed_content_does_not_crash():
    for bozuk in ({}, {"content": []}, {"content": [{}]}, {"content": "metin"}):
        assert NewsCollector._extract_image(_SahteEntry(**bozuk)) is None
