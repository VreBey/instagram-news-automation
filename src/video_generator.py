"""
Video Üretim Modülü (Reels)
MoviePy ve Edge-TTS ile Instagram Reels videoları oluşturur.
"""

import asyncio
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    COLORS, FONTS, FONT_SIZES, REELS_SIZE, REELS_CONFIG,
    FONTS_DIR, REELS_OUTPUT_DIR, MUSIC_DIR, LOGO_MARK_PATH, REELS_SILENT,
    BRAND_HANDLE, REELS_CTA_TEXT, CATEGORIES
)
from src.database import Database
from src.content_language import is_probably_turkish
from src.image_generator import ImageGenerator

logger = logging.getLogger(__name__)

# MoviePy import
try:
    from moviepy.editor import (
        ImageClip, AudioFileClip, CompositeVideoClip, VideoFileClip,
        concatenate_videoclips, CompositeAudioClip, ColorClip
    )
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    logger.warning("moviepy kütüphanesi bulunamadı. Video üretimi devre dışı.")

# Edge-TTS import
try:
    import edge_tts
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    logger.warning("edge-tts kütüphanesi bulunamadı. Sesli anlatım devre dışı.")


class VideoGenerator:
    """Instagram Reels videoları oluşturur."""

    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.img_gen = ImageGenerator(db=self.db)
        REELS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def generate_reels(self, content_id: int = None,
                       reels_data: dict = None) -> str | None:
        """Reels videosu oluştur."""
        if not MOVIEPY_AVAILABLE:
            logger.error("MoviePy yüklü değil. Reels oluşturulamıyor.")
            return None

        if content_id:
            content = self.db.get_content_by_id(content_id)
            if not content:
                logger.error(f"İçerik bulunamadı: ID={content_id}")
                return None
            script = content.get("reels_script", {})
            category = content.get("category", "ai")
        elif reels_data:
            script = reels_data.get("script", reels_data)
            category = reels_data.get("category", "ai")
        else:
            return None

        if not script or not script.get("segments"):
            logger.error("Reels senaryosu boş veya geçersiz.")
            return None

        try:
            logger.info("🎬 Reels video üretimi başlıyor...")

            # 1. Her segment için görsel kart oluştur
            slide_paths = self._create_slide_images(script, category)
            if not slide_paths:
                logger.error("Slide görselleri oluşturulamadı.")
                return None

            # 2. Ses: REELS_SILENT açıkken hiç üretilmez.
            #    Müzik Instagram uygulamasında yayın sırasında ekleniyor
            #    (Instagram müzik kütüphanesi Graph API'den erişilemiyor),
            #    videoda ses olsaydı orada çakışırdı. Bkz. config.REELS_SILENT.
            audio_path = None
            if not REELS_SILENT and TTS_AVAILABLE:
                audio_path = self._generate_tts_audio(script)

            # 3. Videoyu birleştir
            video_path = self._compose_video(slide_paths, audio_path, category)

            # 4. Kapak görselini kalıcı hale getir (ilk haber slide'ı — Reels cover_url için)
            cover_path = None
            if len(slide_paths) > 1:
                cover_source = slide_paths[1]  # slide_paths[0] intro, [1] ilk haber slide'ı
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                cover_path = str(REELS_OUTPUT_DIR / f"reels_cover_{category}_{timestamp}.png")
                try:
                    import shutil
                    shutil.copyfile(cover_source, cover_path)
                except OSError as e:
                    logger.warning(f"Kapak görseli kopyalanamadı: {e}")
                    cover_path = None

            # 5. Geçici slide dosyalarını temizle
            for path in slide_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

            if video_path and content_id:
                self.db.update_content_media(content_id, video_path, thumbnail_path=cover_path)
                # Reels'in TİPİK arka plan kaynağını kaydet — carousel ile
                # aynı kural (çoğunluk, eşitlikte zincirde önce gelen).
                # Bu olmadan `/huni` raporu reels'i hiç görmüyordu.
                kaynaklar = getattr(self, "_last_slide_sources", [])
                if kaynaklar:
                    self.img_gen._slide_background_sources = kaynaklar
                    self.db.set_background_source(
                        content_id, self.img_gen.last_background_source())

            return video_path

        except Exception as e:
            logger.error(f"Reels oluşturma hatası: {e}")
            return None

    # =============================================
    # SLIDE GÖRSELLERİ OLUŞTURMA
    # =============================================

    def _create_slide_images(self, script: dict, category: str) -> list[str]:
        """Her segment için slide görseli oluştur."""
        slides = []
        width, height = REELS_SIZE
        
        # Giriş slide'ı
        intro_path = self._create_intro_slide(script.get("intro", ""), category)
        if intro_path:
            slides.append(intro_path)

        # Haber slide'ları
        #
        # `news_titles` BİLİNÇLİ olarak çizilmiyor. O alan ham kaynak başlığı
        # taşıyordu ve İngilizce sızıntısının kapılarından biriydi. Önce
        # sezgisel bir dil kontrolüyle korunmaya çalışıldı, ama sezgi
        # "English Source Headline" gibi dizeleri kaçırdı — değişmez testi
        # yakaladı (tests/test_no_source_language_leaks.py).
        #
        # Yapısal çözüm: alanı korumak yerine HİÇ KULLANMAMAK. Slaytta
        # gösterilecek metin zaten `segments` — Gemini'nin ürettiği Türkçe
        # anlatım. Ayrı bir başlık katmanı hem gereksiz hem de tek sızıntı
        # vektörüydü.
        segments = script.get("segments", [])
        image_urls = script.get("image_urls", [])
        # Görsel zincirinin TAMAMI reels'te de çalışsın. Eskiden yalnızca
        # `image_urls` taşınıyordu, yani og:image ve Steam kapağı basamakları
        # atlanıyor ve beslemede görsel yoksa doğrudan jenerik stok fotoğrafa
        # düşülüyordu. Canlı ölçüm (7 Ağustos 2026): 5 reels haberinin 2'si
        # stok fotoğrafa düşüyordu, oysa ikisinin de sayfasında og:image
        # vardı — tam zincirle gerçek görsel oranı %60'tan %100'e çıkıyor.
        news_urls = script.get("news_urls", [])
        game_titles = script.get("game_titles", [])
        segment_indices = script.get("segment_indices", [])
        slayt_kaynaklari: list[str] = []

        # ESKİ SENARYO SESSİZ KALMASIN. Alanlar senaryoya sonradan eklendi;
        # öncesinde yazılmış bir senaryo render edildiğinde zincirin 3. ve
        # 4. basamakları hiç çalışamaz ve doğrudan jenerik stok fotoğrafa
        # düşülür. Ölçüm (8 Ağustos 2026): bekleyen 9 reels'in 9'unda da
        # `news_urls` boştu ve yeniden render bunu kurtarmıyordu — çıktı
        # "başarılı" göründüğü için sorun görünmez kalmıştı.
        # Düzeltmesi: scripts/backfill_reels_chain.py
        if segments and not any(news_urls):
            logger.warning(
                "Reels senaryosunda `news_urls` yok — og:image ve Steam "
                "kapağı basamakları atlanacak, muhtemelen stok fotoğrafa "
                "düşülecek. Eski senaryo olabilir; "
                "scripts/backfill_reels_chain.py ile doldurulabilir."
            )

        for i, segment in enumerate(segments):
            try:
                if not is_probably_turkish(segment):
                    logger.warning(
                        f"Reels slaytı atlandı (Türkçe değil): {str(segment)[:60]}"
                    )
                    continue

                # Segmentin ait olduğu HABER indisi. Parser boş/eksik
                # segmentleri düşürebildiği için listedeki sıra (`i`) haber
                # sırasıyla aynı olmayabilir; `segment_indices` numaradan
                # türetiliyor ve doğru eşlemeyi veriyor. Bu olmadan ortadaki
                # bir segment düştüğünde sonraki her haber BAŞKA haberin
                # fotoğrafıyla eşleşiyordu.
                h = segment_indices[i] if i < len(segment_indices) else i
                al = lambda liste: liste[h] if h < len(liste) else None
                slide_path = self._create_news_reel_slide(
                    segment_text=segment,
                    title="",
                    index=i + 1,
                    total=len(segments),
                    category=category,
                    image_url=al(image_urls),
                    news_url=al(news_urls),
                    game_title=al(game_titles),
                )
                if slide_path:
                    slides.append(slide_path)
                    # Reels de ölçüme girsin. Eskiden girmiyordu ve
                    # `/huni` raporundaki "gerçek görsel oranı" reels'i HİÇ
                    # görmüyordu — kullanıcının şikayet ettiği yüzey tam da
                    # ölçümün kör noktasıydı.
                    kaynak = self.img_gen._last_background_source
                    if kaynak:
                        slayt_kaynaklari.append(kaynak)
            except Exception as e:
                logger.error(f"Slide {i+1} oluşturma hatası: {e}")

        # Kapak/çıkış slaytları bilinçli olarak gradyan; ölçüme yalnızca
        # HABER slaytları giriyor.
        self._last_slide_sources = slayt_kaynaklari

        # Çıkış slide'ı
        outro_path = self._create_outro_slide(script.get("outro", ""), category)
        if outro_path:
            slides.append(outro_path)

        return slides

    def _create_intro_slide(self, text: str, category: str) -> str | None:
        """Giriş slide'ı.

        Yeniden tasarlandı (21 Ağustos 2026, kullanıcı geri bildirimi:
        "çok basic kalıyor"). Eskiden bu slayt haber slaytlarında ZATEN
        var olan polish tekniklerinin HİÇBİRİNİ kullanmıyordu — düz 2px
        siyah gölge (glow yok), editoryal üst bar yok, marka rozeti yok.
        Tek slayt olsa da video izleyicinin gördüğü İLK kare; şablondan
        çıkma hissi tam burada başlıyordu. Artık haber slaytlarıyla AYNI
        dil: `_draw_top_bar` (kategori etiketi + logo rozeti),
        `_draw_glow_text` (yumuşak renkli glow), `_draw_brand_centered`.
        """
        try:
            img = self.img_gen._create_gradient_background(REELS_SIZE, category)
            img = self.img_gen._add_decorative_elements(img, category)
            draw = ImageDraw.Draw(img)
            width, height = REELS_SIZE

            self.img_gen._draw_top_bar(draw, REELS_SIZE, category, img=img)

            accent = self.img_gen._hex_to_rgb(
                COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"]
            )

            # Büyük emoji — config.CATEGORIES'ten, story/caption ile aynı kaynak
            emoji = CATEGORIES.get(category, CATEGORIES["gaming"])["emoji"]
            font_emoji = self.img_gen._get_font("accent", 100)
            ew, _ = self.img_gen._measure_mixed_text(draw, emoji, font_emoji)
            self.img_gen._draw_mixed_text(
                draw, ((width - ew) // 2, height // 2 - 200), emoji, font_emoji, fill=(255, 255, 255)
            )

            # Ana metin — düz siyah gölge yerine haber slaytlarıyla AYNI
            # yumuşak, kategori renkli glow (bkz. _draw_glow_text).
            if not text:
                text = "Günün Haberleri!"
            font_title = self.img_gen._get_font("accent", 56)
            lines = self.img_gen._wrap_text(text, font_title, width - 120, draw)

            y = height // 2 - 50
            img, draw = self.img_gen._draw_glow_text(
                img, (0, y), lines, font_title, fill=(255, 255, 255),
                glow_color=accent, line_height=70, center_width=width
            )

            # Marka rozeti — haber slaytlarında zaten var, introda hiç
            # yoktu; ilk kare markasız kalıyordu.
            font_brand = self.img_gen._get_font("body", 20)
            self._draw_brand_centered(
                draw, img, width // 2, height - 140,
                font_brand, self.img_gen._hex_to_rgb(COLORS["text_accent"]), logo_height=28
            )

            # TARİH DAMGASI KALDIRILDI.
            #
            # `datetime.now()` RENDER anını yazıyordu, içeriğin anlattığı
            # dönemi değil. Ölçüm (8 Ağustos 2026): 5-7 Ağustos haberlerini
            # anlatan taslaklar yeniden render edilince giriş slaytında
            # "08.08.2026" yazdı. Üstünde de "Bugünün en önemli haberleri"
            # başlığı vardı — yani ekranda, doğru olmayan bir tazelik
            # iddiası duruyordu.
            #
            # Damga zaten hiçbir zaman doğru olamazdı: reels havuzu SON 7
            # GÜNDE yayınlanmış gönderilerden derleniyor (bkz.
            # get_published_for_reels), tek bir güne ait değil. Yanlış bir
            # tarih göstermektense hiç göstermemek doğru.

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                               dir=str(REELS_OUTPUT_DIR))
            img.save(tmp.name, "PNG")
            return tmp.name
        except Exception as e:
            logger.error(f"Intro slide hatası: {e}")
            return None

    def _create_news_reel_slide(self, segment_text: str, title: str,
                                 index: int, total: int, category: str,
                                 image_url: str = None, news_url: str = None,
                                 game_title: str = None) -> str | None:
        """Haber slide'ı (Reels format).

        Zincire `news_url` ve `game_title` de geçilir — tekil gönderi ve
        carousel yollarıyla aynı basamaklar çalışsın diye. Eskiden yalnızca
        `image_url` geçiliyordu ve reels, zincirin og:image + Steam kapağı
        basamaklarını hiç kullanamıyordu.
        """
        try:
            seed_text = title or segment_text
            seed = abs(hash(seed_text))
            img = self.img_gen._get_background(
                REELS_SIZE, category, seed, image_url=image_url,
                news_url=news_url, game_title=game_title,
                relevance_text=seed_text)
            draw = ImageDraw.Draw(img)
            width, height = REELS_SIZE

            # Numara balonu
            accent_color = self.img_gen._hex_to_rgb(
                COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"]
            )
            
            # BANT DÜZENİ varsa numara balonu bandın ÜSTÜNDEKİ temiz boşluğa
            # konur; sabit y=350 kalsaydı balon görselin üzerine binerdi
            # (ilk denemede tam olarak bu oldu, "QWEN" yazısının üstüne bindi).
            bant = getattr(self.img_gen, "_last_band_rect", None)
            cx = width // 2
            cy = (bant[0] // 2) if bant else 350
            radius = 50
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=accent_color
            )
            num_font = self.img_gen._get_font("accent", 60)
            num_text = str(index)
            # `anchor="mm"` (Pillow ≥8.0) rakamı balonun TAM merkezine
            # oturtur. Eskiden manuel textbbox hesabıyla ("- 5" gibi elle
            # ayarlanmış bir düzeltmeyle) yapılıyordu — bbox'ın üst/sol
            # kenarı fontun ascender/bearing metriklerine göre değiştiğinden
            # bu sabit düzeltme sadece TEK bir font/boyut için doğruydu,
            # rakam balonun içinde gözle görülür şekilde kaymış duruyordu
            # (kullanıcı geri bildirimi: "1 2 3 4 rakamları ortalı değil").
            draw.text((cx, cy), num_text, font=num_font, fill=(255, 255, 255), anchor="mm")

            # Haber metni (segment — TTS okuyacak)
            font_text = self.img_gen._get_font("accent", 44)
            display_text = title if title else segment_text
            lines = self.img_gen._wrap_text(display_text, font_text, width - 100, draw)[:7]

            # Metin, bandın ALTINDAKİ boşluğa DİKEY ORTALANIR. Sabit
            # `height // 2` kalsaydı metin bandın üstüne biner, altta da
            # ekranın yarısı kadar ölü alan kalırdı.
            if bant:
                alan_ust, alan_alt = bant[1] + 40, height - 220
                y = (alan_ust + alan_alt) // 2 - len(lines) * 29
            else:
                y = height // 2 - len(lines) * 30
            img, draw = self.img_gen._draw_headline_block(
                img, (0, y), lines, font_text,
                fill=(255, 255, 255), glow_color=accent_color, line_height=58,
                category=category, center_width=width
            )

            # Progress bar
            bar_y = height - 180
            bar_width = width - 160
            bar_x = 80
            bar_height = 6
            
            # Arka plan
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + bar_width, bar_y + bar_height],
                radius=3, fill=(255, 255, 255, 50)
            )
            # İlerleme
            progress_width = int(bar_width * index / total)
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + progress_width, bar_y + bar_height],
                radius=3, fill=accent_color
            )

            # Hesap bilgisi (logo varsa logo, yoksa metin)
            font_brand = self.img_gen._get_font("body", 20)
            self._draw_brand_centered(
                draw, img, width // 2, height - 140,
                font_brand, self.img_gen._hex_to_rgb(COLORS["text_accent"]), logo_height=28
            )

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                               dir=str(REELS_OUTPUT_DIR))
            img.save(tmp.name, "PNG")
            return tmp.name
        except Exception as e:
            logger.error(f"News reel slide hatası: {e}")
            return None

    def _create_outro_slide(self, text: str, category: str) -> str | None:
        """Çıkış slide'ı.

        _create_intro_slide ile AYNI gerekçeyle yeniden tasarlandı (21
        Ağustos 2026, kullanıcı geri bildirimi): düz 2px siyah gölge
        yerine haber slaytlarıyla aynı yumuşak glow, ve tutarlılık için
        aynı editoryal üst bar eklendi.
        """
        try:
            img = self.img_gen._create_gradient_background(REELS_SIZE, category)
            img = self.img_gen._add_decorative_elements(img, category)
            draw = ImageDraw.Draw(img)
            width, height = REELS_SIZE

            self.img_gen._draw_top_bar(draw, REELS_SIZE, category, img=img)

            accent = self.img_gen._hex_to_rgb(
                COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"]
            )

            # CTA metni
            if not text:
                text = REELS_CTA_TEXT

            # Bell emoji
            font_emoji = self.img_gen._get_font("accent", 100)
            self.img_gen._draw_mixed_text(
                draw, ((width - 100) // 2, height // 2 - 200), "🔔",
                font_emoji, fill=(255, 255, 255)
            )

            # Ana metin — düz siyah gölge yerine yumuşak, kategori renkli glow.
            font_title = self.img_gen._get_font("accent", 48)
            lines = self.img_gen._wrap_text(text, font_title, width - 120, draw)

            y = height // 2 - 40
            img, draw = self.img_gen._draw_glow_text(
                img, (0, y), lines, font_title, fill=(255, 255, 255),
                glow_color=accent, line_height=62, center_width=width
            )
            y += len(lines) * 62

            # Hesap adı (logo varsa logo, yoksa metin)
            font_brand = self.img_gen._get_font("subtitle", 32)
            self._draw_brand_centered(
                draw, img, width // 2, y + 50,
                font_brand, self.img_gen._hex_to_rgb(COLORS["accent_2"]), logo_height=40
            )

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                               dir=str(REELS_OUTPUT_DIR))
            img.save(tmp.name, "PNG")
            return tmp.name
        except Exception as e:
            logger.error(f"Outro slide hatası: {e}")
            return None

    def _draw_brand_centered(self, draw: ImageDraw.Draw, img: Image.Image,
                              center_x: int, y: int, fallback_font, fallback_fill,
                              logo_height: int = 32):
        """Marka gösterimi: logo mark varsa ortalanmış logo, yoksa metin (mevcut davranış)."""
        logo = self.img_gen._get_logo("mark")
        if logo is not None:
            ratio = logo_height / logo.height
            resized = logo.resize((int(logo.width * ratio), logo_height))
            img.paste(resized, (center_x - resized.width // 2, y), resized)
            return

        # Logo yoksa metin yedeği; ad yapılandırılmamışsa hiç çizme.
        if not BRAND_HANDLE:
            return
        brand = BRAND_HANDLE
        bbox = draw.textbbox((0, 0), brand, font=fallback_font)
        bw = bbox[2] - bbox[0]
        draw.text((center_x - bw // 2, y), brand, font=fallback_font, fill=fallback_fill)

    # =============================================
    # TTS SESLİ ANLATIM
    # =============================================

    def _generate_tts_audio(self, script: dict) -> str | None:
        """Edge-TTS ile sesli anlatım oluştur."""
        if not TTS_AVAILABLE:
            return None

        try:
            # Tam metni birleştir
            full_text = script.get("intro", "")
            for segment in script.get("segments", []):
                full_text += f". {segment}"
            full_text += f". {script.get('outro', '')}"

            if not full_text.strip():
                return None

            # TTS ses dosyası oluştur
            voice = REELS_CONFIG["tts_voice"]
            output_path = tempfile.NamedTemporaryFile(
                suffix=".mp3", delete=False,
                dir=str(REELS_OUTPUT_DIR)
            ).name

            # Asenkron TTS çalıştır
            asyncio.run(self._async_tts(full_text, voice, output_path))
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info("🔊 TTS ses dosyası oluşturuldu")
                return output_path
            
            return None

        except Exception as e:
            logger.warning(f"TTS hatası: {e}")
            return None

    async def _async_tts(self, text: str, voice: str, output_path: str):
        """Asenkron TTS."""
        communicate = edge_tts.Communicate(text, voice, rate="+10%")
        await communicate.save(output_path)

    # =============================================
    # VİDEO BİRLEŞTİRME (MoviePy)
    # =============================================

    def _compose_video(self, slide_paths: list[str],
                       audio_path: str | None,
                       category: str) -> str | None:
        """Slide görsellerinden video oluştur."""
        if not MOVIEPY_AVAILABLE:
            return None

        try:
            duration_per_slide = REELS_CONFIG["duration_per_slide"]
            fps = REELS_CONFIG["fps"]
            
            clips = []
            
            for i, slide_path in enumerate(slide_paths):
                # Görseli video clip'e çevir
                clip = ImageClip(slide_path).set_duration(duration_per_slide)
                
                # Fade-in efekti (ilk slide hariç)
                if i > 0:
                    clip = clip.crossfadein(REELS_CONFIG["transition_duration"])
                
                clips.append(clip)

            if not clips:
                return None

            # Klipleri birleştir — NEGATİF DOLGU ŞART.
            #
            # `crossfadein` yalnızca klibe bir maske takar; kliplerin ÜST ÜSTE
            # BİNMESİNİ sağlamaz. Dolgu olmadan klipler uç uca ekleniyordu ve
            # her geçişte açılan klibin altında yalnızca siyah zemin kalıyordu:
            # yani "çapraz geçiş" değil, SİYAHTAN AÇILMA oluyordu.
            #
            # Ölçüm (8 Ağustos 2026, yayına hazır bir reels): 5. ve 10.
            # saniyede kare parlaklığı tam olarak 0.0'a düşüyordu. 7 slaytlık
            # bir videoda 6 siyah çakma demek — kullanıcının "reels kalitesi
            # çok kötü" geri bildiriminin görünür sebeplerinden biri.
            #
            # Negatif dolgu klipleri geçiş süresi kadar bindirir; alttaki
            # klip görünür kalır ve geçiş gerçekten çapraz olur. Toplam süre
            # de (n-1) × geçiş kadar kısalır, bu yüzden aşağıdaki
            # `max_duration` kırpması bu değere göre çalışır.
            final_video = concatenate_videoclips(
                clips, method="compose",
                padding=-REELS_CONFIG["transition_duration"],
            )

            # Ses ekle
            if audio_path and os.path.exists(audio_path):
                try:
                    tts_audio = AudioFileClip(audio_path)
                    
                    # Video süresine göre sesi ayarla
                    if tts_audio.duration > final_video.duration:
                        tts_audio = tts_audio.subclip(0, final_video.duration)
                    
                    audio_clips = [tts_audio]

                    # Arka plan müziği ekle (varsa)
                    bg_music = self._get_background_music()
                    if bg_music:
                        bg_audio = AudioFileClip(bg_music)
                        bg_audio = bg_audio.volumex(REELS_CONFIG["bg_music_volume"])
                        if bg_audio.duration > final_video.duration:
                            bg_audio = bg_audio.subclip(0, final_video.duration)
                        audio_clips.append(bg_audio)

                    final_audio = CompositeAudioClip(audio_clips)
                    final_video = final_video.set_audio(final_audio)
                    
                except Exception as e:
                    logger.warning(f"Ses ekleme hatası: {e}")

            # Süreyi kontrol et
            if final_video.duration > REELS_CONFIG["max_duration"]:
                final_video = final_video.subclip(0, REELS_CONFIG["max_duration"])

            # Videoyu kaydet
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"reels_{category}_{timestamp}.mp4"
            filepath = str(REELS_OUTPUT_DIR / filename)

            final_video.write_videofile(
                filepath,
                fps=fps,
                codec=REELS_CONFIG["codec"],
                audio_codec=REELS_CONFIG["audio_codec"],
                bitrate=REELS_CONFIG["bitrate"],
                logger=None  # MoviePy loglarını sustur
            )

            # Kaynakları serbest bırak
            final_video.close()
            for clip in clips:
                clip.close()

            logger.info(f"🎬 Reels videosu oluşturuldu: {filename}")
            return filepath

        except Exception as e:
            logger.error(f"Video birleştirme hatası: {e}")
            return None

    def add_logo_watermark(self, input_path: str, output_path: str) -> bool:
        """
        Kullanıcının Gemini web'de (Veo) üretip Telegram'a geri gönderdiği ham
        reels videosuna sağ üst köşede küçük, yarı saydam bir marka
        logosu bindirir. Otomatik MoviePy slaytları (ve onların üzerine
        basılan logo — bkz. _draw_brand_centered) kaldırıldığından, yeni akışta
        marka sürekliliğini sağlayan tek nokta burasıdır.
        """
        if not MOVIEPY_AVAILABLE:
            logger.warning("MoviePy yüklü değil, logo bindirilemedi — video olduğu gibi kullanılacak.")
            return False
        if not LOGO_MARK_PATH.exists():
            return False

        video_clip = None
        final = None
        try:
            video_clip = VideoFileClip(input_path)

            target_h = max(48, int(video_clip.h * 0.09))
            logo_img = Image.open(LOGO_MARK_PATH).convert("RGBA")
            ratio = target_h / logo_img.height
            logo_img = logo_img.resize((int(logo_img.width * ratio), target_h))
            alpha = logo_img.split()[-1].point(lambda p: int(p * 0.85))
            logo_img.putalpha(alpha)

            logo_clip = (
                ImageClip(np.array(logo_img))
                .set_duration(video_clip.duration)
                .margin(right=24, top=24, opacity=0)
                .set_position(("right", "top"))
            )

            final = CompositeVideoClip([video_clip, logo_clip])
            final.write_videofile(
                output_path,
                fps=video_clip.fps or REELS_CONFIG["fps"],
                codec=REELS_CONFIG["codec"],
                audio_codec=REELS_CONFIG["audio_codec"],
                logger=None,
            )
            logger.info(f"🏷️ Reels videosuna logo bindirildi: {output_path}")
            return True
        except Exception as e:
            logger.warning(f"Logo bindirme hatası: {e}")
            return False
        finally:
            if final is not None:
                final.close()
            if video_clip is not None:
                video_clip.close()

    def _get_background_music(self) -> str | None:
        """Arka plan müziği dosyası bul."""
        if not MUSIC_DIR.exists():
            return None
        
        music_files = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
        if music_files:
            return str(music_files[0])
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    
    gen = VideoGenerator()
    
    test_script = {
        "intro": "Örnek video — test verisi",
        "segments": [
            "Bu bir örnek segmenttir. Satır kaydırmasını ve segment süresini denemek için kullanılır.",
            "İkinci örnek segment. Gerçek bir haber içermez, yalnızca görsel düzeni doğrular.",
            "Üçüncü örnek segment. Uzunluğu bilerek farklı tutuldu ki hizalama görünsün.",
        ],
        "outro": "Bu bir örnek çıktıdır, yayınlamayın",
        "news_titles": [
            "Örnek Başlık Bir",
            "Örnek Başlık İki",
            "Örnek Başlık Üç"
        ],
        "category": "ai"
    }
    
    path = gen.generate_reels(reels_data=test_script)
    if path:
        print(f"\n✅ Test Reels: {path}")
