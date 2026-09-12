"""Klasik şablon: tam sayfa arka plan (foto/mesh) + üstte kategori + ortada başlık."""

from PIL import Image, ImageDraw


def render(gen, size: tuple, category: str, title: str, source: str,
           seed: int = None, image_url: str = None, manual_image_path: str = None,
           subtitle: str = None, game_title: str = None,
           news_url: str = None) -> Image.Image:
    img = gen._get_background(size, category, seed, image_url=image_url,
                               manual_image_path=manual_image_path, game_title=game_title,
                               news_url=news_url, relevance_text=title)
    img = gen._add_decorative_elements(img, category)
    draw = ImageDraw.Draw(img)

    gen._draw_top_bar(draw, size, category, img=img)
    img, draw = gen._draw_title(img, draw, size, title, category, subtitle=subtitle)
    gen._draw_source(draw, size, source)
    gen._draw_bottom_bar(draw, size, img=img)

    return img
