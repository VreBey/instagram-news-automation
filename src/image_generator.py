"""
Görsel Üretim Modülü (Feed Gönderileri)
Pillow ile profesyonel Instagram feed görselleri oluşturur.
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    COLORS, FONTS, FONT_SIZES, POST_SIZE_SQUARE, POST_SIZE_PORTRAIT,
    FONTS_DIR, POSTS_OUTPUT_DIR, LOGOS_DIR,
    LOGO_HORIZONTAL_PATH, LOGO_MARK_PATH, POST_LAYOUT_VARIANTS,
    BRAND_HANDLE, CATEGORIES
)
from src.database import Database
from src.stock_photos import fetch_stock_photo
from src.article_photos import fetch_article_photo, fetch_article_photo_from_page
from src.game_cover_art import fetch_game_cover_art
from src.image_layouts import LAYOUTS
from src.content_language import is_probably_turkish, safe_display_title

logger = logging.getLogger(__name__)

# Metin fontları (Inter vb.) emoji glifi içermez — PIL de tek bir draw.text()
# çağrısında iki fontu karıştıramaz. Bu yüzden emoji içeren dizeler önce
# emoji/metin parçalarına ayrılır, her parça uygun fontla çizilir.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U0000FE0F"
    "]+"
)


def _split_emoji_runs(text: str) -> list[tuple[str, bool]]:
    """Metni ardışık (parça, emoji_mi) tuple'larına böl."""
    runs = []
    pos = 0
    for match in _EMOJI_PATTERN.finditer(text):
        if match.start() > pos:
            runs.append((text[pos:match.start()], False))
        runs.append((match.group(), True))
        pos = match.end()
    if pos < len(text):
        runs.append((text[pos:], False))
    return runs


