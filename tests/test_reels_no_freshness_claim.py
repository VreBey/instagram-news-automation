"""Reels ekranda TAZELİK İDDİASI etmemeli.

Reels havuzu SON 7 GÜNDE yayınlanmış gönderilerden derleniyor
(`get_published_for_reels`, days=7) — tek bir güne ait değil.

Ölçüm (8 Ağustos 2026): 5-7 Ağustos haberlerini anlatan taslaklar yeniden
render edilince giriş slaytında "08.08.2026" yazdı; çünkü tarih
`datetime.now()` ile RENDER anında damgalanıyordu. Üstünde de "Bugünün en
önemli Oyun haberleri!" başlığı vardı. Ekranda iki ayrı yanlış iddia:
yanlış tarih ve olmayan bir tazelik.
"""

import inspect
import re

from config import PROMPTS
from src.video_generator import VideoGenerator


def test_intro_slide_does_not_stamp_the_render_date():
    """
    Damga render anını yazıyordu, içeriğin anlattığı dönemi değil — üstelik
    her yeniden render'da değişiyordu.
    """
    kaynak = inspect.getsource(VideoGenerator._create_intro_slide)
    govde = "\n".join(
        s for s in kaynak.splitlines() if not s.strip().startswith("#")
    )
    assert not re.search(r"datetime\.now\(\)", govde), \
        "giriş slaytı yine render tarihini damgalıyor"


def test_prompt_asks_for_a_claim_free_intro():
    p = PROMPTS["generate_reels_script"]
    assert "Öne çıkan" in p
    assert "TAZELİK İDDİASI KULLANMA" in p


def test_prompt_no_longer_asks_for_todays_news():
    p = PROMPTS["generate_reels_script"]
    assert "Giriş: \"Bugünün" not in p, "giriş yine 'bugünün' diyor"


def test_intro_renders_without_a_date(tmp_db, tmp_path, monkeypatch):
    """
    Uçtan uca: üretilen giriş slaytında bir tarih dizesi OLMAMALI.
    Görselden metin okuyamadığımız için çizilen metinler yakalanıyor.
    """
    from PIL import ImageDraw

    v = VideoGenerator(db=tmp_db)
    cizilen: list[str] = []
    gercek = ImageDraw.ImageDraw.text

    def _yakala(self, xy, text, *a, **k):
        cizilen.append(str(text))
        return gercek(self, xy, text, *a, **k)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", _yakala)
    v._create_intro_slide("Öne çıkan Oyun haberleri!", "gaming")

    tarihli = [t for t in cizilen if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", t.strip())]
    assert not tarihli, f"giriş slaytında tarih çizildi: {tarihli}"
