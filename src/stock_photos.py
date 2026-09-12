"""
Stok Fotoğraf Entegrasyonu (Pexels)
Her haber öğesi için kategoriye uygun, lisanslı bir stok fotoğraf çeker.
PEXELS_API_KEY yapılandırılmamışsa veya herhangi bir hata olursa None döner —
çağıran taraf (image_generator.py) bu durumda mesh-gradient arka plana düşer.
"""

import logging
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PEXELS_API_KEY, STOCK_PHOTO_CACHE_DIR

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Türkçe/karışık dilli haber başlıklarından anahtar kelime çıkarmak yerine
# kategori başına küratörlü İngilizce arama terimleri kullanılır — daha
# güvenilir ve tutarlı sonuç verir.
PEXELS_QUERY_TERMS = {
    "ai": [
        "artificial intelligence technology",
        "futuristic robot",
        "data server room",
        "neural network abstract",
        "computer chip macro",
        "machine learning code",
    ],
    "gaming": [
        "video game controller",
        "esports gaming setup",
        "gaming keyboard neon",
        "video game console",
        "arcade neon",
        "gamer playing console",
    ],
}


def fetch_stock_photo(category: str, seed: int) -> str | None:
    """
    Kategoriye uygun bir stok fotoğraf indirir (yerel önbellekten varsa oradan),
    yerel dosya yolunu döndürür. Herhangi bir sorunda None döner.
    """
    if not PEXELS_API_KEY:
        return None

    terms = PEXELS_QUERY_TERMS.get(category, PEXELS_QUERY_TERMS["ai"])
    query = terms[seed % len(terms)]

    try:
        response = requests.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": 15, "orientation": "portrait"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        photos = data.get("photos", [])
        if not photos:
            logger.debug(f"Pexels sonuç bulamadı: '{query}'")
            return None

        photo = photos[seed % len(photos)]
        photo_id = photo["id"]
        photo_url = photo["src"]["large2x"]

        STOCK_PHOTO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = STOCK_PHOTO_CACHE_DIR / f"{photo_id}.jpg"

        if cache_path.exists():
            return str(cache_path)

        img_response = requests.get(photo_url, timeout=30)
        img_response.raise_for_status()
        cache_path.write_bytes(img_response.content)

        logger.info(f"📷 Stok fotoğraf indirildi: '{query}' → {cache_path.name}")
        return str(cache_path)

    except requests.RequestException as e:
        logger.warning(f"Pexels API hatası: {e}")
        return None
    except (KeyError, IndexError) as e:
        logger.warning(f"Pexels yanıtı beklenmedik formatta: {e}")
        return None