class ImageGenerator:
    """Instagram feed görselleri oluşturur."""

    def __init__(self, db: Database = None):
        self.db = db or Database()
        self.fonts_cache = {}
        self.logo_cache = {}
        # Son üretimde arka planın HANGİ kaynaktan geldiği. Kullanıcı
        # "gerçek görüntü kullanmıyor" diye bildirdiğinde bunu ölçecek hiçbir
        # veri yoktu — zincir kaynağını kaydetmiyordu, yalnızca görseli
        # döndürüyordu. Artık kaydediliyor, böylece "kaç gönderi gerçek
        # fotoğraf aldı" sorusu anekdotla değil sayıyla cevaplanabiliyor.
        self._last_background_source = None
        # Carousel'de her slayt ayrı bir kaynaktan gelebilir (ör. her oyun
        # kendi Steam kapağını alır), o yüzden slayt slayt biriktirilir.
        self._slide_background_sources = []
        # Bant düzeni kullanıldıysa (üst, alt) piksel sınırı; cover-fit'te None.
        # Slayt yerleşimi metni/numarayı buna göre konumlandırır.
        self._last_band_rect = None
        POSTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # =============================================
    # ANA GÖRSEL OLUŞTURMA
    # =============================================

    def generate_post_image(self, content_id: int = None, news_data: dict = None,
                            size: tuple = None) -> str | None:
        """Feed gönderisi için görsel oluştur (layout varyantları arasında deterministik seçim)."""

        if content_id:
            content = self.db.get_content_by_id(content_id)
            if not content:
                logger.error(f"İçerik bulunamadı: ID={content_id}")
                return None
        elif news_data:
            content = news_data
        else:
            logger.error("content_id veya news_data gerekli.")
            return None

        # Haber, isimlendirilmiş birden fazla öge içeren bir LİSTE olarak
        # tespit edildiyse (bkz. content_processor._ai_detect_list_content),
        # tekil görsel yerine kaydırmalı (carousel) görsel üretilir — kullanıcı
        # geri bildirimi: "daha fazla detay vermek gerekirse feed içerisinde
        # kaydırmalı şekilde içerikler yapılabilmeli".
        # DİL KAPISI HER YOLDA VAR ama her yol KENDİ ÇİZDİĞİ metni denetler.
        # Carousel `summary_text` kullanmaz — kapak metnini
        # `list_items["cover"]`den alır, o yüzden kapısı da orada
        # (bkz. _generate_post_carousel). Buradaki `safe_display_title`'ı
        # carousel dalının önüne taşımak, özeti olmayan ama geçerli liste
        # içeriklerini öldürür; bu hata bir kez yapıldı ve testler yakaladı.
        if content_id and content.get("list_items"):
            carousel_path = self._generate_post_carousel(content_id, content)
            if carousel_path:
                return carousel_path
            # Carousel üretimi başarısız olduysa (ör. yetersiz slayt ya da
            # kapak dil kapısına takıldıysa), aşağıdaki tekil görsel akışına
            # düşülür — o akış Türkçe özeti kullanır, içerik hiç kaybolmasın.

        size = size or POST_SIZE_PORTRAIT  # Varsayılan 1080x1350
        category = content.get("category", "ai")

        # Tekil görselin çizdiği metin `summary_text`: çeviri yoksa ya da özet
        # kaynak başlığın kopyasıysa görsel HİÇ üretilmez. Eskiden burada
        # `summary_text or news_title` vardı ve Gemini özeti üretemediğinde
        # ham İngilizce başlık doğrudan görselin üzerine basılıyordu.
        title = safe_display_title(content)
        if not title:
            return None
        source = content.get("source_name", "")
        image_url = content.get("image_url")
        manual_image_path = content.get("manual_image_path")
        # Steam'de aranacak sorgu için HER ZAMAN ham İngilizce news_title
        # kullanılır (summary_text Gemini'nin ürettiği Türkçe metin olabilir
        # ve oyun adının orijinal biçimini bozabilir/eksik bırakabilir).
        game_title = content.get("news_title")
        news_url = content.get("news_url") or content.get("url")
        # Altyazı YALNIZCA üretilmiş Türkçe caption'dan çıkarılır.
        # Eskiden `... or content.get("description")` vardı ve caption'dan
        # cümle çıkarılamadığında ham İNGİLİZCE haber açıklaması görselin
        # üzerine basılıyordu.
        subtitle = self._truncate_subtitle(
            self._extract_subtitle_from_caption(content.get("caption"))
        )

        try:
            # Aynı içerik yeniden üretildiğinde hep aynı şablonu versin diye
            # deterministik bir seed kullanılır (rastgele değil).
            seed = content_id if content_id is not None else abs(hash(title))
            variant = self._select_layout_variant(seed)

            render = LAYOUTS[variant]
            img = render(
                self, size, category, title, source, seed,
                image_url=image_url, manual_image_path=manual_image_path, subtitle=subtitle,
                game_title=game_title, news_url=news_url
            )

            # Dosyayı kaydet
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"post_{category}_{timestamp}.png"
            filepath = str(POSTS_OUTPUT_DIR / filename)

            img.save(filepath, "PNG", quality=95)
            kaynak = self._last_background_source or "bilinmiyor"
            logger.info(
                f"📸 Feed görseli oluşturuldu ({variant}, arka plan: {kaynak}): {filename}"
            )

            # Veritabanını güncelle
            if content_id:
                self.db.update_content_media(content_id, filepath)
                self.db.set_background_source(content_id, kaynak)

            return filepath

        except Exception as e:
            logger.error(f"Görsel oluşturma hatası: {e}")
            return None

    # Zincirin öncelik sırası — eşitlik bozmak için kullanılır (öndeki kazanır).
    _BACKGROUND_CHAIN = ("manual", "article_image", "og_image",
                         "game_cover", "stock_photo", "gradient")

    def last_background_source(self) -> str:
        """Son üretilen carousel'in TİPİK arka plan kaynağı: slaytların çoğunluğu.

        Tek bir içeriğe tek bir kaynak yazılıyor, ama carousel'de slaytlar
        farklı kaynaklardan gelebiliyor. Çoğunluk seçiliyor ki "1 slayt
        gerçek, 4 slayt gradyan" olan bir gönderi rapora "gerçek görsel"
        diye girmesin. Eşitlikte zincirde önce gelen (daha iyi) kaynak
        kazanır.
        """
        kaynaklar = [k for k in self._slide_background_sources if k]
        if not kaynaklar:
            return "bilinmiyor"
        sira = {k: i for i, k in enumerate(self._BACKGROUND_CHAIN)}
        return max(set(kaynaklar),
                   key=lambda k: (kaynaklar.count(k), -sira.get(k, 99)))

    def _select_layout_variant(self, seed: int) -> str:
        """İçeriğe göre deterministik layout varyantı seç (rastgele değil)."""
        return POST_LAYOUT_VARIANTS[seed % len(POST_LAYOUT_VARIANTS)]

    def _gorsel_alakali_mi(self, photo_path: str, relevance_text: str | None) -> bool:
        """Görsel gerçekten bu habere ait mi — CLIP tarzı embedding kontrolü.

        `article_image` ve `og_image` basamaklarında kullanılıyor:
        `game_cover`'a dokunulmuyor (Steam kapağı zaten OYUN ADINA göre
        kesin eşleşiyor), `stock_photo`'ya dokunulmuyor (Pexels zaten
        kategori-genel bir yedek, habere özel olması beklenmiyor —
        alakasız sayılıp elenirse HER stok görsel reddedilir, gradient'e
        düşülür).

        `relevance_text` yoksa (çağıran taraf geçmediyse) ya da NVIDIA API
        yanıt vermiyorsa (None) FAIL OPEN: görsel kullanılır. Bu opsiyonel
        bir kalite sinyali, çekirdek işlevi bloklayan bir kapı değil.

        DEVRE DIŞI (11 Ağustos 2026). İlk kalibrasyon (10 Ağustos, 5 örnek:
        3 doğru + 2 kasıtlı yanlış) doğru eşleşmeleri 0.133-0.197, yanlışları
        0.063-0.069 vermişti — net bir ayrım gibi görünüyordu. Production'a
        alınınca İLK turda 4 GERÇEK, alakalı görsel yanlışlıkla reddedildi
        (Wuthering Waves/Onimusha/Aion2 — hepsi kendi oyununun ekran
        görüntüsüydü) ve skorları 0.047-0.094 çıktı: yani "doğru" örneklerin
        gerçek dağılımı, ilk kalibrasyondaki "yanlış" aralığıyla ÇAKIŞIYOR.
        Model, geniş açılı/atmosferik sahne görsellerinde (karakter küçük,
        kompozisyon konu-merkezli değil) düşük skor veriyor — bu GÖRSEL
        KALİTESİYLE ilgisiz bir zayıflık, ama eşik onu "alakasız" sanıyor.
        Sabit bir eşikle bu iki durumu ayırmak güvenilir değil.

        3-5 örneklik bir kalibrasyonla production'a çıkmak yetersizdi;
        gerçek zarar (4 iyi görsel kaybı) ölçülmüş bir fayda olmadan
        gerçekleşti. Fonksiyon bilerek SİLİNMEDİ — kalibrasyon
        (ör. çok daha büyük bir örneklemle, ya da farklı bir model/eşik
        stratejisiyle) düzeltilirse `return True` satırı kaldırılıp asıl
        mantık geri açılabilir.
        """
        return True

    def _get_background(self, size: tuple, category: str, seed: int | None,
                         image_url: str | None = None,
                         manual_image_path: str | None = None,
                         game_title: str | None = None,
                         news_url: str | None = None,
                         relevance_text: str | None = None) -> Image.Image:
        """
        Arka plan kaynağını öncelik sırasıyla dener. Sıra ve adlar
        `_BACKGROUND_CHAIN` sabitinde de duruyor; kaydedilen
        `background_source` değerleri o adlardır.

        1. `manual` — kullanıcının Telegram'dan "🎨 Farklı Görsel İste" ile
           gönderdiği Gemini görseli (varsa en yüksek öncelik).
        2. `article_image` — haberin kendi RSS/NewsAPI/Currents kaydındaki
           gerçek görseli (konuya özel olduğu için stoktan önce denenir).
        3. `og_image` — beslemede görsel yoksa/reddedildiyse makalenin KENDİ
           sayfasındaki og:image. Canlı ölçüm: haberlerin %14.8'inde
           `image_url` hiç yok, %10.8'inde gelen görsel reddediliyordu.
        4. `game_cover` — gaming kategorisinde, başlıktan tahmin edilen
           OYUNUN gerçek Steam kapak görseli. Kullanıcı geri bildirimi: her
           oyun haberi aynı jenerik "klavye/kontrolcü" görseliyle çıkıyordu.
        5. `stock_photo` — Pexels'ten kategoriye uygun stok (seed verilmişse).
        6. `gradient` — hiçbiri yoksa mesh-gradient'e zarifçe düş.

        NOT: bu liste 7 Ağustos 2026'ya kadar og:image basamağını hiç
        saymıyordu ve numaralar bir kayıktı — yani docstring, hemen
        üstündeki `_BACKGROUND_CHAIN` sabitiyle çelişiyordu. Bu listeyi
        değiştirirken sabiti de güncelleyin.

        `relevance_text` parametresi hâlâ kabul ediliyor (5 çağıran zincire
        geçiyor) ama `_gorsel_alakali_mi` DEVRE DIŞI — her zaman True
        dönüyor (bkz. o fonksiyonun docstring'i, 11 Ağustos 2026: yanlış
        kalibre edilmiş eşik gerçek görselleri reddediyordu). Parametre
        imzası bilerek korundu ki kalibrasyon düzeltilince tek satırlık bir
        değişiklikle geri açılabilsin.
        """
        if manual_image_path and Path(manual_image_path).exists():
            try:
                img = self._compose_photo_background(size, manual_image_path, category)
                self._last_background_source = "manual"
                return img
            except Exception as e:
                logger.warning(f"Manuel görsel işlenemedi, diğer kaynaklara düşülüyor: {e}")

        if image_url:
            photo_path = fetch_article_photo(image_url)
            if photo_path and self._gorsel_alakali_mi(photo_path, relevance_text):
                try:
                    img = self._compose_photo_background(size, photo_path, category)
                    self._last_background_source = "article_image"
                    return img
                except Exception as e:
                    logger.warning(f"Makale görseli işlenemedi, sayfa/Steam/Pexels'e düşülüyor: {e}")

        # Beslemede image_url yoksa (ya da reddedildiyse) makalenin KENDİ
        # sayfasına gidip og:image aranır. Canlı ölçüm: haberlerin %14.8'inde
        # image_url hiç yok, %10.8'inde ise gelen görsel çok küçük/bozuk olup
        # reddediliyordu — bu içerikler doğrudan jenerik stok fotoğrafa
        # düşüyordu. og:image bu boşluğun büyük kısmını kapatıyor.
        if news_url:
            photo_path = fetch_article_photo_from_page(news_url)
            if photo_path and self._gorsel_alakali_mi(photo_path, relevance_text):
                try:
                    img = self._compose_photo_background(size, photo_path, category)
                    self._last_background_source = "og_image"
                    return img
                except Exception as e:
                    logger.warning(f"Sayfa görseli işlenemedi, Steam/Pexels'e düşülüyor: {e}")

        if category == "gaming" and game_title:
            cover_path = fetch_game_cover_art(game_title)
            if cover_path:
                try:
                    img = self._compose_photo_background(size, cover_path, category)
                    self._last_background_source = "game_cover"
                    return img
                except Exception as e:
                    logger.warning(f"Steam kapak görseli işlenemedi, Pexels/mesh'e düşülüyor: {e}")

        if seed is not None:
            photo_path = fetch_stock_photo(category, seed)
            if photo_path:
                try:
                    img = self._compose_photo_background(size, photo_path, category)
                    self._last_background_source = "stock_photo"
                    return img
                except Exception as e:
                    logger.warning(f"Stok fotoğraf işlenemedi, mesh'e düşülüyor: {e}")

        self._last_background_source = "gradient"
        return self._create_gradient_background(size, category)

    # Kırpma bu oranın üstünde bilgi atıyorsa cover-fit yerine BANT düzeni
    # kullanılır. 0.35 = görselin üçte birinden fazlası kaybolacaksa.
    _MAX_KIRPMA_ORANI = 0.35

    # Bant SADECE bu orandan dar (daha dikey) hedeflerde kullanılır — pratikte
    # reels/story 9:16 (0.5625). Gönderi 4:5 (0.8) hariç TUTULUYOR çünkü
    # yerleşimi bandı BİLMİYOR: metni sabit koordinatlara çiziyor ve bant
    # açılsa metin görselin üzerine binerdi. Bandı gönderiye açmak isteyen,
    # önce _create_post_image'ı last_band_rect'e göre hizalamalı.
    _BANT_HEDEF_ORAN_UST_SINIR = 0.6

    def _compose_photo_background(self, size: tuple, photo_path: str, category: str) -> Image.Image:
        """Fotoğrafı arka plana yerleştirir.

        İki düzen var ve seçim ÖLÇÜLEN KIRPMA MİKTARINA göre yapılır:

        * **cover-fit** — kaynak ile hedef oranı yakınsa. Tam kaplar, en iyi
          görünüm.
        * **bant** — kaynak yatay, hedef dikeyse (reels 9:16 tipik durum).
          Görsel kendi oranında, tam genişlikte bir bant olarak durur; arkasını
          aynı görselin ağır bulanık hâli doldurur.

        Neden bant gerekti (8 Ağustos 2026 ölçümü): reels'te kaynak görselin
        ortalama **%66'sı** kırpılıp atılıyordu; 60 görselin 59'u yarısından
        fazlasını kaybediyordu. Haber görselleri ortalama 16:9 yatay, reels
        ise 9:16 dikey.

        Görünen sonuç: haber görsellerinin çoğu üzerine başlık basılmış
        "thumbnail" tipi olduğu için kırpma o yazıyı KELİME ORTASINDAN
        kesiyordu — yayınlanan bir slaytta arka planda "IVALR" ve
        "OSOFT & EPIC vs ST" yazıyordu, üstünde de bizim Türkçe metnimiz.
        İki yazı yarışıyordu, biri İngilizce ve sakat.

        Bulanıklaştırma denendi ve YETMEDİ: yarıçap 14'te bile dev yazı
        okunuyor, üstelik görsel tanınmaz hâle geliyordu. Yazılı görseli
        otomatik tespit etmek de denendi; 40 gerçek görselde hiçbir eşik
        ayırt edemedi. Çözüm kırpmayı azaltmak oldu.
        """
        width, height = size
        photo = Image.open(photo_path).convert("RGB")
        self._last_band_rect = None

        hedef_oran = width / height
        kaynak_oran = photo.width / photo.height
        if kaynak_oran > hedef_oran:
            gorunur = (photo.height * hedef_oran) / photo.width
        else:
            gorunur = (photo.width / hedef_oran) / photo.height
        if ((1 - gorunur) > self._MAX_KIRPMA_ORANI
                and hedef_oran <= self._BANT_HEDEF_ORAN_UST_SINIR):
            return self._compose_band_background(size, photo, category)

        # Cover-fit: oranı koruyarak kırp
        src_ratio = photo.width / photo.height
        dst_ratio = width / height
        if src_ratio > dst_ratio:
            new_height = height
            new_width = int(height * src_ratio)
        else:
            new_width = width
            new_height = int(width / src_ratio)
        photo = photo.resize((new_width, new_height), Image.LANCZOS)
        left = (new_width - width) // 2
        top = (new_height - height) // 2
        photo = photo.crop((left, top, left + width, top + height))

        # Marka rengiyle ÇOK hafif ton. Önceden 0.22 idi ve gerçek oyun
        # kapak sanatını tanınmaz hale getiriyordu (ör. Cyberpunk 2077'nin
        # sarı/magenta kimliği çamurlu bir limon yeşiline dönüyordu) —
        # kullanıcı geri bildirimi "oyunlarda hep aynı görsel" tam olarak
        # bu yüzdendi. Artık sadece hafif bir renk bütünlüğü katıyor,
        # görselin kendi kimliğini eziyor değil.
        accent = self._hex_to_rgb(COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"])
        tint = Image.new("RGB", size, accent)
        photo = Image.blend(photo, tint, alpha=0.06)

        # Okunabilirlik scrim'i. Önceki sürüm üst %35 ve alt %55'i
        # karartıyordu ama BAŞLIK tam aradaki aydınlık banda düşüyordu —
        # beyaz metin parlak bir fotoğrafın üzerinde kalıp kontrast
        # standardının altına iniyordu. Artık üstte eyebrow/logo için hafif,
        # %22'den itibaren metin alanını tamamen kapsayan güçlü ve sürekli
        # bir karartma rampası var.
        scrim = Image.new("L", size, 0)
        sdraw = ImageDraw.Draw(scrim)
        for y in range(height):
            t = y / height
            top_fade = max(0.0, 1.0 - t / 0.16) * 115
            body_ramp = min(max((t - 0.22) / 0.38, 0.0), 1.0) * 210
            sdraw.line([(0, y), (width, y)], fill=int(max(top_fade, body_ramp)))
        scrim = scrim.filter(ImageFilter.GaussianBlur(40))
        dark_layer = Image.new("RGB", size, self._hex_to_rgb(COLORS["bg_dark"]))
        img = Image.composite(dark_layer, photo, scrim)

        # Çok ince grain — diğer arka planlarla doku tutarlılığı
        noise = Image.effect_noise(size, 18)
        grain = Image.merge("RGBA", (noise, noise, noise, Image.new("L", size, 10)))
        img = Image.alpha_composite(img.convert("RGBA"), grain).convert("RGB")

        return img

    def _compose_band_background(self, size: tuple, photo: Image.Image,
                                  category: str) -> Image.Image:
        """Görseli KIRPMADAN, kendi oranında bir bant olarak yerleştirir.

        Yatay bir haber görselini dikey kareye sığdırmanın tek kayıpsız yolu.
        Arkayı aynı görselin ağır bulanık/karartılmış hâli doldurur — amaç
        renk uyumu, detayın okunması değil (bilerek tanınmaz).

        Bant üstte değil ÜSTE YAKIN duruyor: metin alanı altta kalsın ve
        slayt numarası bandın üstündeki temiz boşluğa düşsün.
        """
        W, H = size
        koyu = self._hex_to_rgb(COLORS["bg_dark"])

        # 1) Arka plan: aynı görsel, kaplayacak şekilde büyütülmüş, ağır
        #    bulanık ve karartılmış.
        arka = photo.copy()
        o = arka.width / arka.height
        if o > W / H:
            arka = arka.resize((max(W, int(H * o)), H), Image.LANCZOS)
        else:
            arka = arka.resize((W, max(H, int(W / o))), Image.LANCZOS)
        sol = (arka.width - W) // 2
        ust = (arka.height - H) // 2
        arka = arka.crop((sol, ust, sol + W, ust + H))
        arka = arka.filter(ImageFilter.GaussianBlur(46))
        arka = Image.blend(arka, Image.new("RGB", size, koyu), 0.58)

        # 2) Bant: görselin TAMAMI, tam genişlikte. Çok yüksek görsellerde
        #    (dikey kaynak) bant ekranı taşmasın diye yüksekliği sınırlanır.
        bant_h = int(W / (photo.width / photo.height))
        max_bant = int(H * 0.52)
        if bant_h > max_bant:
            # Dikey kaynak: burada kırpma zaten az, ortadan hizala.
            olcek = max_bant / bant_h
            yeni_w = int(W * olcek)
            bant = photo.resize((yeni_w, max_bant), Image.LANCZOS)
            bant_x, bant_h = (W - yeni_w) // 2, max_bant
        else:
            bant = photo.resize((W, bant_h), Image.LANCZOS)
            bant_x = 0
        # Bant, ÜST %26'yı slayt numarasına bırakacak şekilde konumlanır;
        # altında da metin için yer kalır. Yerleşim sabit koordinatlara
        # gömülmesin diye bandın sınırları çağırana bildiriliyor.
        bant_y = int(H * 0.26)
        arka.paste(bant, (bant_x, bant_y))
        self._last_band_rect = (bant_y, bant_y + bant_h)

        # 3) Bandın altından itibaren metin alanına yumuşak geçiş.
        gecis = Image.new("L", size, 0)
        gdraw = ImageDraw.Draw(gecis)
        alt = bant_y + bant_h
        for y in range(max(0, alt - 80), H):
            t = min(max((y - (alt - 80)) / 210, 0.0), 1.0)
            gdraw.line([(0, y), (W, y)], fill=int(t * 232))
        gecis = gecis.filter(ImageFilter.GaussianBlur(26))
        img = Image.composite(Image.new("RGB", size, koyu), arka, gecis)

        # 4) Diğer arka planlarla aynı doku
        noise = Image.effect_noise(size, 18)
        grain = Image.merge("RGBA", (noise, noise, noise, Image.new("L", size, 10)))
        return Image.alpha_composite(img.convert("RGBA"), grain).convert("RGB")

    def _draw_glow_text(self, img: Image.Image, xy: tuple, lines: list[str],
                         font: ImageFont.FreeTypeFont, fill: tuple, glow_color: tuple,
                         line_height: int, center_width: int = None,
                         glow_radius: int = 9) -> tuple:
        """
        Sert siyah gölge yerine metni yumuşak, renkli bir 'glow' ile çiz.
        img RGBA↔RGB dönüşümü gerektirdiğinden güncellenmiş (img, draw)
        çiftini döner — çağıran taraf ikisini de yeniden atamalı.
        """
        x0, y0 = xy
        glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer)
        positions = []
        y = y0
        for line in lines:
            if center_width is not None:
                text_w, _ = self._measure_mixed_text(gdraw, line, font)
                lx = x0 + (center_width - text_w) // 2
            else:
                lx = x0
            positions.append((lx, y, line))
            # Emoji içerebilir (Gemini başlıklara emoji ekleyebiliyor) — düz
            # font emoji glifi içermediğinden karışık metin çizimi kullanılır.
            self._draw_mixed_text(gdraw, (lx, y), line, font, fill=glow_color + (255,))
            y += line_height
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(glow_radius))

        img = img.convert("RGBA")
        img = Image.alpha_composite(img, glow_layer).convert("RGB")
        draw = ImageDraw.Draw(img)
        for lx, ly, line in positions:
            self._draw_mixed_text(draw, (lx, ly), line, font, fill=fill)

        return img, draw

    def _draw_letter_spaced(self, draw: ImageDraw.Draw, xy: tuple, text: str,
                             font: ImageFont.FreeTypeFont, fill: tuple, spacing: int = 3) -> int:
        """Karakterleri aralıklı çiz (eyebrow/etiket metinleri için). Bitiş x'ini döner."""
        x, y = xy
        for ch in text:
            draw.text((x, y), ch, font=font, fill=fill)
            bbox = draw.textbbox((0, 0), ch, font=font)
            x += (bbox[2] - bbox[0]) + spacing
        return x

    def _draw_headline_block(self, img: Image.Image, xy: tuple, lines: list[str],
                              font: ImageFont.FreeTypeFont, fill: tuple, glow_color: tuple,
                              line_height: int, category: str, subtitle: str = None,
                              center_width: int = None, subtitle_max_width: int = None) -> tuple:
        """
        Başlığı tek bir blok olarak çizer ve soluna kategori renginde dikey
        bir vurgu çubuğu koyar. Verilirse altına küçük bir subtitle satırı
        da eklenir. Güncellenmiş (img, draw) çifti döner.

        Önceden son satırın arkasına renkli bir "vurgu kutusu" (pill)
        çiziliyordu. İki sorunu vardı: (1) kutu, satır yüksekliğinden büyük
        olan glif kutusu yüzünden ÜSTTEKİ satırın alt uzantılarını kesiyordu
        (ör. "Cyberpunk 2077"deki y/p harfleri), (2) hangi kelimenin
        vurgulanacağı tamamen satır kırılmasına bağlıydı, yani "geliyor",
        "ulaştı" gibi anlamsız kelimeler vurgulanıyordu. Sol vurgu çubuğu
        aynı marka sinyalini verir, hiçbir zaman metni kesmez ve keyfi bir
        kelimeyi öne çıkarmaz.
        """
        x0, y0 = xy
        accent = self._hex_to_rgb(COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"])

        img, draw = self._draw_glow_text(
            img, (x0, y0), lines, font, fill=fill, glow_color=glow_color,
            line_height=line_height, center_width=center_width
        )

        content_bottom = y0 + max(len(lines), 1) * line_height

        # Sol vurgu çubuğu — yalnızca sola hizalı düzenlerde (ortalanmış
        # kart düzeninde dikey bir çubuk kompozisyonu bozar).
        if lines and center_width is None:
            bar_w = 6
            bar_gap = 22
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            odraw.rounded_rectangle(
                [x0 - bar_gap - bar_w, y0 + 6, x0 - bar_gap, content_bottom - 6],
                radius=bar_w // 2, fill=accent + (255,),
            )
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(img)

        if subtitle:
            sub_font = self._get_font("body", FONT_SIZES["caption"])
            sub_color = self._hex_to_rgb(COLORS["text_secondary"])
            max_w = subtitle_max_width
            if max_w is None:
                max_w = center_width if center_width is not None else (img.width - x0 - 60)

            # Karakter sayısı yerine gerçek piksel genişliğine göre kırp —
            # farklı layout'larda (tam genişlik/split panel/kart içi) alan
            # farklı olduğundan sabit karakter sayısı taşmaya neden olabilir.
            fitted = subtitle
            fbbox = draw.textbbox((0, 0), fitted, font=sub_font)
            while (fbbox[2] - fbbox[0]) > max_w and len(fitted) > 1:
                fitted = fitted[:-1]
                fbbox = draw.textbbox((0, 0), fitted, font=sub_font)
            if fitted != subtitle:
                fitted = fitted.rstrip()
                if len(fitted) > 1:
                    fitted = fitted[:-1].rstrip()
                fitted += "…"
                fbbox = draw.textbbox((0, 0), fitted, font=sub_font)

            if center_width is not None:
                sx = x0 + (center_width - (fbbox[2] - fbbox[0])) // 2
            else:
                sx = x0
            draw.text((sx, content_bottom + 14), fitted, font=sub_font, fill=sub_color)

        return img, draw

    @staticmethod
    def _truncate_subtitle(text: str, max_chars: int = 70) -> str | None:
        """Metni kısa bir alt açıklama satırına indirger. Boşsa None döner."""
        if not text:
            return None
        text = text.strip()
        if not text:
            return None
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 1].rstrip() + "…"

    @staticmethod
    def _extract_subtitle_from_caption(caption: str) -> str | None:
        """
        Caption'ın gövde metninden (ilk satır genelde emoji'li bir 'kanca'
        cümlesi olduğundan onu atlayıp) alt açıklama için bir cümle çıkarır.
        Haberin ham (İngilizce) description'ı yerine bunun kullanılması,
        Gemini captionı çevirdiğinde alt açıklamanın da Türkçe/tutarlı
        kalmasını sağlar — description hiçbir zaman çevrilmiyor.
        """
        if not caption:
            return None
        lines = [l.strip() for l in caption.split("\n") if l.strip()]
        if not lines:
            return None
        return lines[1] if len(lines) > 1 else lines[0]

    # =============================================
    # MARKA/LOGO
    # =============================================

    def _draw_brand_mark(self, draw: ImageDraw.Draw, img: Image.Image,
                          x: int, y: int, align: str = "right"):
        """Logo varsa yatay logoyu yapıştır, yoksa marka adını metin olarak yaz."""
        logo = self._get_logo("horizontal")
        if logo is not None:
            target_h = 36
            ratio = target_h / logo.height
            resized = logo.resize((int(logo.width * ratio), target_h))
            paste_x = x - resized.width if align == "right" else x
            img.paste(resized, (paste_x, y - target_h // 2), resized)
            return

        # Logo yoksa metin yedeği. Ad yapılandırılmamışsa hiç çizme —
        # başkasının hesap adını basmaktansa boş bırakmak doğru.
        if not BRAND_HANDLE:
            return
        brand = BRAND_HANDLE
        font_brand = self._get_font("body", FONT_SIZES["small"])
        bbox = draw.textbbox((0, 0), brand, font=font_brand)
        brand_w = bbox[2] - bbox[0]
        paste_x = x - brand_w if align == "right" else x
        draw.text((paste_x, y - 10), brand, font=font_brand,
                  fill=self._hex_to_rgb(COLORS["text_accent"]))

    def _generate_post_carousel(self, content_id: int, content: dict) -> str | None:
        """
        list_items tespit edilmiş bir haber için çok slaytlı (kaydırmalı) feed
        gönderisi üretir: slayt 1 kapak, slayt 2..N her öge kendi adı/detayıyla.
        Üretilen yollar db.update_content_carousel ile kaydedilir (ilk slayt
        media_path'e de yazılır — tekil-görsel varsayan eski kod yolları,
        ör. Telegram onay önizlemesi, değişmeden çalışmaya devam eder).
        """
        list_items = content["list_items"]

        # Kapak metni Gemini'nin liste tespiti yanıtındaki `KAPAK:` satırından
        # geliyor ve orada ham İngilizce başlığın yankılanmasını engelleyen
        # hiçbir şey yok (bkz. _parse_list_content_response). Kapak feed'de
        # görünen slayt olduğu için ayrıca kontrol ediliyor; takılırsa carousel
        # üretilmez ve çağıran taraf tekil görsel akışına düşer — o akış zaten
        # `safe_display_title`'dan geçmiş Türkçe özeti kullanır.
        kapak = (list_items.get("cover") or "").strip()
        if not is_probably_turkish(kapak):
            logger.warning(
                f"Carousel kapağı Türkçe görünmüyor, tekil görsele düşülüyor "
                f"(ID={content_id}): {kapak[:60]!r}"
            )
            return None

        category = content.get("category", "ai")
        source = content.get("source_name", "")
        image_url = content.get("image_url")
        # Tekil görselde olduğu gibi haberin KENDİ sayfası da denenmeli:
        # eskiden carousel slaytlarına news_url hiç geçilmiyordu, bu yüzden
        # og:image basamağı carousel'de atlanıyor ve besleme görselsizse
        # doğrudan stok fotoğrafa düşülüyordu.
        news_url = content.get("news_url") or content.get("url")

        # `display_title` sözleşmesi: bu metinler Gemini'nin ürettiği TÜRKÇE
        # liste ögeleri (kaynak başlık değil), o yüzden doğrudan çizilebilir.
        slides = [{
            "display_title": list_items["cover"],
            "display_subtitle": None,
            "category": category,
            "source_name": source,
            "image_url": image_url,
            "news_url": news_url,
        }]
        for item in list_items.get("items", []):
            slides.append({
                "display_title": item["name"],
                "display_subtitle": item["detail"],
                "category": category,
                "source_name": source,
                "image_url": image_url,
                "news_url": news_url,
                # Her öge kendi Steam kapak görselini alabilsin diye kendi
                # adını taşır (paylaşılan tek image_url yerine).
                "game_title": item["name"] if category == "gaming" else None,
            })

        paths = self.generate_carousel_images(slides)
        if len(paths) < 2:
            logger.warning(f"Carousel yetersiz slaytla üretildi (ID={content_id}), tekil görsele düşülüyor.")
            return None

        self.db.update_content_carousel(content_id, paths)
        kaynak = self.last_background_source()
        self.db.set_background_source(content_id, kaynak)
        logger.info(f"📸 Carousel gönderi üretildi ({len(paths)} slayt, "
                    f"arka plan: {kaynak}): ID={content_id}")
        return paths[0]

    def generate_carousel_images(self, news_list: list[dict],
                                  size: tuple = None) -> list[str]:
        """Carousel gönderi için çoklu görsel oluştur."""
        size = size or POST_SIZE_PORTRAIT
        filepaths = []
        self._slide_background_sources = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        for i, news in enumerate(news_list[:10]):  # Maks 10 slide
            try:
                category = news.get("category", "ai")
                img = self._get_background(size, category, seed=i, image_url=news.get("image_url"),
                                            game_title=news.get("game_title"),
                                            news_url=news.get("news_url"),
                                            relevance_text=news.get("display_title"))
                self._slide_background_sources.append(self._last_background_source)
                draw = ImageDraw.Draw(img)

                img = self._add_decorative_elements(img, category)
                draw = ImageDraw.Draw(img)

                # Slide numarası
                self._draw_slide_number(draw, size, i + 1, len(news_list))

                # Üst bar
                self._draw_top_bar(draw, size, category, img=img)

                # SÖZLEŞME: slayt üreticisi çizilecek metni `display_title`
                # (ve varsa `display_subtitle`) olarak AÇIKÇA verir.
                #
                # Eski hali `news.get("title", news.get("summary_text", ""))`
                # idi — İngilizce kaynak başlığı Türkçe özete tercih ediyordu.
                # Ama sorun yalnızca sıra değildi: bu fonksiyon İKİ ayrı
                # üreticiden slayt alıyor (derleme ve liste gönderisi) ve
                # ikisi metni farklı alanlarda taşıyordu. Belirsiz sözleşme,
                # her düzeltmenin bir yolu kırmasına yol açıyordu.
                #
                # Artık üretici hangi metnin güvenli olduğunu kendisi
                # söylüyor; burada tahmin yok, kaynak alanlara düşüş yok.
                title = (news.get("display_title") or "").strip()
                if not title:
                    logger.warning("Carousel slaytı atlandı: display_title yok.")
                    continue
                subtitle = self._truncate_subtitle(news.get("display_subtitle"))
                img, draw = self._draw_title(img, draw, size, title, category, subtitle=subtitle)

                # Kaynak
                self._draw_source(draw, size, news.get("source_name", ""))

                # Alt bar
                self._draw_bottom_bar(draw, size, img=img)

                filename = f"carousel_{timestamp}_{i+1:02d}.png"
                filepath = str(POSTS_OUTPUT_DIR / filename)
                img.save(filepath, "PNG", quality=95)
                filepaths.append(filepath)

            except Exception as e:
                logger.error(f"Carousel slide {i+1} hatası: {e}")
                continue

        logger.info(f"📸 Carousel oluşturuldu: {len(filepaths)} slide")
        return filepaths

    # =============================================
    # ARKA PLAN OLUŞTURMA
    # =============================================

    def _create_gradient_background(self, size: tuple, category: str) -> Image.Image:
        """
        'Mesh gradient' hissi veren premium arka plan: koyu taban üzerinde
        birkaç büyük bulanık ışık kaynağı + ince grain doku + yumuşak vignette.
        (2026 sosyal medya tasarım trendleri: düz gradyan yerine ambiyans
        ışığı, steril görünümü kıran grain — bkz. proje notları.)
        """
        width, height = size
        bg_dark = self._hex_to_rgb(COLORS["bg_dark"])

        if category == "ai":
            colors = COLORS["gradient_ai"]
        elif category == "gaming":
            colors = COLORS["gradient_gaming"]
        else:
            colors = COLORS["gradient_mixed"]
        c1 = self._hex_to_rgb(colors[0])
        c2 = self._hex_to_rgb(colors[1])

        img = Image.new("RGB", size, bg_dark)

        # Mesh-gradient hissi: birkaç büyük, çok bulanık "ışık kaynağı"
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        blobs = [
            (int(width * 0.10), int(height * 0.10), int(width * 0.60), c1, 165),
            (int(width * 0.95), int(height * 0.30), int(width * 0.55), c2, 150),
            (int(width * 0.25), int(height * 0.95), int(width * 0.65), c1, 130),
        ]
        for cx, cy, r, color, alpha in blobs:
            odraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha,))
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=int(width * 0.14)))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        # Grain / doku — steril "AI görseli" hissini kırar
        noise = Image.effect_noise(size, 22)
        grain = Image.merge("RGBA", (noise, noise, noise, Image.new("L", size, 16)))
        img = Image.alpha_composite(img.convert("RGBA"), grain).convert("RGB")

        # Üst/alt yumuşak vignette — metin okunabilirliği
        vign = Image.new("L", size, 0)
        vdraw = ImageDraw.Draw(vign)
        vdraw.rectangle([0, 0, width, int(height * 0.30)], fill=100)
        vdraw.rectangle([0, int(height * 0.68), width, height], fill=130)
        vign = vign.filter(ImageFilter.GaussianBlur(90))
        dark_layer = Image.new("RGB", size, (0, 0, 0))
        img = Image.composite(dark_layer, img, vign)

        return img

    def _add_decorative_elements(self, img: Image.Image, category: str) -> Image.Image:
        """
        Kenarlara doğru çok hafif bir koyulaşma (vignette) ekleyerek merkeze
        derinlik/odak verir.

        Önceden burada üç büyük yarı saydam renkli DAİRE çiziliyordu; bunlar
        gerçek kapak görsellerinin üzerinde görünür lekeler oluşturup
        kompozisyonu kirletiyordu ve "şablondan çıkma" hissi veriyordu
        (bkz. tasarım denetimi). Vignette aynı derinlik hissini, ayırt
        edilebilir bir şekil bırakmadan verir.
        """
        width, height = img.size

        mask = Image.new("L", (width, height), 0)
        mdraw = ImageDraw.Draw(mask)
        # Kenarlardan içeri doğru daralan çerçeveler: dışarısı koyu, içerisi açık
        steps = 60
        for i in range(steps):
            inset = int(min(width, height) * 0.5 * (i / steps))
            value = int(70 * (1 - i / steps) ** 2)
            mdraw.rectangle(
                [inset, inset, width - inset, height - inset],
                outline=value, width=max(1, int(min(width, height) * 0.5 / steps) + 1),
            )
        mask = mask.filter(ImageFilter.GaussianBlur(int(min(width, height) * 0.06)))

        dark = Image.new("RGB", (width, height), (0, 0, 0))
        return Image.composite(dark, img, mask)

    # =============================================
    # METİN ÇİZİM
    # =============================================

    def _draw_top_bar(self, draw: ImageDraw.Draw, size: tuple, category: str,
                       img: Image.Image = None):
        """Üst bar: kategori etiketi + (varsa) sağ üstte logo rozeti."""
        width, height = size
        padding = 60
        y_start = 80

        # Kategori etiketi — ince accent çizgisi + letter-spaced küçük başlık
        # (dolu-renkli rozet yerine editoryal/premium bir "eyebrow" görünümü)
        # Etiket metni config.CATEGORIES'ten; bilinmeyen kategori eskiden
        # olduğu gibi "gaming" görünümüne düşer.
        label = CATEGORIES.get(category, CATEGORIES["gaming"])["label"]
        if category == "ai":
            accent = self._hex_to_rgb(COLORS["ai_color"])
        else:
            accent = self._hex_to_rgb(COLORS["gaming_color"])

        font = self._get_font("subtitle", 24)
        line_y = y_start + 14

        draw.line([(padding, line_y), (padding + 36, line_y)], fill=accent, width=4)
        self._draw_letter_spaced(draw, (padding + 50, y_start), label, font, accent, spacing=3)

        # Sağ üstte logo rozeti (varsa) — yoksa hiçbir şey çizilmez (mevcut davranış)
        if img is not None:
            logo = self._get_logo("mark")
            if logo is not None:
                target_h = 56
                ratio = target_h / logo.height
                resized = logo.resize((int(logo.width * ratio), target_h))
                img.paste(resized, (width - padding - resized.width, y_start), resized)

    def _draw_title(self, img: Image.Image, draw: ImageDraw.Draw, size: tuple,
                     title: str, category: str, subtitle: str = None) -> tuple:
        """Ana başlığı glow efektiyle çiz (otomatik satır kırma). Güncellenmiş (img, draw) döner."""
        width, height = size
        padding = 60

        # Başlık alanı
        text_area_width = width - (padding * 2)
        y_start = int(height * 0.30)
        max_y = int(height * 0.72)
        if subtitle:
            max_y -= 55

        # Font boyutunu otomatik ayarla
        font_size = FONT_SIZES["title"]
        font = self._get_font("accent", font_size)

        # Metni satırlara böl
        lines = self._wrap_text(title, font, text_area_width, draw)

        # Eğer çok fazla satır varsa font boyutunu küçült
        line_height = font_size + 12
        while len(lines) * line_height > (max_y - y_start) and font_size > 28:
            font_size -= 4
            font = self._get_font("accent", font_size)
            lines = self._wrap_text(title, font, text_area_width, draw)
            line_height = font_size + 12

        # Metni dikey ortala
        total_text_height = len(lines) * line_height
        y_offset = y_start + (max_y - y_start - total_text_height) // 2

        text_color = self._hex_to_rgb(COLORS["text_primary"])
        glow_color = self._hex_to_rgb(COLORS["ai_color"] if category == "ai" else COLORS["gaming_color"])

        return self._draw_headline_block(
            img, (padding, y_offset), lines, font,
            fill=text_color, glow_color=glow_color, line_height=line_height,
            category=category, subtitle=subtitle, subtitle_max_width=text_area_width
        )

    def _draw_source(self, draw: ImageDraw.Draw, size: tuple, source: str):
        """Kaynak bilgisini çiz."""
        if not source:
            return

        width, height = size
        padding = 60
        y_pos = int(height * 0.78)

        font = self._get_font("body", FONT_SIZES["caption"])
        text = f"📰 {source}"

        self._draw_mixed_text(
            draw, (padding, y_pos), text, font,
            fill=self._hex_to_rgb(COLORS["text_secondary"])
        )

    def _draw_bottom_bar(self, draw: ImageDraw.Draw, size: tuple, img: Image.Image = None):
        """Alt bar: marka (logo varsa logo, yoksa metin)."""
        width, height = size
        padding = 60
        y_pos = height - 80

        font = self._get_font("body", FONT_SIZES["small"])

        # Marka/Hesap adı — logo varsa logo, yoksa metin (mevcut davranış)
        if img is not None:
            self._draw_brand_mark(draw, img, width - padding, y_pos + 12, align="right")
        elif BRAND_HANDLE:
            brand = BRAND_HANDLE
            bbox = draw.textbbox((0, 0), brand, font=font)
            brand_w = bbox[2] - bbox[0]
            draw.text(
                (width - padding - brand_w, y_pos), brand, font=font,
                fill=self._hex_to_rgb(COLORS["text_accent"])
            )

    def _draw_slide_number(self, draw: ImageDraw.Draw, size: tuple,
                            current: int, total: int):
        """Carousel slide numarası."""
        width = size[0]
        font = self._get_font("subtitle", 22)
        text = f"{current}/{total}"
        
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        
        draw.text(
            (width - 60 - text_w, 85), text, font=font,
            fill=self._hex_to_rgb(COLORS["text_secondary"])
        )

    # =============================================
    # YARDIMCI METOTLAR
    # =============================================

    def _get_logo(self, variant: str = "horizontal") -> Image.Image | None:
        """
        Logo görselini yükle (cache'li). Dosya yoksa None döner ve çağıran
        taraf mevcut metin-tabanlı marka gösterimine düşer (fontlardaki
        fallback zinciriyle aynı felsefe).
        """
        if variant in self.logo_cache:
            return self.logo_cache[variant]

        path = LOGO_HORIZONTAL_PATH if variant == "horizontal" else LOGO_MARK_PATH
        logo = None
        if path.exists():
            try:
                logo = Image.open(path).convert("RGBA")
            except Exception as e:
                logger.warning(f"Logo yüklenemedi ({variant}): {e}")
                logo = None

        self.logo_cache[variant] = logo
        return logo

    # NotoColorEmoji BİTMAP bir font: yalnızca kendi gömülü boyutunda
    # (109 px) açılır, başka her boyutta PIL "invalid pixel size" hatası
    # verir. Segoe UI Emoji (Windows) ise ölçeklenebilir.
    #
    # Ölçüm (8 Ağustos 2026, üretim sunucusu): 28/56/100 px isteklerinin
    # ÜÇÜ DE hata veriyordu, hata `except Exception: continue` ile
    # yutuluyordu ve emoji hiç çizilmiyordu. Sonuç: reels giriş slaytındaki
    # büyük 🎮/🤖 hiç görünmüyordu (slayt boş duruyordu) ve hikâye
    # prompt'unun istediği emoji'ler de sessizce kayboluyordu.
    _EMOJI_BITMAP_SIZE = 109

    def _get_emoji_font(self, size: int) -> ImageFont.FreeTypeFont | None:
        """
        Renkli emoji fontu yükle (cache'li). Bulunamazsa None döner ve
        çağıran taraf emoji'yi çizmeden atlar (bozuk '.notdef' kutusu yerine).

        İstenen boyutta açılamazsa BİTMAP boyutunda açılır; ölçekleme
        `_emoji_bitmap` içinde yapılır.
        """
        cache_key = f"emoji_{size}"
        if cache_key in self.fonts_cache:
            return self.fonts_cache[cache_key]

        candidates = [
            "C:/Windows/Fonts/seguiemj.ttf",  # Windows — Segoe UI Emoji (renkli, ölçeklenebilir)
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",  # Ubuntu/Debian (fonts-noto-color-emoji)
            "/usr/share/fonts/noto/NotoColorEmoji.ttf",
        ]
        font = None
        for path in candidates:
            if not os.path.exists(path):
                continue
            for deneme in (size, self._EMOJI_BITMAP_SIZE):
                try:
                    font = ImageFont.truetype(path, deneme)
                    break
                except OSError:
                    continue
            if font is not None:
                break

        self.fonts_cache[cache_key] = font
        return font

    def _emoji_bitmap(self, chunk: str, hedef_px: int) -> Image.Image | None:
        """Emoji parçasını `hedef_px` yüksekliğinde RGBA görsel olarak üret.

        Bitmap fontta doğrudan istenen boyutta çizim mümkün olmadığı için
        emoji kendi boyutunda çizilip ölçekleniyor. Ölçeklenebilir bir font
        (Segoe UI Emoji) varsa zaten hedef boyutta açılır ve ölçekleme
        yalnızca 1.0 katsayısıyla geçilir.
        """
        font = self._get_emoji_font(hedef_px)
        if font is None:
            return None
        try:
            gecici = Image.new("RGBA", (font.size * 3, font.size * 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(gecici)
            d.text((0, 0), chunk, font=font, embedded_color=True)
            kutu = gecici.getbbox()
            if not kutu:
                return None
            gecici = gecici.crop(kutu)
            if gecici.height != hedef_px:
                oran = hedef_px / gecici.height
                gecici = gecici.resize(
                    (max(1, int(gecici.width * oran)), hedef_px), Image.LANCZOS
                )
            return gecici
        except Exception as e:
            logger.debug(f"Emoji çizilemedi ({chunk!r}): {e}")
            return None

    def _draw_mixed_text(self, draw: ImageDraw.Draw, xy: tuple, text: str,
                          font: ImageFont.FreeTypeFont, fill, shadow: bool = False) -> int:
        """
        Emoji içeren metni doğru çiz: emoji'ler renkli emoji fontuyla, geri
        kalanı verilen fontla. Emoji fontu yoksa o parça atlanır. Toplam
        çizilen genişliği döner.
        """
        x, y = xy
        total_width = 0

        for chunk, is_emoji in _split_emoji_runs(text):
            if not chunk:
                continue
            if is_emoji:
                # Emoji AYRI bir RGBA görsele çizilip yapıştırılıyor:
                # bitmap font istenen boyutta açılamadığı için ölçekleme
                # şart (bkz. _emoji_bitmap). Yapıştırmak için hedef görsel
                # gerekiyor; ImageDraw onu `_image` alanında tutuyor.
                # Erişilemezse eski davranışa (emoji'yi atla) düşülür.
                hedef = getattr(draw, "_image", None)
                gorsel = self._emoji_bitmap(chunk, font.size) if hedef else None
                if gorsel is None:
                    continue
                hedef.paste(gorsel, (int(x + total_width), int(y)), gorsel)
                total_width += gorsel.width
            else:
                bbox = draw.textbbox((0, 0), chunk, font=font)
                if shadow:
                    draw.text((x + total_width + 2, y + 2), chunk, font=font, fill=(0, 0, 0))
                draw.text((x + total_width, y), chunk, font=font, fill=fill)
                total_width += bbox[2] - bbox[0]

        return total_width

    def _measure_mixed_text(self, draw: ImageDraw.Draw, text: str,
                             font: ImageFont.FreeTypeFont) -> tuple:
        """_draw_mixed_text çizmeden önce (genişlik, yükseklik) ölç (rozet/arka plan boyutlandırma için)."""
        total_width = 0
        max_height = 0
        for chunk, is_emoji in _split_emoji_runs(text):
            if not chunk:
                continue
            if is_emoji:
                # Ölçü, ÇİZİLECEK olanla aynı olmalı: emoji ölçeklenmiş
                # bitmap olarak çiziliyor, o yüzden ölçü de ondan alınır.
                # Font boyutundan hesaplanırsa (bitmap fontta 109 px)
                # rozet/arka plan kutuları gerçeğin üç katı çıkar.
                gorsel = self._emoji_bitmap(chunk, font.size)
                if gorsel is None:
                    continue
                total_width += gorsel.width
                max_height = max(max_height, gorsel.height)
                continue
            bbox = draw.textbbox((0, 0), chunk, font=font)
            total_width += bbox[2] - bbox[0]
            max_height = max(max_height, bbox[3] - bbox[1])
        return total_width, max_height

    def _get_font(self, style: str, size: int) -> ImageFont.FreeTypeFont:
        """Font yükle (cache ile)."""
        cache_key = f"{style}_{size}"
        if cache_key in self.fonts_cache:
            return self.fonts_cache[cache_key]

        font_name = FONTS.get(style, FONTS["body"])
        
        # Proje fontlarını dene
        font_paths = [
            FONTS_DIR / f"{font_name}.ttf",
            FONTS_DIR / f"{font_name}.otf",
        ]

        for font_path in font_paths:
            if font_path.exists():
                try:
                    font = ImageFont.truetype(str(font_path), size)
                    self.fonts_cache[cache_key] = font
                    return font
                except Exception:
                    continue

        # Sistem fontlarını dene
        system_fonts = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/verdana.ttf",
        ]
        for sys_font in system_fonts:
            if os.path.exists(sys_font):
                try:
                    font = ImageFont.truetype(sys_font, size)
                    self.fonts_cache[cache_key] = font
                    return font
                except Exception:
                    continue

        # Fallback: varsayılan font
        font = ImageFont.load_default()
        self.fonts_cache[cache_key] = font
        return font

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.FreeTypeFont,
                   max_width: int, draw: ImageDraw.Draw) -> list[str]:
        """Metni satırlara böl."""
        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test_line, font=font)
            text_width = bbox[2] - bbox[0]

            if text_width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        return lines

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple:
        """Hex renk kodunu RGB tuple'a çevir."""
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    
    gen = ImageGenerator()
    
    # Test görseli oluştur
    test_data = {
        "category": "ai",
        "summary_text": "ÖRNEK BAŞLIK: Yapay Zeka Şablonunu Denemek İçin Üretilmiş Test Metnidir",
        "source_name": "Örnek Kaynak",
        "news_title": "Örnek Yapay Zeka Başlığı (test)"
    }
    
    path = gen.generate_post_image(news_data=test_data)
    if path:
        print(f"\n✅ Test görseli: {path}")
