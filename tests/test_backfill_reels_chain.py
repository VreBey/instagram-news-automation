"""Eski reels senaryolarına zincir alanlarını geri doldurma.

Zincirin og:image ve Steam basamakları senaryodaki `news_urls` /
`game_titles` alanlarına bağlı. Bu alanlar senaryoya sonradan eklendi;
öncesinde yazılmış senaryolar yeniden render edilse bile doğrudan stok
fotoğrafa düşüyordu.

Ölçüm (8 Ağustos 2026): bildirim penceresindeki 9 bekleyen reels'in
9'unda da `news_urls` boştu; aynı haberler tek tek sınandığında görselsiz
8 haberin 8'inde og:image bulunuyordu. Yani kayıp kaynak sitelerden değil,
senaryodaki eksik alandan geliyordu.
"""

import json

import pytest

from scripts.backfill_reels_chain import geri_doldur


@pytest.fixture
def eski_reels(tmp_db):
    """Zincir alanları OLMAYAN bir reels taslağı kurar."""
    def _kur(ozetler):
        for i, ozet in enumerate(ozetler):
            nid = tmp_db.add_news(
                title=f"English Source {i}", url=f"https://kaynak/{i}",
                source_name="IGN", category="gaming",
                description="aciklama", image_url=None,
            )
            tmp_db.mark_news_processed(nid, relevance_score=0.8)
            tmp_db.add_content(news_id=nid, content_type="post",
                               caption="c", summary_text=ozet)

        senaryo = {
            "intro": "Selam", "outro": "Bay",
            "segments": [f"segment {i}" for i in range(len(ozetler))],
            "news_titles": ozetler,   # TÜRKÇE ÖZET — ham başlık değil
        }
        return tmp_db.add_content(news_id=nid, content_type="reels",
                                  caption="c", reels_script=senaryo)
    return _kur


def _senaryo(db, cid):
    with db._get_connection() as c:
        ham = c.execute("SELECT reels_script FROM processed_content WHERE id=?",
                        (cid,)).fetchone()[0]
    return json.loads(ham)


def test_dry_run_writes_nothing(tmp_db, eski_reels):
    cid = eski_reels(["Türkçe özet A", "Türkçe özet B"])
    rapor = geri_doldur(tmp_db.db_path, uygula=False)

    assert rapor["guncellenen"] == 1
    assert not _senaryo(tmp_db, cid).get("news_urls"), "kuru çalışma yazdı"


def test_backfill_fills_the_chain_fields(tmp_db, eski_reels):
    cid = eski_reels(["Türkçe özet A", "Türkçe özet B"])
    geri_doldur(tmp_db.db_path, uygula=True)

    s = _senaryo(tmp_db, cid)
    assert s["news_urls"] == ["https://kaynak/0", "https://kaynak/1"]
    assert len(s["image_urls"]) == 2


def test_game_titles_use_the_raw_source_title(tmp_db, eski_reels):
    """
    Steam kapağı HAM başlıkla aranmalı. Türkçe özetle aranırsa hiçbir oyun
    bulunamaz — zincirin 4. basamağı sessizce ölür.
    """
    cid = eski_reels(["Türkçe özet A"])
    geri_doldur(tmp_db.db_path, uygula=True)

    assert _senaryo(tmp_db, cid)["game_titles"] == ["English Source 0"]


def test_segments_stay_untouched(tmp_db, eski_reels):
    """Sadece zincir alanları yazılmalı; metin ve sıra korunmalı."""
    cid = eski_reels(["Türkçe özet A", "Türkçe özet B"])
    once = _senaryo(tmp_db, cid)["segments"]
    geri_doldur(tmp_db.db_path, uygula=True)
    assert _senaryo(tmp_db, cid)["segments"] == once


def test_unmatched_script_is_skipped_not_corrupted(tmp_db):
    """Hiçbir haber eşleşmezse senaryoya BOŞ liste yazılmamalı."""
    nid = tmp_db.add_news(title="H", url="https://k/x", source_name="IGN",
                          category="gaming", description="a")
    cid = tmp_db.add_content(
        news_id=nid, content_type="reels", caption="c",
        reels_script={"segments": ["a"], "news_titles": ["hiç eşleşmeyen"]},
    )

    rapor = geri_doldur(tmp_db.db_path, uygula=True)

    assert rapor["atlanan"] == 1
    assert "news_urls" not in _senaryo(tmp_db, cid)


def test_stale_script_is_loud_at_render_time(tmp_db, caplog, monkeypatch):
    """
    Zincir alanları eksik bir senaryo render edilirken UYARI vermeli.

    Bu sınıfın maliyeti sessizliğiydi: render "başarılı" dönüyor, video
    üretiliyor, ama tüm slaytlar stok fotoğraf oluyordu. Kimse bakmadan
    fark edilmiyordu.
    """
    from src.video_generator import VideoGenerator

    v = VideoGenerator(db=tmp_db)
    monkeypatch.setattr(v, "_create_intro_slide", lambda *a, **k: None)
    monkeypatch.setattr(v, "_create_news_reel_slide", lambda **k: None)
    monkeypatch.setattr(v, "_create_outro_slide", lambda *a, **k: None)

    with caplog.at_level("WARNING"):
        v._create_slide_images(
            {"segments": ["Türkçe bir segment metni"], "image_urls": [None]},
            "gaming",
        )

    assert "news_urls" in caplog.text


def test_complete_script_renders_quietly(tmp_db, caplog, monkeypatch):
    from src.video_generator import VideoGenerator

    v = VideoGenerator(db=tmp_db)
    monkeypatch.setattr(v, "_create_intro_slide", lambda *a, **k: None)
    monkeypatch.setattr(v, "_create_news_reel_slide", lambda **k: None)
    monkeypatch.setattr(v, "_create_outro_slide", lambda *a, **k: None)

    with caplog.at_level("WARNING"):
        v._create_slide_images(
            {"segments": ["Türkçe bir segment metni"],
             "image_urls": [None], "news_urls": ["https://haber/1"]},
            "gaming",
        )

    assert "news_urls" not in caplog.text


def test_already_notified_reels_are_left_alone(tmp_db, eski_reels):
    """
    Telegram'a gitmiş reels dokunulmaz — kullanıcının gördüğü içerik
    arkasından sessizce değişmemeli.
    """
    cid = eski_reels(["Türkçe özet A"])
    with tmp_db._get_connection() as c:
        c.execute("UPDATE processed_content SET telegram_message_id=42 "
                  "WHERE id=?", (cid,))

    rapor = geri_doldur(tmp_db.db_path, uygula=True)

    assert rapor["incelenen"] == 0
    assert "news_urls" not in _senaryo(tmp_db, cid)
