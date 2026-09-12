"""
Hikaye Üretim Modülü
Instagram Story görselleri oluşturur (1080x1920 dikey format).
"""

import logging
import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    COLORS, FONTS, FONT_SIZES, STORY_SIZE,
    FONTS_DIR, STORIES_OUTPUT_DIR, CATEGORIES, STORY_BADGE_TEXT
)
from src.database import Database
from src.image_generator import ImageGenerator
from src.content_language import safe_display_title

logger = logging.getLogger(__name__)


class StoryGenerator:
    """Instagram Story görselleri oluşturur."""

    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.img_gen = ImageGenerator(db=self.db)
        STORIES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def generate_story_image(self, content_id: int = None,
                              news_data: dict = None,
                              sibling_post_published: bool = False) -> str | None:
        """
        Tekil haber hikayesi görseli oluştur.

        sibling_post_published: Aynı habere ait feed gönderisi GERÇEKTEN
        yayınlanmışsa True verilir — bu durumda CTA "detaylar profilde"
        diyebilir. Aksi halde (varsayılan) hiçbir zaman profilde var olmayan
        bir şeye atıfta bulunulmaz, genel "takip et" çağrısı kullanılır.
        publish_scheduled() bu görseli yayından hemen önce, en güncel
        sibling durumuyla yeniden oluşturur (bkz. scheduler.py).
        """
        if content_id:
            content = self.db.get_content_by_id(content_id)
            if not content:
                logger.error(f"İçerik bulunamadı: ID={content_id}")
                return None
        elif news_data:
            content = news_data
        else:
            return None

        category = content.get("category", "ai")

        # Ortak dil kapısı — bkz. src/content_language.py. Eskiden burada
        # `summary_text or news_title` vardı; özet üretilemediğinde ham
        # İngilizce başlık hikayenin üzerine basılıyordu.
        title = safe_display_title(content)
        if not title:
            return None
        # Story içeriği için ayrı bir çevrilmiş "gövde" metni üretilmiyor
        # (sadece kısa summary_text) — haberin ham (İngilizce) description'ını
        # alt açıklama olarak göstermek başlıkla dil tutarsızlığına yol açar,
        # bu yüzden story'lerde subtitle hiç gösterilmez.
        subtitle = None

        try:
            # 1. Arka plan (belirli bir habere ait olduğundan gerçek haber
            # görseli/stok foto denenir)
            seed = content_id if content_id is not None else abs(hash(title))
            img = self.img_gen._get_background(
                STORY_SIZE, category, seed,
                image_url=content.get("image_url"),
                manual_image_path=content.get("manual_image_path"),
                game_title=content.get("news_title"),
                news_url=content.get("news_url") or content.get("url"),
                relevance_text=title,
            )

            # 2. Dekoratif elementler
            img = self._add_story_decorations(img, category)
            draw = ImageDraw.Draw(img)

            width, height = STORY_SIZE

            # 3. Üst kısım - Kategori balonu
            self._draw_category_bubble(draw, width, category)

            # 4. "SON DAKİKA" veya "GÜNDEM" etiketi
            self._draw_breaking_label(draw, width, category)

            # 5. Ana başlık (büyük font, ortada)
            img, draw = self._draw_story_title(img, draw, width, height, title, category, subtitle=subtitle)

            # 6. Kaynak bilgisi
            source = content.get("source_name", "")
            if source:
                self._draw_story_source(draw, width, height, source)

            # 7. Alt kısım - CTA (Call to Action)
            cta_text = "📌 Detaylar profilde" if sibling_post_published else "👆 Daha fazlası için takip et"
            self._draw_cta(draw, width, height, cta_text)

            # 7b. Marka logosu (varsa) — yoksa görsel değişmez. Bu, gerçekte
            # kullanılan tekil hikaye üretim yolu olduğundan (bkz.
            # generate_daily_summary_stories/_create_cta_slide — o ayrı,
            # pipeline'da hiç çağrılmayan kullanılmayan bir yol), logo
            # buraya eklenmezse yayınlanan hikayelerin HİÇBİRİNDE marka
            # görünmüyordu. Postlarla aynı marka kalitesi için sadece rozet
            # değil, marka adını da içeren yatay logo kullanılır
            # (bkz. image_generator.py:_draw_brand_mark — postlarda kullanılan
            # aynı görsel).
            logo = self.img_gen._get_logo("horizontal")
            if logo is not None:
                target_h = 40
                ratio = target_h / logo.height
                resized = logo.resize((int(logo.width * ratio), target_h))
                img.paste(resized, ((width - resized.width) // 2, height - 165), resized)

            # 8. Swipe up / daha fazla göstergesi
            self._draw_swipe_indicator(draw, width, height, category)

            # Kaydet
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"story_{category}_{timestamp}.png"
            filepath = str(STORIES_OUTPUT_DIR / filename)
            
            img.save(filepath, "PNG", quality=95)
            logger.info(f"📱 Hikaye görseli oluşturuldu: {filename}")

            if content_id:
                self.db.update_content_media(content_id, filepath)
                # `generate_post_image` bunu her zaman yazıyordu, story
                # tarafında EKSİKTİ — `background_source` hiç kaydedilmediği
                # için "story'lerde gerçek görsel oranı nedir" sorusu hiç
                # ölçülemiyordu (11 Ağustos 2026 denetiminde fark edildi:
                # az önce yayınlanan 6 story'nin 6'sında da alan NULL'dı).
                kaynak = self.img_gen._last_background_source or "bilinmiyor"
                self.db.set_background_source(content_id, kaynak)

            return filepath

        except Exception as e:
            logger.error(f"Hikaye oluşturma hatası: {e}")
            return None

    def generate_daily_summary_stories(self, news_list: list[dict]) -> list[str]:
        """Günlük haber özeti hikaye serisi oluştur (birden fazla slide)."""
        filepaths = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Slide 1: Kapak
        cover_path = self._create_cover_slide(timestamp)
        if cover_path:
            filepaths.append(cover_path)

        # Slide 2-N: Haberler
        for i, news in enumerate(news_list[:5]):  # Maks 5 haber slide
            try:
                path = self._create_news_slide(news, i + 1, len(news_list[:5]), timestamp)
                if path:
                    filepaths.append(path)
            except Exception as e:
                logger.error(f"Haber slide {i+1} hatası: {e}")

        # Son slide: CTA
        cta_path = self._create_cta_slide(timestamp)
        if cta_path:
            filepaths.append(cta_path)

        logger.info(f"📱 Hikaye serisi oluşturuldu: {len(filepaths)} slide")
        return filepaths

    # =============================================
    # ÖZEL SLIDE OLUŞTURMA
    # =============================================

    def _create_cover_slide(self, timestamp: str) -> str | None:
        """Kapak slide'ı oluştur."""
        try:
            img = self.img_gen._create_gradient_background(STORY_SIZE, "ai")
            img = self._add_story_decorations(img, "ai")
            draw = ImageDraw.Draw(img)
            width, height = STORY_SIZE

            # Tarih
            date_text = datetime.now().strftime("%d %B %Y").replace(
                "January", "Ocak").replace("February", "Şubat").replace(
                "March", "Mart").replace("April", "Nisan").replace(
                "May", "Mayıs").replace("June", "Haziran").replace(
                "July", "Temmuz").replace("August", "Ağustos").replace(
                "September", "Eylül").replace("October", "Ekim").replace(
                "November", "Kasım").replace("December", "Aralık")
            
            font_date = self.img_gen._get_font("body", 28)
            bbox = draw.textbbox((0, 0), date_text, font=font_date)
            text_w = bbox[2] - bbox[0]
            draw.text(
                ((width - text_w) // 2, height // 2 - 180),
                date_text, font=font_date,
                fill=self.img_gen._hex_to_rgb(COLORS["text_secondary"])
            )

            # Ana başlık
            title = "GÜNÜN HABERLERİ"
            font_title = self.img_gen._get_font("accent", 64)
            bbox = draw.textbbox((0, 0), title, font=font_title)
            text_w = bbox[2] - bbox[0]
            draw.text(
                ((width - text_w) // 2, height // 2 - 100),
                title, font=font_title,
                fill=self.img_gen._hex_to_rgb(COLORS["text_primary"])
            )

            # Alt başlık
            subtitle = "🤖 AI  &  🎮 Gaming"
            font_sub = self.img_gen._get_font("subtitle", 36)
            text_w, _ = self.img_gen._measure_mixed_text(draw, subtitle, font_sub)
            self.img_gen._draw_mixed_text(
                draw, ((width - text_w) // 2, height // 2 + 10),
                subtitle, font_sub,
                fill=self.img_gen._hex_to_rgb(COLORS["accent_2"])
            )

            # Swipe
            self._draw_swipe_indicator(draw, width, height, "ai")

            filename = f"story_cover_{timestamp}.png"
            filepath = str(STORIES_OUTPUT_DIR / filename)
            img.save(filepath, "PNG", quality=95)
            return filepath
        except Exception as e:
            logger.error(f"Kapak slide hatası: {e}")
            return None

    def _create_news_slide(self, news: dict, index: int, total: int,
                           timestamp: str) -> str | None:
        """Haber slide'ı oluştur."""
        try:
            category = news.get("category", "ai")
            title_for_seed = news.get("title", news.get("summary_text", ""))
            seed = news.get("id") if news.get("id") is not None else abs(hash(title_for_seed))
            img = self.img_gen._get_background(
                STORY_SIZE, category, seed, image_url=news.get("image_url"),
                game_title=news.get("title"),
                relevance_text=title_for_seed,
            )
            img = self._add_story_decorations(img, category)
            draw = ImageDraw.Draw(img)
            width, height = STORY_SIZE

            # Slide numarası
            font_num = self.img_gen._get_font("accent", 80)
            num_text = f"{index}"
            bbox = draw.textbbox((0, 0), num_text, font=font_num)
            num_w = bbox[2] - bbox[0]
            
            # Numara dairesi
            cx = width // 2
            cy = 300
            radius = 55
            accent_color = self.img_gen._hex_to_rgb(
                COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"]
            )
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=accent_color
            )
            draw.text(
                ((width - num_w) // 2, cy - 45),
                num_text, font=font_num,
                fill=(255, 255, 255)
            )

            # Slide sayacı
            counter_text = f"{index}/{total}"
            font_counter = self.img_gen._get_font("body", 20)
            bbox = draw.textbbox((0, 0), counter_text, font=font_counter)
            cw = bbox[2] - bbox[0]
            draw.text(
                ((width - cw) // 2, cy + radius + 20),
                counter_text, font=font_counter,
                fill=self.img_gen._hex_to_rgb(COLORS["text_secondary"])
            )

            # Kategori
            cat_label = "🤖 AI" if category == "ai" else "🎮 GAMING"
            font_cat = self.img_gen._get_font("subtitle", 24)
            cat_w, _ = self.img_gen._measure_mixed_text(draw, cat_label, font_cat)
            self.img_gen._draw_mixed_text(
                draw, ((width - cat_w) // 2, 450),
                cat_label, font_cat,
                fill=accent_color
            )

            # Haber başlığı
            title = news.get("title", news.get("summary_text", ""))
            subtitle = self.img_gen._truncate_subtitle(news.get("description"))
            font_title = self.img_gen._get_font("accent", 44)
            lines = self.img_gen._wrap_text(title, font_title, width - 120, draw)[:6]

            y_offset = 540
            line_height = 56
            img, draw = self.img_gen._draw_headline_block(
                img, (0, y_offset), lines, font_title,
                fill=(255, 255, 255), glow_color=accent_color, line_height=line_height,
                category=category, subtitle=subtitle, center_width=width,
                subtitle_max_width=width - 120
            )
            y_offset += line_height * len(lines)
            if subtitle:
                y_offset += 40

            # Kaynak
            source = news.get("source_name", "")
            if source:
                source_text = f"📰 {source}"
                font_source = self.img_gen._get_font("body", 22)
                sw, _ = self.img_gen._measure_mixed_text(draw, source_text, font_source)
                self.img_gen._draw_mixed_text(
                    draw, ((width - sw) // 2, y_offset + 40),
                    source_text, font_source,
                    fill=self.img_gen._hex_to_rgb(COLORS["text_secondary"])
                )

            # Swipe
            self._draw_swipe_indicator(draw, width, height, category)

            filename = f"story_news_{timestamp}_{index:02d}.png"
            filepath = str(STORIES_OUTPUT_DIR / filename)
            img.save(filepath, "PNG", quality=95)
            return filepath
        except Exception as e:
            logger.error(f"Haber slide hatası: {e}")
            return None

    def _create_cta_slide(self, timestamp: str) -> str | None:
        """CTA (Call to Action) son slide."""
        try:
            img = self.img_gen._create_gradient_background(STORY_SIZE, "gaming")
            img = self._add_story_decorations(img, "gaming")
            draw = ImageDraw.Draw(img)
            width, height = STORY_SIZE

            # Emoji
            font_emoji = self.img_gen._get_font("accent", 80)
            self.img_gen._draw_mixed_text(
                draw, ((width - 80) // 2, height // 2 - 200),
                "🔔", font_emoji, fill=(255, 255, 255)
            )

            # Ana metin
            cta_text = "TAKİP ET"
            font_cta = self.img_gen._get_font("accent", 60)
            bbox = draw.textbbox((0, 0), cta_text, font=font_cta)
            tw = bbox[2] - bbox[0]
            draw.text(
                ((width - tw) // 2, height // 2 - 60),
                cta_text, font=font_cta, fill=(255, 255, 255)
            )

            # Alt metin
            sub_text = "Günlük AI & Gaming haberlerini\nkaçırma!"
            font_sub = self.img_gen._get_font("body", 32)
            last_line_y = height // 2 + 40
            for i, line in enumerate(sub_text.split("\n")):
                bbox = draw.textbbox((0, 0), line, font=font_sub)
                lw = bbox[2] - bbox[0]
                last_line_y = height // 2 + 40 + i * 44
                draw.text(
                    ((width - lw) // 2, last_line_y),
                    line, font=font_sub,
                    fill=self.img_gen._hex_to_rgb(COLORS["text_secondary"])
                )

            # Marka logosu (varsa) — yoksa görsel değişmez
            logo = self.img_gen._get_logo("mark")
            if logo is not None:
                target_h = 48
                ratio = target_h / logo.height
                resized = logo.resize((int(logo.width * ratio), target_h))
                logo_y = last_line_y + 70
                img.paste(resized, ((width - resized.width) // 2, logo_y), resized)

            filename = f"story_cta_{timestamp}.png"
            filepath = str(STORIES_OUTPUT_DIR / filename)
            img.save(filepath, "PNG", quality=95)
            return filepath
        except Exception as e:
            logger.error(f"CTA slide hatası: {e}")
            return None

    # =============================================
    # DEKORASYON VE ELEMENTLER
    # =============================================

    def _add_story_decorations(self, img: Image.Image, category: str) -> Image.Image:
        """Hikaye için dekoratif elementler."""
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        width, height = img.size

        if category == "ai":
            accent = self.img_gen._hex_to_rgb(COLORS["primary"])
        else:
            accent = self.img_gen._hex_to_rgb(COLORS["secondary"])

        # Üst ve alt gradient bar
        for y in range(60):
            alpha = int(80 * (1 - y / 60))
            draw.line([(0, y), (width, y)], fill=accent + (alpha,))
            draw.line([(0, height - y - 1), (width, height - y - 1)], fill=accent + (alpha,))

        # Köşe dekorasyonları
        corner_size = 120
        # Sol üst
        for i in range(corner_size):
            alpha = int(40 * (1 - i / corner_size))
            draw.line([(i, 0), (0, i)], fill=accent + (alpha,))
        # Sağ alt
        for i in range(corner_size):
            alpha = int(40 * (1 - i / corner_size))
            draw.line(
                [(width - i - 1, height - 1), (width - 1, height - i - 1)],
                fill=accent + (alpha,)
            )

        img = img.convert("RGBA")
        img = Image.alpha_composite(img, overlay)
        return img.convert("RGB")

    def _draw_category_bubble(self, draw: ImageDraw.Draw, width: int, category: str):
        """Kategori balonu çiz."""
        # Etiket ve emoji config.CATEGORIES'ten — image_generator ile aynı
        # kaynak, artık iki yerde ayrı ayrı yazılıp birbirinden kopmuyor.
        kat = CATEGORIES.get(category, CATEGORIES["gaming"])
        full_label = f"{kat['emoji']} {kat['label']}"
        
        font = self.img_gen._get_font("subtitle", 22)
        text_w, text_h = self.img_gen._measure_mixed_text(draw, full_label, font)

        pad = 14
        x = (width - text_w - pad * 2) // 2
        y = 100

        color = self.img_gen._hex_to_rgb(
            COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"]
        )
        draw.rounded_rectangle(
            [x, y, x + text_w + pad * 2, y + text_h + pad * 2],
            radius=20, fill=color
        )
        self.img_gen._draw_mixed_text(
            draw, (x + pad, y + pad), full_label, font,
            fill=(255, 255, 255)
        )

    def _draw_breaking_label(self, draw: ImageDraw.Draw, width: int, category: str):
        """'SON DAKİKA' veya 'GÜNDEM' etiketi."""
        label = STORY_BADGE_TEXT
        font = self.img_gen._get_font("accent", 20)
        text_w, _ = self.img_gen._measure_mixed_text(draw, label, font)

        self.img_gen._draw_mixed_text(
            draw, ((width - text_w) // 2, 170), label, font,
            fill=self.img_gen._hex_to_rgb(COLORS["accent"])
        )

    def _draw_story_title(self, img: Image.Image, draw: ImageDraw.Draw, width: int,
                           height: int, title: str, category: str = "ai",
                           subtitle: str = None) -> tuple:
        """Hikaye ana başlığı (glow efektiyle + vurgu kutusu). Güncellenmiş (img, draw) döner."""
        font = self.img_gen._get_font("accent", 52)
        padding = 60

        lines = self.img_gen._wrap_text(title, font, width - padding * 2, draw)
        lines = lines[:5]

        line_height = 66
        total_h = len(lines) * line_height
        y_start = (height - total_h) // 2 - 40

        glow_color = self.img_gen._hex_to_rgb(
            COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"]
        )
        return self.img_gen._draw_headline_block(
            img, (0, y_start), lines, font,
            fill=(255, 255, 255), glow_color=glow_color, line_height=line_height,
            category=category, subtitle=subtitle, center_width=width,
            subtitle_max_width=width - padding * 2
        )

    def _draw_story_source(self, draw: ImageDraw.Draw, width: int, height: int, source: str):
        """Hikaye kaynak bilgisi."""
        text = f"📰 {source}"
        font = self.img_gen._get_font("body", 24)
        tw, _ = self.img_gen._measure_mixed_text(draw, text, font)

        self.img_gen._draw_mixed_text(
            draw, ((width - tw) // 2, int(height * 0.72)),
            text, font,
            fill=self.img_gen._hex_to_rgb(COLORS["text_secondary"])
        )

    def _draw_cta(self, draw: ImageDraw.Draw, width: int, height: int,
                  text: str = "👆 Daha fazlası için takip et"):
        """Call to Action alanı."""
        font = self.img_gen._get_font("body", 22)
        tw, _ = self.img_gen._measure_mixed_text(draw, text, font)

        self.img_gen._draw_mixed_text(
            draw, ((width - tw) // 2, height - 200),
            text, font,
            fill=self.img_gen._hex_to_rgb(COLORS["text_secondary"])
        )

    def _draw_swipe_indicator(self, draw: ImageDraw.Draw, width: int,
                               height: int, category: str):
        """Swipe göstergesi (küçük ok)."""
        cx = width // 2
        cy = height - 120
        
        color = self.img_gen._hex_to_rgb(
            COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"]
        )
        
        # Yukarı ok
        arrow_size = 12
        draw.polygon(
            [(cx, cy - arrow_size), (cx - arrow_size, cy + arrow_size // 2),
             (cx + arrow_size, cy + arrow_size // 2)],
            fill=color
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    
    gen = StoryGenerator()
    
    # Test hikayesi
    test = {
        "category": "gaming",
        "summary_text": "ÖRNEK BAŞLIK: Oyun Şablonunu Denemek İçin Üretilmiş Test Metnidir",
        "news_title": "Örnek Oyun Başlığı (test)",
        "source_name": "Örnek Kaynak"
    }
    
    path = gen.generate_story_image(news_data=test)
    if path:
        print(f"\n✅ Test hikayesi: {path}")
