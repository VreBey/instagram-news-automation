"""Slayt geçişleri SİYAHTAN AÇILMAMALI.

`crossfadein` yalnızca klibe maske takar; kliplerin üst üste binmesini
sağlamaz. `concatenate_videoclips` negatif dolgu olmadan çağrılınca klipler
uç uca ekleniyor ve açılan klibin altında yalnızca siyah zemin kalıyor —
yani çapraz geçiş değil, siyahtan açılma oluyor.

Ölçüm (8 Ağustos 2026, yayına hazır bir reels): 5. ve 10. saniyede kare
parlaklığı tam olarak 0.0'a düşüyordu. 7 slaytlık bir videoda 6 siyah
çakma demek.
"""

import numpy as np
import pytest

from config import REELS_CONFIG

moviepy = pytest.importorskip("moviepy.editor")


@pytest.fixture
def parlak_slaytlar(tmp_path):
    """İki AYRI parlak renk — geçiş kararırsa ölçümde görünür."""
    from PIL import Image

    yollar = []
    for i, renk in enumerate([(230, 230, 230), (200, 200, 200)]):
        y = tmp_path / f"s{i}.png"
        Image.new("RGB", (216, 384), renk).save(y)
        yollar.append(str(y))
    return yollar


def _video(slaytlar, tmp_db, tmp_path):
    from src.video_generator import VideoGenerator

    v = VideoGenerator(db=tmp_db)
    yol = v._compose_video(slaytlar, None, "gaming")
    assert yol, "video üretilemedi"
    return yol


def _parlaklik(video_yolu, saniye):
    klip = moviepy.VideoFileClip(video_yolu)
    try:
        return float(np.asarray(klip.get_frame(saniye)).mean())
    finally:
        klip.close()


def test_transition_never_goes_black(tmp_db, parlak_slaytlar, tmp_path):
    """
    İki parlak slayt arasındaki geçişte kare KARARMAMALI. Her ikisi de
    ~200+ parlaklıkta; araları da o civarda kalmalı.
    """
    yol = _video(parlak_slaytlar, tmp_db, tmp_path)
    gecis = REELS_CONFIG["duration_per_slide"] - REELS_CONFIG["transition_duration"]

    en_karanlik = min(
        _parlaklik(yol, t)
        for t in (gecis, gecis + 0.1, gecis + 0.25, gecis + 0.4)
    )

    assert en_karanlik > 100, (
        f"geçişte kare karardı (parlaklık {en_karanlik:.1f}) — "
        "negatif dolgu yok, geçiş siyahtan açılıyor"
    )


def test_overlap_shortens_the_total_duration(tmp_db, parlak_slaytlar, tmp_path):
    """
    Bindirmenin KANITI: toplam süre, slayt sayısı × süre değil; her geçiş
    kadar kısa olmalı. Eşit çıkıyorsa klipler uç uca eklenmiş demektir.
    """
    yol = _video(parlak_slaytlar, tmp_db, tmp_path)
    klip = moviepy.VideoFileClip(yol)
    try:
        sure = klip.duration
    finally:
        klip.close()

    # MoviePy dolguyu her klipten SONRA uyguluyor, sonuncusu dahil:
    # toplam = n × süre + n × dolgu. Yani 2 slaytta 10 − 1,0 = 9,0 sn.
    # (Sezgisel beklenti 9,5 idi; ölçüm 9,0 dedi ve doğru olan ölçüm.)
    # Pratik sonucu: son slayt geçiş süresi kadar kısalıyor.
    bindirmesiz = 2 * REELS_CONFIG["duration_per_slide"]
    beklenen = bindirmesiz - 2 * REELS_CONFIG["transition_duration"]

    assert abs(sure - beklenen) < 0.35, (
        f"süre {sure:.2f} sn; bindirmeli {beklenen} sn beklenirdi "
        f"(bindirmesiz {bindirmesiz} sn)"
    )


def test_first_slide_is_not_faded_in(tmp_db, parlak_slaytlar, tmp_path):
    """İlk slayt açılışta karartılmamalı — videonun ilk karesi doludur."""
    yol = _video(parlak_slaytlar, tmp_db, tmp_path)
    assert _parlaklik(yol, 0.1) > 100
