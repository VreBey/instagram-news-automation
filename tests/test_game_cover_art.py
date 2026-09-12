"""
src.game_cover_art birim testleri. Gerçek ağ isteği atılmaz — requests.get
mock'lanır. Adaylara ayırma (_extract_game_name_candidates) ve katı doğrulama
(_is_valid_match) özellikle test edilir — bunlar canlı Steam API ile
denenerek kalibre edildi (bkz. modül docstring'i): tek kelimelik zayıf
adaylar ve gevşek 'ortak kelime' eşleşmesi gerçek yanlış-pozitiflere yol
açtığı için kasıtlı olarak katı.
"""

import io

from PIL import Image

import src.game_cover_art as game_cover_art
from src.game_cover_art import (
    _extract_game_name_candidates,
    _is_valid_match,
    fetch_game_cover_art,
)


def _fake_image_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (50, 80, 150)).save(buf, "JPEG")
    return buf.getvalue()


def _search_response(mocker, items: list):
    resp = mocker.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"items": items}
    return resp


def _rawg_response(mocker, results: list):
    resp = mocker.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"results": results}
    return resp


def _image_response(mocker, content: bytes):
    resp = mocker.Mock()
    resp.raise_for_status.return_value = None
    resp.content = content
    return resp


# ---- _extract_game_name_candidates ----

def test_extract_candidates_finds_leading_game_name():
    cands = _extract_game_name_candidates("Grand Theft Auto 6 delayed again, Rockstar says")
    assert "Grand Theft Auto 6" in cands


def test_extract_candidates_handles_connectors_inside_name():
    cands = _extract_game_name_candidates("The Legend of Zelda: Echoes of Wisdom gets a release date")
    assert any("Legend of Zelda" in c for c in cands)


def test_single_word_candidate_is_protected_by_exact_match_not_by_exclusion():
    """
    Tek kelimelik adaylar artık ÜRETİLİYOR (gerçek oyun adlarının önemli bir
    kısmı tek kelime: Palia, EverQuest, Valorant). Yanlış eşleşme koruması
    aday üretiminde değil, _is_valid_match'teki TAM EŞİTLİK şartında:
    "Rockstar" adayı "Rockstar Life" ile eşleşemez.
    """
    cands = _extract_game_name_candidates("Rockstar announces new project")
    assert "Rockstar" in cands
    assert _is_valid_match("Rockstar", "Rockstar Life") is False
    assert _is_valid_match("Rockstar", "Rockstar") is True


def test_extract_candidates_returns_empty_for_no_capitalized_words():
    assert _extract_game_name_candidates("a quick update about some things") == []


def test_extract_candidates_orders_longest_first():
    cands = _extract_game_name_candidates("Call of Duty Black Ops 7 beta impressions")
    assert cands[0] == "Call of Duty Black Ops 7"


# ---- _is_valid_match ----

def test_is_valid_match_accepts_exact_normalized_equality():
    assert _is_valid_match("Cyberpunk 2077", "Cyberpunk 2077") is True


def test_is_valid_match_accepts_punctuation_and_symbol_differences():
    assert _is_valid_match("Call of Duty Black Ops 7", "Call of Duty®: Black Ops 7") is True


def test_is_valid_match_accepts_when_result_extends_candidate():
    assert _is_valid_match("Hollow Knight Silksong", "Hollow Knight: Silksong") is True


def test_is_valid_match_rejects_unrelated_product_sharing_a_trailing_digit():
    # Canlı testte gözlemlenen gerçek yanlış-pozitif: "Diablo 4" sorgusu,
    # alakasız bir Funko paketiyle (adında hem "4" hem "Diablo" geçen) gevşek
    # kelime-kesişimiyle eşleşiyordu. Katı normalize-prefix kuralı bunu reddeder.
    assert _is_valid_match("Diablo 4", "Funko Fusion - Pack 4 - El Diablo") is False


def test_is_valid_match_rejects_when_candidate_is_only_a_substring_in_the_middle():
    assert _is_valid_match("Half Life 3", "Half-Life: A Place in the West - Chapter 3") is False


def test_is_valid_match_rejects_empty_strings():
    assert _is_valid_match("", "Cyberpunk 2077") is False
    assert _is_valid_match("Cyberpunk 2077", "") is False


# ---- fetch_game_cover_art (uçtan uca, mock'lu) ----

def test_fetch_game_cover_art_returns_none_without_query():
    assert fetch_game_cover_art(None) is None
    assert fetch_game_cover_art("") is None


def test_fetch_game_cover_art_returns_none_when_no_candidates_extracted(mocker):
    get_mock = mocker.patch("src.game_cover_art.requests.get")
    assert fetch_game_cover_art("just a lowercase sentence with no names") is None
    get_mock.assert_not_called()


