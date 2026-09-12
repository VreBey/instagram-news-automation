"""
Manuel Görsel Uygulama (Telegram ve Web Paneli ortak mantığı)
Kullanıcının Gemini'den üretip geri gönderdiği/yüklediği görseli bir içeriğe
bağlar ve ilgili medyayı bu görselle yeniden render eder. Hem
src/telegram_bot.py (foto yanıtı) hem de src/dashboard.py (dosya yükleme)
tarafından çağrılır — tek bir yerde tutularak iki kanal arasında mantık
tekrarı önlenir.
"""

import logging
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import MANUAL_IMAGE_CACHE_DIR
from src.database import Database
from src.image_generator import ImageGenerator
from src.story_generator import StoryGenerator

logger = logging.getLogger(__name__)


def apply_manual_image(content_id: int, image_bytes: bytes, db: Database,
                        img_gen: ImageGenerator, story_gen: StoryGenerator) -> str | None:
    """
    Görseli içeriğe kalıcı olarak bağlar (MANUAL_IMAGE_CACHE_DIR'e yazıp
    manual_image_path'i günceller) ve içerik türüne göre (post/story) medyayı
    bu görselle yeniden oluşturur. Başarılıysa yeni medya yolunu döner,
    içerik bulunamazsa veya yeniden oluşturma başarısız olursa None döner.
    """
    dest_path = MANUAL_IMAGE_CACHE_DIR / f"{content_id}.jpg"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(image_bytes)
    db.set_manual_image_path(content_id, str(dest_path))

    content = db.get_content_by_id(content_id)
    if not content:
        logger.error(f"Manuel görsel uygulanamadı — içerik bulunamadı: ID={content_id}")
        return None

    if content["content_type"] == "story":
        return story_gen.generate_story_image(content_id=content_id)
    return img_gen.generate_post_image(content_id=content_id)
