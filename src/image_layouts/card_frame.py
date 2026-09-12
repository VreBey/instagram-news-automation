"""Kart şablonu: arka plan (foto/mesh) üzerinde ortalanmış yuvarlak köşeli kart."""

from PIL import Image, ImageDraw

from config import COLORS, FONT_SIZES


def render(gen, size: tuple, category: str, title: str, source: str,
           seed: int = None, image_url: str = None, manual_image_path: str = None,
           subtitle: str = None, game_title: str = None,
           news_url: str = None) -> Image.Image:
    width, height = size
    img = gen._get_background(size, category, seed, image_url=image_url,
                               manual_image_path=manual_image_path, game_title=game_title,
                               news_url=news_url, relevance_text=title)
    img = gen._add_decorative_elements(img, category)
    draw = ImageDraw.Draw(img)

    gen._draw_top_bar(draw, size, category, img=img)

    card_margin = 50
    card_x0, card_y0 = card_margin, int(height * 0.28)
    card_x1, card_y1 = width - card_margin, int(height * 0.74)

    # Yarı saydam kart zemini
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=28, fill=gen._hex_to_rgb(COLORS["bg_card"]) + (220,)
    )
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Başlık, kart içinde ortalanmış
    text_area_width = max(200, (card_x1 - card_x0) - 80)
    font_size = FONT_SIZES["title"]
    font = gen._get_font("accent", font_size)
    lines = gen._wrap_text(title, font, text_area_width, draw)
    line_height = font_size + 12
    max_card_text_height = (card_y1 - card_y0) - 100
    if subtitle:
        max_card_text_height -= 55
    while len(lines) * line_height > max_card_text_height and font_size > 26:
        font_size -= 4
        font = gen._get_font("accent", font_size)
        lines = gen._wrap_text(title, font, text_area_width, draw)
        line_height = font_size + 12

    total_text_height = len(lines) * line_height
    y_offset = card_y0 + max(0, ((card_y1 - card_y0) - total_text_height) // 2)
    text_color = gen._hex_to_rgb(COLORS["text_primary"])
    glow_color = gen._hex_to_rgb(COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"])
    img, draw = gen._draw_headline_block(
        img, (card_x0, y_offset), lines, font,
        fill=text_color, glow_color=glow_color, line_height=line_height,
        category=category, subtitle=subtitle, center_width=(card_x1 - card_x0),
        subtitle_max_width=text_area_width
    )

    gen._draw_source(draw, size, source)
    gen._draw_bottom_bar(draw, size, img=img)

    return img
