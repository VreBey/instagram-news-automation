"""Reels senaryosu ayrıştırıcısı — bozuk ama makul yanıtlara dayanmalı.

Gemini düz metin döndürüyor (yapılandırılmış çıktı modu yok) ve prompt
"INTRO: / SEGMENT 1: / SEGMENT 2: / OUTRO:" biçimini istiyor. Model bu
biçimden azıcık saparsa parser sessizce YANLIŞ çıktı üretiyordu.

İki gerçek kusur (8 Ağustos 2026 denetimi):

1. `\\s*(.*)` kalıbındaki `\\s*` SATIR SONUNU da yiyordu. "SEGMENT 2:"
   boş bırakılırsa bir sonraki satırın tamamı yakalanıyor ve segmentin
   içeriği "SEGMENT 3: Diablo haberi" oluyordu — yani İŞARETÇİNİN KENDİSİ
   reels slaytına basılıp yayınlanıyordu.

2. Parser segment NUMARASINI atıyordu. `video_generator` görselleri
   segmentlere İNDİSE göre eşliyor; ortadaki bir segment düşerse sonraki
   her haber BAŞKA haberin fotoğrafıyla eşleşiyordu.
"""

from src.content_processor import ContentProcessor


def _ayristir(metin):
    return ContentProcessor._parse_reels_script_response(metin)


def test_marker_never_leaks_into_the_content():
    """Boş bir segment, işaretçiyi bir sonraki segmentin metnine sokmamalı."""
    r = _ayristir(
        "INTRO: Giriş\n"
        "SEGMENT 1: Elden Ring haberi\n"
        "SEGMENT 2:\n"
        "SEGMENT 3: Diablo haberi\n"
        "OUTRO: Çıkış"
    )
    assert r["segments"] == ["Elden Ring haberi", "Diablo haberi"]
    for s in r["segments"]:
        assert "SEGMENT" not in s.upper(), f"işaretçi metne sızdı: {s!r}"


def test_dropped_segment_does_not_shift_image_pairing():
    """
    2. segment düşse bile 3. segment 3. haberin görselini almalı.
    Aksi halde haber, başka haberin fotoğrafıyla yayınlanır.
    """
    r = _ayristir(
        "SEGMENT 1: Birinci\n"
        "SEGMENT 2:\n"
        "SEGMENT 3: Üçüncü\n"
    )
    assert r["segment_indices"] == [0, 2]

    gorseller = ["bir.jpg", "iki.jpg", "uc.jpg"]
    eslesme = [gorseller[h] for h in r["segment_indices"]]
    assert eslesme == ["bir.jpg", "uc.jpg"]


def test_out_of_order_segments_are_reordered_by_number():
    """Model sırayı bozarsa numara esas alınmalı."""
    r = _ayristir("SEGMENT 2: İkinci\nSEGMENT 1: Birinci\n")
    assert r["segments"] == ["Birinci", "İkinci"]
    assert r["segment_indices"] == [0, 1]


def test_intro_and_outro_stop_at_the_line_end():
    r = _ayristir("INTRO:\nSEGMENT 1: Haber\nOUTRO:\n")
    assert r["intro"] == ""
    assert r["outro"] == ""
    assert r["segments"] == ["Haber"]


def test_normal_response_still_parses():
    r = _ayristir(
        "INTRO: Bugünün en önemli oyun haberleri!\n"
        "SEGMENT 1: Birinci haber metni\n"
        "SEGMENT 2: İkinci haber metni\n"
        "OUTRO: Takip et, hiçbir haberi kaçırma!"
    )
    assert r["intro"] == "Bugünün en önemli oyun haberleri!"
    assert r["segments"] == ["Birinci haber metni", "İkinci haber metni"]
    assert r["segment_indices"] == [0, 1]
    assert r["outro"] == "Takip et, hiçbir haberi kaçırma!"


def test_extra_whitespace_and_case_are_tolerated():
    r = _ayristir("  segment 1 :   Boşluklu haber  \n")
    assert r["segments"] == ["Boşluklu haber"]


def test_no_segments_fails_closed():
    """Kullanılamaz yanıtta None dönmeli — şablona düşülmemeli."""
    assert _ayristir("Model bugün başka bir şey söyledi.") is None
    assert _ayristir("") is None


def test_video_generator_uses_the_indices(tmp_db, monkeypatch):
    """Uçtan uca: düşen segment, görselin kaymasına yol açmamalı."""
    from src.video_generator import VideoGenerator

    v = VideoGenerator(db=tmp_db)
    kullanilan = []

    def _sahte_slayt(segment_text, title, index, total, category,
                     image_url=None, news_url=None, game_title=None):
        kullanilan.append((segment_text, image_url))
        return None  # slayt üretmeye gerek yok

    monkeypatch.setattr(v, "_create_news_reel_slide", _sahte_slayt)
    monkeypatch.setattr(v, "_create_intro_slide", lambda *a, **k: None)
    monkeypatch.setattr(v, "_create_outro_slide", lambda *a, **k: None)

    v._create_slide_images({
        "segments": ["Birinci haber metni burada", "Üçüncü haber metni burada"],
        "segment_indices": [0, 2],
        "image_urls": ["bir.jpg", "iki.jpg", "uc.jpg"],
        "news_urls": ["bir.html", "iki.html", "uc.html"],
        "game_titles": ["Bir", "Iki", "Uc"],
    }, "gaming")

    assert kullanilan == [
        ("Birinci haber metni burada", "bir.jpg"),
        ("Üçüncü haber metni burada", "uc.jpg"),
    ], "segment yanlış haberin görseliyle eşleşti"
