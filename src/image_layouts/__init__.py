"""
Feed gönderisi görsel şablonları.
Her modül tek bir şablonu temsil eder ve tek bir `render(gen, ...)` fonksiyonu
sunar — `gen` bir ImageGenerator örneğidir, paylaşılan çizim araç kutusuna
(_get_background, _draw_top_bar, _draw_headline_block vb.) buradan erişilir.

Yeni bir şablon eklemek için: bu pakette yeni bir dosya oluşturup aynı
imzada bir `render` fonksiyonu yazın, sonra aşağıdaki LAYOUTS sözlüğüne
ekleyin ve config.py'deki POST_LAYOUT_VARIANTS listesine adını ekleyin.
"""

from src.image_layouts import gradient_classic, split_panel, card_frame

LAYOUTS = {
    "gradient_classic": gradient_classic.render,
    "split_panel": split_panel.render,
    "card_frame": card_frame.render,
}