def test_fetch_game_cover_art_returns_none_on_search_request_exception(mocker):
    mocker.patch(
        "src.game_cover_art.requests.get",
        side_effect=game_cover_art.requests.RequestException("boom"),
    )
    assert fetch_game_cover_art("Cyberpunk 2077 gets a big update") is None


def test_fetch_game_cover_art_downloads_and_caches_on_match(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    items = [{"id": 1091500, "name": "Cyberpunk 2077"}]
    get_mock = mocker.patch(
        "src.game_cover_art.requests.get",
        side_effect=[
            _search_response(mocker, items),
            _image_response(mocker, _fake_image_bytes(600, 900)),
        ],
    )

    path = fetch_game_cover_art("Cyberpunk 2077 gets massive update with new content")

    assert path is not None
    assert path.endswith(".jpg")
    assert get_mock.call_count == 2


def test_fetch_game_cover_art_uses_cache_on_second_call(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    items = [{"id": 1091500, "name": "Cyberpunk 2077"}]
    mocker.patch(
        "src.game_cover_art.requests.get",
        side_effect=[
            _search_response(mocker, items),
            _image_response(mocker, _fake_image_bytes(600, 900)),
        ],
    )
    first_path = fetch_game_cover_art("Cyberpunk 2077 gets massive update")

    get_mock = mocker.patch("src.game_cover_art.requests.get")
    second_path = fetch_game_cover_art("Cyberpunk 2077 gets massive update")

    assert second_path == first_path
    get_mock.assert_not_called()


def test_fetch_game_cover_art_returns_none_when_cover_too_small(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    items = [{"id": 1091500, "name": "Cyberpunk 2077"}]
    mocker.patch(
        "src.game_cover_art.requests.get",
        side_effect=[
            _search_response(mocker, items),
            _image_response(mocker, _fake_image_bytes(50, 50)),
        ],
    )
    assert fetch_game_cover_art("Cyberpunk 2077 gets massive update") is None


def test_fetch_game_cover_art_falls_through_weak_candidate_to_stronger_one(mocker, tmp_path, monkeypatch):
    """
    Birden fazla aday varsa (ör. oyun adı + ayrı bir stüdyo adı), güçlü
    (çok kelimeli) aday önce denenir; sonuç yoksa daha zayıf tek kelimelik
    adaylar HİÇ denenmez (bkz. _extract_game_name_candidates testleri) —
    burada tek aday zaten güçlü olduğundan direkt eşleşmeli.
    """
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    items = [{"id": 1091500, "name": "Cyberpunk 2077: Phantom Liberty"}]
    mocker.patch(
        "src.game_cover_art.requests.get",
        side_effect=[
            _search_response(mocker, items),
            _image_response(mocker, _fake_image_bytes(600, 900)),
        ],
    )
    path = fetch_game_cover_art("Cyberpunk 2077 expansion gets new trailer")
    assert path is not None


# ---- RAWG (Steam'de bulunamayan konsol-exclusive/Steam-dışı oyunlar için) ----

def test_fetch_game_cover_art_skips_rawg_without_api_key(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    monkeypatch.setattr(game_cover_art, "RAWG_API_KEY", "")
    # Steam sonuçsuz döner; RAWG_API_KEY boş olduğu için RAWG hiç denenmemeli.
    mocker.patch("src.game_cover_art.requests.get", return_value=_search_response(mocker, []))
    rawg_mock = mocker.patch("src.game_cover_art._search_rawg")

    assert fetch_game_cover_art("Silent Hill f announced for PS5 and PC") is None
    rawg_mock.assert_not_called()


def test_fetch_game_cover_art_falls_back_to_rawg_when_steam_has_no_match(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    monkeypatch.setattr(game_cover_art, "RAWG_API_KEY", "test-key")
    mocker.patch("src.game_cover_art._resolve_via_steam", return_value=None)
    mocker.patch(
        "src.game_cover_art._resolve_via_rawg",
        return_value=("Silent Hill f", "https://example.com/shf.jpg"),
    )
    mocker.patch(
        "src.game_cover_art.requests.get",
        return_value=_image_response(mocker, _fake_image_bytes(1280, 720)),
    )

    path = fetch_game_cover_art("Silent Hill f announced for PS5 and PC")
    assert path is not None


def test_fetch_game_cover_art_prefers_steam_over_rawg_when_both_match(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    monkeypatch.setattr(game_cover_art, "RAWG_API_KEY", "test-key")
    mocker.patch(
        "src.game_cover_art._resolve_via_steam",
        return_value=("Cyberpunk 2077", "https://cdn.example.com/cover.jpg"),
    )
    rawg_mock = mocker.patch("src.game_cover_art._resolve_via_rawg")
    mocker.patch(
        "src.game_cover_art.requests.get",
        return_value=_image_response(mocker, _fake_image_bytes(600, 900)),
    )

    path = fetch_game_cover_art("Cyberpunk 2077 gets massive update")
    assert path is not None
    rawg_mock.assert_not_called()


def test_fetch_game_cover_art_returns_none_when_neither_source_matches(mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(game_cover_art, "GAME_COVER_CACHE_DIR", tmp_path)
    monkeypatch.setattr(game_cover_art, "RAWG_API_KEY", "test-key")
    mocker.patch("src.game_cover_art._resolve_via_steam", return_value=None)
    mocker.patch("src.game_cover_art._resolve_via_rawg", return_value=None)

    assert fetch_game_cover_art("Silent Hill f announced for PS5 and PC") is None


def test_resolve_via_rawg_returns_none_without_api_key():
    assert game_cover_art._resolve_via_rawg(["Silent Hill"]) is None


def test_resolve_via_rawg_rejects_weak_match_same_as_steam(mocker, monkeypatch):
    """RAWG kademesi de aynı katı _is_valid_match kuralına tabi olmalı."""
    monkeypatch.setattr(game_cover_art, "RAWG_API_KEY", "test-key")
    rawg_results = [{"name": "Funko Fusion - Pack 4 - El Diablo", "background_image": "https://example.com/x.jpg"}]
    mocker.patch("src.game_cover_art.requests.get", return_value=_rawg_response(mocker, rawg_results))

    assert game_cover_art._resolve_via_rawg(["Diablo 4"]) is None


def test_resolve_via_rawg_accepts_valid_match(mocker, monkeypatch):
    monkeypatch.setattr(game_cover_art, "RAWG_API_KEY", "test-key")
    rawg_results = [{"name": "Silent Hill f", "background_image": "https://example.com/shf.jpg"}]
    mocker.patch("src.game_cover_art.requests.get", return_value=_rawg_response(mocker, rawg_results))

    result = game_cover_art._resolve_via_rawg(["Silent Hill f"])
    assert result == ("Silent Hill f", "https://example.com/shf.jpg")


# ---- Title Case başlıklar (canlı üretimde keşfedilen kör nokta) ----

def test_extract_candidates_handles_title_case_headlines():
    """
    Canlı üretimde oyun kapağı önbelleği 0 dosyaydı: gerçek haber başlıkları
    Title Case ("Albion Online Dragonfire: August 31 Launch Date...") olduğu
    için "büyük harfli kelime dizisi" mantığı TÜM BAŞLIĞI tek aday yapıyor,
    hiçbir mağazada eşleşmiyordu. Artık baştan başlayan kısalan ön ekler de
    aday üretiliyor.
    """
    cands = _extract_game_name_candidates(
        "Albion Online Dragonfire: August 31 Launch Date, Dragon Raids, and New Region Explained"
    )
    assert "Albion Online" in cands
    cands = _extract_game_name_candidates(
        "Metal Gear Solid: Master Collection Vol. 2 Launches August 27 With MGS4"
    )
    assert "Metal Gear Solid" in cands


def test_single_word_candidates_only_from_first_phrase():
    """
    Gerçek yanlış eşleşme: "EA UFC 6 top selling video game in June"
    başlığındaki "June", Steam'de gerçekten "June" adlı bir oyunla TAM
    eşleşip o oyunun kapağını getirdi. Tek kelimelik adaylar artık yalnızca
    başlığın İLK öbeğinden alınıyor (oyun adı başta geçer).
    """
    cands = _extract_game_name_candidates("EA UFC 6 top selling video game in June")
    assert "June" not in cands
    # İlk öbekten gelen tek kelime kabul edilir
    assert "Palia" in _extract_game_name_candidates(
        "Palia Patch 0.205 Brings Ranch Animals, Mounts & Summer Progression"
    )


def test_single_word_stoplist_blocks_generic_words():
    """Başlık jenerik bir kelimeyle başlasa bile tek kelimelik aday olmamalı."""
    cands = _extract_game_name_candidates("Update Brings New Features")
    assert "Update" not in cands


def test_is_valid_match_requires_exact_equality_for_single_word():
    """
    Tek kelimede ön ek eşleşmesi tehlikeli: "Rockstar" adayı "Rockstar Life"
    ile eşleşirdi. Tek kelime için tam eşitlik aranır.
    """
    assert _is_valid_match("Palia", "Palia") is True
    assert _is_valid_match("Rockstar", "Rockstar Life") is False
    # Çok kelimeli adaylarda ön ek eşleşmesi hâlâ geçerli
    assert _is_valid_match("Metal Gear Solid", "METAL GEAR SOLID: MASTER COLLECTION Vol.1") is True
