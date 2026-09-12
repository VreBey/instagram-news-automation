"""
Instagram Access Token Yenileme Modülü
Uzun ömürlü Instagram/Facebook token'ları ~60 günde bir sona erer.
Bu modül, süresi dolmadan önce fb_exchange_token akışıyla token'ı yeniler
ve kalıcı olarak veritabanında (app_settings) saklar — .env dosyası sadece
ilk değeri sağlar, .env'i koddan yeniden yazmaya çalışmaz.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET, INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_API, TOKEN_REFRESH_WARNING_DAYS
)
from src.database import Database

logger = logging.getLogger(__name__)

TOKEN_SETTING_KEY = "instagram_access_token"
EXPIRES_SETTING_KEY = "instagram_token_expires_at"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class TokenManager:
    """Uzun ömürlü Instagram access token'ını yönetir ve gerektiğinde yeniler."""

    def __init__(self, db: Database = None):
        self.db = db or Database()

    def get_current_token(self) -> str:
        """Kullanılacak güncel token'ı döndür (önce DB, yoksa .env)."""
        return self.db.get_setting(TOKEN_SETTING_KEY) or INSTAGRAM_ACCESS_TOKEN

    def refresh_long_lived_token(self, current_token: str) -> dict | None:
        """
        fb_exchange_token akışıyla token'ı yenile. Meta, bu akışın hem
        kısa ömürlü→uzun ömürlü dönüşüm hem de mevcut uzun ömürlü bir
        token'ın süresini uzatmak için kullanılmasına izin verir.
        """
        if not INSTAGRAM_APP_ID or not INSTAGRAM_APP_SECRET:
            logger.warning("INSTAGRAM_APP_ID/APP_SECRET yapılandırılmamış, token yenilenemez.")
            return None

        try:
            response = requests.get(
                f"{INSTAGRAM_API['graph_url']}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": INSTAGRAM_APP_ID,
                    "client_secret": INSTAGRAM_APP_SECRET,
                    "fb_exchange_token": current_token,
                },
                timeout=15,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Token yenileme isteği başarısız: {e}")
            return None

    def check_and_refresh_if_needed(self) -> bool:
        """
        Token'ın süresi dolmaya yakınsa (veya süre bilgisi hiç yoksa) yeniler.
        Döndürür: yenileme denendiyse ve başarılıysa True.
        """
        current_token = self.get_current_token()
        if not current_token:
            logger.debug("Instagram access token yapılandırılmamış, yenileme atlanıyor.")
            return False

        expires_at_str = self.db.get_setting(EXPIRES_SETTING_KEY)
        if expires_at_str:
            try:
                expires_at = datetime.strptime(expires_at_str, DATETIME_FORMAT)
                days_remaining = (expires_at - datetime.now()).days
                if days_remaining > TOKEN_REFRESH_WARNING_DAYS:
                    logger.debug(f"Instagram token {days_remaining} gün daha geçerli.")
                    return False
                logger.info(f"Instagram token {days_remaining} gün içinde sona eriyor, yenileniyor...")
            except ValueError:
                pass

        result = self.refresh_long_lived_token(current_token)
        if not result or "access_token" not in result:
            logger.warning("Instagram token yenilenemedi.")
            return False

        new_token = result["access_token"]
        expires_in = result.get("expires_in", 60 * 24 * 3600)  # saniye, varsayılan ~60 gün
        new_expires_at = datetime.now() + timedelta(seconds=expires_in)

        self.db.set_setting(TOKEN_SETTING_KEY, new_token)
        self.db.set_setting(EXPIRES_SETTING_KEY, new_expires_at.strftime(DATETIME_FORMAT))
        logger.info(f"✅ Instagram token yenilendi. Yeni son geçerlilik: {new_expires_at:%Y-%m-%d}")
        return True

    def get_expiry_status(self) -> dict:
        """Dashboard'da göstermek için token süresi bilgisi."""
        expires_at_str = self.db.get_setting(EXPIRES_SETTING_KEY)
        if not expires_at_str:
            return {"known": False, "expires_at": None, "days_remaining": None, "warning": False}

        try:
            expires_at = datetime.strptime(expires_at_str, DATETIME_FORMAT)
            days_remaining = (expires_at - datetime.now()).days
            return {
                "known": True,
                "expires_at": expires_at_str,
                "days_remaining": days_remaining,
                "warning": days_remaining < TOKEN_REFRESH_WARNING_DAYS,
            }
        except ValueError:
            return {"known": False, "expires_at": None, "days_remaining": None, "warning": False}
