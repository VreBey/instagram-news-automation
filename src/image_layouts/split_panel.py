"""Split-panel şablonu: üstte arka plan (foto/mesh) başlık bloğu, altta düz kontrast panel."""

from PIL import Image, ImageDraw

from config import COLORS, FONT_SIZES


def render(gen, size: tuple, category: str, title: str, source: str,
           seed: int = None, image_url: str = None, manual_image_path: str = None,
           subtitle: str = None, game_title: str = None,
           news_url: str = None) -> Image.Image:
    width, height = size
    panel_h = int(height * 0.22)

    img = gen._get_background(size, category, seed, image_url=image_url,
                               manual_image_path=manual_image_path, game_title=game_title,
                               news_url=news_url, relevance_text=title)
    img = gen._add_decorative_elements(img, category)
    draw = ImageDraw.Draw(img)

    # Alt kontrast panel (bg_card)
    draw.rectangle(
        [0, height - panel_h, width, height],
        fill=gen._hex_to_rgb(COLORS["bg_card"])
    )

    gen._draw_top_bar(draw, size, category, img=img)

    # Başlık üst bloğa sığacak şekilde (panel üstünde kalacak alan)
    padding = 60
    text_area_width = width - (padding * 2)
    y_start = int(height * 0.30)
    max_y = height - panel_h - 40
    if subtitle:
        max_y -= 55

    font_size = FONT_SIZES["title"]
    font = gen._get_font("accent", font_size)
    lines = gen._wrap_text(title, font, text_area_width, draw)
    line_height = font_size + 12
    while len(lines) * line_height > (max_y - y_start) and font_size > 28:
        font_size -= 4
        font = gen._get_font("accent", font_size)
        lines = gen._wrap_text(title, font, text_area_width, draw)
        line_height = font_size + 12

    total_text_height = len(lines) * line_height
    y_offset = y_start + max(0, (max_y - y_start - total_text_height) // 2)
    text_color = gen._hex_to_rgb(COLORS["text_primary"])
    glow_color = gen._hex_to_rgb(COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"])
    img, draw = gen._draw_headline_block(
        img, (padding, y_offset), lines, font,
        fill=text_color, glow_color=glow_color, line_height=line_height,
        category=category, subtitle=subtitle, subtitle_max_width=text_area_width
    )

    # Alt panelde kaynak (sol) + logo/marka (sağ)
    panel_y = height - panel_h + (panel_h // 2) - 12
    if source:
        font_source = gen._get_font("body", FONT_SIZES["caption"])
        gen._draw_mixed_text(draw, (padding, panel_y), f"📰 {source}", font_source,
                              fill=gen._hex_to_rgb(COLORS["text_secondary"]))

    gen._draw_brand_mark(draw, img, width - padding, panel_y, align="right")

    return img
