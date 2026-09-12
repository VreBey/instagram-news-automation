"""NVIDIA NIM embedding istemcisi — metin ve görsel embedding.

SADECE embedding için: metin üretiminde Gemini'nin yerine denendi ve geri
çekildi (bkz. config.py'deki NVIDIA_API_KEY açıklaması). Burada iki iş var:
  1. `embed_text`  — haber tekilleştirme (bkz. Database.find_similar_recent)
  2. `embed_image` — görsel-metin alaka kontrolü (bkz. ImageGenerator._get_background)

TASARIM İLKESİ: bu modülün HİÇBİR çağrısı pipeline'ı durdurmamalı. NVIDIA
API'si çökerse, yavaşsa ya da anahtar geçersizse, her fonksiyon sessizce
None döner — çağıran taraf bunu "sinyal yok, eskisi gibi davran" olarak
okur (fail open). Bu iki özellik OPSİYONEL kalite iyileştirmesi; pipeline'ın
çekirdek işlevi (haber topla, içerik üret, yayınla) bunlara bağımlı değil.
"""

import base64
import logging
import math

import requests

from config import (
    NVIDIA_API_KEY, NVIDIA_EMBED_TEXT_MODEL, NVIDIA_EMBED_VL_MODEL,
    NVIDIA_EMBED_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_EMBEDDINGS_URL = "https://integrate.api.nvidia.com/v1/embeddings"


def _post(model: str, girdi: str, input_type: str) -> list[float] | None:
    if not NVIDIA_API_KEY:
        return None
    try:
        r = requests.post(
            _EMBEDDINGS_URL,
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
            json={"model": model, "input": [girdi], "input_type": input_type},
            timeout=NVIDIA_EMBED_TIMEOUT_SECONDS,
        )
        if r.status_code != 200:
            logger.warning(f"NVIDIA embedding {r.status_code}: {r.text[:200]}")
            return None
        return r.json()["data"][0]["embedding"]
    except requests.RequestException as e:
        logger.warning(f"NVIDIA embedding isteği başarısız: {type(e).__name__}: {e}")
        return None
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(f"NVIDIA embedding yanıtı beklenmedik biçimde: {e}")
        return None


def embed_text(text: str, input_type: str = "query",
               model: str = NVIDIA_EMBED_TEXT_MODEL) -> list[float] | None:
    """Metni embedding vektörüne çevirir. Başarısızsa None (fail open).

    `model` parametresi BİLEREK var: `embed_text` iki AYRI amaç için
    kullanılıyor ve ikisi FARKLI modelde çalışmak zorunda.
      - Haber tekilleştirme (varsayılan `NVIDIA_EMBED_TEXT_MODEL`): metin
        metinle kıyaslanıyor.
      - Görsel-alaka kontrolü (`model=NVIDIA_EMBED_VL_MODEL` GEÇİLMELİ):
        metin görselle kıyaslanacak, o yüzden `embed_image`'in kullandığı
        AYNI VL modeliyle embed edilmesi ŞART.

    Bu ayrım bir hatadan öğrenildi: `_gorsel_alakali_mi` ilk sürümde
    `embed_text`'i varsayılan modelle çağırıyordu — metin ve görsel FARKLI
    vektör uzaylarında çıkıyor, kosinüs benzerliği anlamsız bir sayı
    (0.03 civarı, hem gerçek eşleşmelerde hem alakasızlarda) veriyordu.
    Aynı VL modeline geçilince ölçülen ayrım (0.13-0.20 / 0.06-0.07) geri
    geldi.
    """
    if not text or not text.strip():
        return None
    return _post(model, text, input_type)


def embed_image(image_path: str) -> list[float] | None:
    """Görseli embedding vektörüne çevirir. Başarısızsa None (fail open).

    `NVIDIA_EMBED_VL_MODEL` kullanır — metinle aynı uzayda karşılaştırmak
    için `embed_text(..., model=NVIDIA_EMBED_VL_MODEL)` ile çağrılmalı.
    """
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except OSError as e:
        logger.warning(f"Görsel okunamadı ({image_path}): {e}")
        return None
    data_uri = f"data:image/jpeg;base64,{b64}"
    return _post(NVIDIA_EMBED_VL_MODEL, data_uri, "passage")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """İki embedding vektörü arasındaki kosinüs benzerliği (-1.0 – 1.0)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
