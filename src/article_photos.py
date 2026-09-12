"""
Haber Kaynağı Görseli Entegrasyonu
Her haberin kendi RSS/NewsAPI/Currents kaydındaki image_url'sini indirip
konuya özel bir arka plan olarak kullanır. Herhangi bir sorunda (URL yok,
indirme hatası, bozuk/küçük görsel) None döner — çağıran taraf (image_generator.py)
bu durumda Pexels'e, o da olmazsa mesh-gradient'e düşer.
"""

import hashlib
import io
import logging
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from PIL import Image, UnidentifiedImageError

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    ARTICLE_PHOTO_CACHE_DIR,
    ARTICLE_PHOTO_MIN_LONG_SIDE,
    ARTICLE_PHOTO_MIN_SHORT_SIDE,
)

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


# og:image ve muadilleri için kademeli seçici zinciri. Sıra önemlidir:
# og:image sosyal paylaşım için özellikle seçilmiş, en büyük ve en temsili
# görseldir; twitter:image ikinci tercih; image_src eski ama hâlâ yaygın.
# NOT: og:image için `property=` VE `name=` ikisi de kabul edilir.
# Open Graph spesifikasyonu `property` diyor, ama pek çok CMS `name`
# üretiyor — canlı ölçümde rpgsite.net tam olarak bunu yapıyordu
# (`<meta name="og:image" content="...">`) ve yalnızca `property` arandığı
# için görseli hiç bulunamıyor, gönderi stok fotoğrafa/gradyana düşüyordu.
_META_IMAGE_PATTERNS = [
    r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::secure_url)?["\']',
    r'<meta[^>]+(?:name|property)=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']twitter:image(?::src)?["\']',
    r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
]


def extract_og_image(html: str, base_url: str | None = None) -> str | None:
    """
    Makale HTML'inden sosyal paylaşım görselini çıkarır.

    Tam bir HTML ayrıştırıcı yerine hedefli regex kullanılıyor: aranan şey
    <head> içindeki tek bir meta etiketinin content'i — dar, iyi tanımlı bir
    iş. Projeye yeni bir bağımlılık (bs4/lxml) eklemek ve onu VDS'e kurmak
    bu kazanç için gereksiz. Etiket öznitelik sırası siteden siteye
    değiştiğinden her biçim için iki kalıp (content önce/sonra) denenir.

    `base_url` verilirse GÖRELİ adresler (ör. `/uploads/kapak.jpg`) sayfanın
    adresine göre çözülür. Canlı ölçümde konami.com tam olarak bunu yapıyordu:
    og:image etiketi vardı, bulunuyordu, ama "http" ile başlamadığı için
    atılıyor ve gönderi jenerik stok fotoğrafa düşüyordu.
    """
    head = html[:200_000]  # og:image daima <head>'de; tüm sayfayı taramaya gerek yok
    for pattern in _META_IMAGE_PATTERNS:
        match = re.search(pattern, head, re.IGNORECASE)
        if match:
            url = match.group(1).strip()
            if url.startswith("//"):
                url = "https:" + url
            elif not url.startswith("http") and base_url:
                url = urljoin(base_url, url)
            if url.startswith("http"):
                return url
    return None


def fetch_article_photo_from_page(article_url: str | None) -> str | None:
    """
    RSS/API kaydında image_url YOKKEN makale sayfasına gidip og:image'ı bulur
    ve indirir. Bulunan görsel normal fetch_article_photo akışından geçer
    (boyut/tip doğrulaması + önbellek).

    Nazik davranış: tek istek, kısa timeout, yalnızca HTML yanıtları okunur
    ve gövde 1 MB'da kesilir (bazı haber sayfaları 5+ MB). Herhangi bir
    sorunda sessizce None döner — çağıran taraf stok fotoğrafa düşer.
    """
    if not article_url or not article_url.startswith("http"):
        return None

    try:
        response = requests.get(
            article_url, headers=_HEADERS, timeout=12, stream=True,
            allow_redirects=True,
        )
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            return None
        chunks, size = [], 0
        for chunk in response.iter_content(8192, decode_unicode=True):
            if not chunk:
                continue
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8", "ignore"))
            size += len(chunk)
            if size >= 1_000_000:
                break
        html = "".join(chunks)
    except (requests.RequestException, UnicodeDecodeError) as e:
        logger.debug(f"Makale sayfası okunamadı ({article_url}): {e}")
        return None

    og_url = extract_og_image(html, base_url=response.url or article_url)
    if not og_url:
        logger.debug(f"Makale sayfasında og:image bulunamadı: {article_url}")
        return None

    logger.info(f"🔎 Makale sayfasından görsel bulundu: {og_url[:90]}")
    return fetch_article_photo(og_url)


# Görsel CDN'lerinin küçültme parametreleri. Besleme çoğu zaman KÜÇÜK
# varyantı veriyor (ör. gamespot `...jpg?w=300`), oysa aynı adres bu
# parametreler olmadan tam boy görseli döndürüyor. Canlı ölçümde stok
# fotoğrafa düşen GameSpot haberlerinin sebebi tam olarak buydu: görsel
# vardı, indiriliyordu, 300x168 olduğu için "logo herhalde" diye eleniyordu.
_DOWNSCALE_PARAMS = {"w", "width", "h", "height", "size", "resize", "fit", "s"}


def _full_size_variant(url: str) -> str | None:
    """Küçültme parametreleri temizlenmiş sürüm (temizlenecek bir şey yoksa None)."""
    parts = urlsplit(url)
    if not parts.query:
        return None
    params = parse_qsl(parts.query, keep_blank_values=True)
    kalan = [(k, v) for k, v in params if k.lower() not in _DOWNSCALE_PARAMS]
    if len(kalan) == len(params):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(kalan), parts.fragment))


def _download_photo(url: str) -> Image.Image | None:
    """İndirip doğrular; kullanılabilir bir görselse Image, değilse None."""
    try:
        response = requests.get(url, headers=_HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.debug(f"Makale görseli indirme hatası ({url}): {e}")
        return None

    content_type = response.headers.get("content-type", "")
    if content_type and not content_type.startswith("image/"):
        logger.debug(f"Makale görseli değil (content-type={content_type}): {url}")
        return None

    try:
        img = Image.open(io.BytesIO(response.content))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        logger.debug(f"Makale görseli decode edilemedi: {e}")
        return None

    uzun, kisa = max(img.width, img.height), min(img.width, img.height)
    if uzun < ARTICLE_PHOTO_MIN_LONG_SIDE or kisa < ARTICLE_PHOTO_MIN_SHORT_SIDE:
        logger.debug(
            f"Makale görseli çok küçük ({img.width}x{img.height}): {url}"
        )
        return None
    return img


def fetch_article_photo(image_url: str | None) -> str | None:
    """
    Haberin kendi görselini indirir (yerel önbellekten varsa oradan),
    yerel dosya yolunu döndürür. URL yoksa, indirilemezse, görsel değilse,
    bozuksa veya çok küçükse (muhtemelen logo/ikon) None döner.

    Adreste küçültme parametresi varsa ÖNCE temizlenmiş tam boy sürüm
    denenir; o başarısız olursa beslemedeki özgün adrese dönülür (bazı
    CDN'ler parametresiz istekleri reddediyor).
    """
    if not image_url:
        return None

    # Önbellek anahtarı DAİMA özgün adresten türetilir: hangi varyantın
    # indirildiğinden bağımsız olarak aynı haber aynı dosyayı bulsun.
    cache_key = hashlib.md5(image_url.encode("utf-8")).hexdigest()
    ARTICLE_PHOTO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = ARTICLE_PHOTO_CACHE_DIR / f"{cache_key}.jpg"

    if cache_path.exists():
        return str(cache_path)

    adaylar = [u for u in (_full_size_variant(image_url), image_url) if u]
    for aday in adaylar:
        img = _download_photo(aday)
        if img is None:
            continue
        img.convert("RGB").save(cache_path, "JPEG", quality=90)
        logger.info(f"📰 Makale görseli indirildi ({img.width}x{img.height}): "
                    f"{aday} → {cache_path.name}")
        return str(cache_path)

    return None
