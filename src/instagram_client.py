"""
Instagram API İstemcisi
Instagram Graph API ile gönderi, hikaye ve Reels paylaşımı yapar.
"""

import logging
import time
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_USER_ID,
    INSTAGRAM_API, MEDIA_HOST_URL,
    CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET,
    CLOUDINARY_UPLOAD_FOLDER
)

logger = logging.getLogger(__name__)

# Cloudinary import (opsiyonel — kurulu değilse medya hosting devre dışı kalır)
try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False
    logger.warning("cloudinary kütüphanesi bulunamadı. Medya hosting devre dışı.")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}


class InsightsPermissionError(RuntimeError):
    """Uygulama token'ında `instagram_manage_insights` izni yok.

    Ayrı bir istisna olmasının sebebi: bu, tek tek medyaların değil TÜM
    hesabın sorunu. Çağıran taraf bunu yakalayıp turu kesmeli — 58 medya için
    58 kez denemek hem anlamsız hem de gerçek sebebi 58 satır uyarının içinde
    gizliyor (2026-08-04 denetiminde tam olarak bu oldu).
    """


class InstagramClient:
    """Instagram Graph API istemcisi."""

    def __init__(self, access_token: str = None, user_id: str = None, db=None):
        self.access_token = access_token or self._resolve_token(db)
        self.user_id = user_id or INSTAGRAM_USER_ID
        self.graph_url = INSTAGRAM_API["graph_url"]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}"
        })

    @staticmethod
    def _resolve_token(db=None) -> str:
        """
        Kullanılacak token'ı çöz: önce veritabanı (TokenManager tarafından
        yenilenmiş olabilir), yoksa .env'deki INSTAGRAM_ACCESS_TOKEN.
        """
        try:
            from src.database import Database
            from src.token_manager import TOKEN_SETTING_KEY
            db = db or Database()
            return db.get_setting(TOKEN_SETTING_KEY) or INSTAGRAM_ACCESS_TOKEN
        except Exception:
            return INSTAGRAM_ACCESS_TOKEN

    def is_configured(self) -> bool:
        """API yapılandırmasının geçerli olup olmadığını kontrol et."""
        return bool(self.access_token and self.user_id)

    # =============================================
    # FEED GÖNDERİSİ PAYLAŞIMI
    # =============================================

    def publish_post(self, image_url: str, caption: str = "",
                     hashtags: list[str] = None) -> dict:
        """
        Feed gönderisi paylaş (tek görsel).
        
        Args:
            image_url: Görsel URL'si (public erişilebilir olmalı)
            caption: Gönderi açıklaması
            hashtags: Hashtag listesi
        
        Returns:
            {"success": bool, "media_id": str, "permalink": str, "error": str}
        """
        if not self.is_configured():
            return {"success": False, "error": "API yapılandırılmamış"}

        try:
            # Hashtag'leri caption'a ekle
            full_caption = self._build_caption(caption, hashtags)

            # 1. Container oluştur
            container_id = self._create_media_container(
                media_type="IMAGE",
                media_url=image_url,
                caption=full_caption
            )
            if not container_id:
                return {"success": False, "error": "Container oluşturulamadı"}

            # 2. Container durumunu bekle
            if not self._wait_for_container(container_id):
                return {"success": False, "error": "Container hazır olmadı"}

            # 3. Yayınla
            result = self._publish_media(container_id)
            return result

        except Exception as e:
            logger.error(f"Post paylaşım hatası: {e}")
            return {"success": False, "error": str(e)}

    def publish_carousel(self, image_urls: list[str], caption: str = "",
                         hashtags: list[str] = None) -> dict:
        """
        Carousel (çoklu görsel) gönderisi paylaş.
        
        Args:
            image_urls: Görsel URL listesi (2-10 arası)
            caption: Gönderi açıklaması
            hashtags: Hashtag listesi
        """
        if not self.is_configured():
            return {"success": False, "error": "API yapılandırılmamış"}

        if len(image_urls) < 2:
            return {"success": False, "error": "Carousel için en az 2 görsel gerekli"}

        try:
            full_caption = self._build_caption(caption, hashtags)
            
            # 1. Her görsel için alt container oluştur
            children_ids = []
            for url in image_urls[:10]:
                child_id = self._create_media_container(
                    media_type="IMAGE",
                    media_url=url,
                    is_carousel_item=True
                )
                if child_id:
                    self._wait_for_container(child_id)
                    children_ids.append(child_id)

            if len(children_ids) < 2:
                return {"success": False, "error": "Yeterli carousel öğesi oluşturulamadı"}

            # 2. Carousel container oluştur
            carousel_id = self._create_carousel_container(children_ids, full_caption)
            if not carousel_id:
                return {"success": False, "error": "Carousel container oluşturulamadı"}

            # 3. Bekle ve yayınla
            if not self._wait_for_container(carousel_id):
                return {"success": False, "error": "Carousel hazır olmadı"}

            return self._publish_media(carousel_id)

        except Exception as e:
            logger.error(f"Carousel paylaşım hatası: {e}")
            return {"success": False, "error": str(e)}

    # =============================================
    # HİKAYE PAYLAŞIMI
    # =============================================

    def publish_story(self, media_url: str, media_type: str = "IMAGE") -> dict:
        """
        Instagram Story paylaş.
        
        Args:
            media_url: Medya URL'si (görsel veya video)
            media_type: "IMAGE" veya "VIDEO"
        """
        if not self.is_configured():
            return {"success": False, "error": "API yapılandırılmamış"}

        try:
            # Story container oluştur
            endpoint = f"{self.graph_url}/{self.user_id}/media"
            
            params = {
                "access_token": self.access_token,
                "media_type": "STORIES",
            }
            
            if media_type == "VIDEO":
                params["video_url"] = media_url
            else:
                params["image_url"] = media_url

            # Feed gönderisiyle AYNI yeniden deneme yolundan geçer. Eskiden
            # burada ham `session.post` vardı ve CDN yayılım hatası story'yi
            # doğrudan düşürüyordu (7 Ağustos 2026: post ve story aynı anda,
            # aynı sebeple kayboldu).
            container_id = self._post_container(endpoint, params)
            if not container_id:
                return {"success": False, "error": "Story container oluşturulamadı"}

            # Bekle ve yayınla
            if not self._wait_for_container(container_id):
                return {"success": False, "error": "Story container hazır olmadı"}

            return self._publish_media(container_id)

        except Exception as e:
            logger.error(f"Story paylaşım hatası: {e}")
            return {"success": False, "error": str(e)}

    # =============================================
    # REELS PAYLAŞIMI
    # =============================================

    def publish_reels(self, video_url: str, caption: str = "",
                      hashtags: list[str] = None,
                      cover_url: str = None,
                      share_to_feed: bool = True) -> dict:
        """
        Instagram Reels paylaş.
        
        Args:
            video_url: Video URL'si (9:16, H.264, 5-90 sn)
            caption: Açıklama
            hashtags: Hashtag listesi
            cover_url: Kapak görseli URL'si
            share_to_feed: Feed'de de paylaşılsın mı
        """
        if not self.is_configured():
            return {"success": False, "error": "API yapılandırılmamış"}

        try:
            full_caption = self._build_caption(caption, hashtags)

            # Reels container oluştur
            endpoint = f"{self.graph_url}/{self.user_id}/media"
            
            params = {
                "access_token": self.access_token,
                "media_type": "REELS",
                "video_url": video_url,
                "caption": full_caption,
                "share_to_feed": str(share_to_feed).lower(),
            }

            if cover_url:
                params["cover_url"] = cover_url

            response = self.session.post(endpoint, data=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            container_id = data.get("id")
            if not container_id:
                return {"success": False, "error": "Reels container oluşturulamadı"}

            # Reels video işleme daha uzun sürebilir
            if not self._wait_for_container(container_id, max_attempts=120):
                return {"success": False, "error": "Reels video işlenemedi"}

            return self._publish_media(container_id)

        except Exception as e:
            logger.error(f"Reels paylaşım hatası: {e}")
            return {"success": False, "error": str(e)}

    # =============================================
    # CONTAINER İŞLEMLERİ
    # =============================================

    def _create_media_container(self, media_type: str, media_url: str,
                                 caption: str = None,
                                 is_carousel_item: bool = False) -> str | None:
        """Medya container'ı oluştur."""
        endpoint = f"{self.graph_url}/{self.user_id}/media"
        
        params = {
            "access_token": self.access_token,
        }

        if media_type == "IMAGE":
            params["image_url"] = media_url
        elif media_type == "VIDEO":
            params["video_url"] = media_url
            params["media_type"] = "VIDEO"

        if is_carousel_item:
            params["is_carousel_item"] = "true"
        elif caption:
            params["caption"] = caption

        return self._post_container(endpoint, params)

    # Meta'nın "medyayı URI'den çekemedim" hatası. Kalıcı gibi görünür
    # (`is_transient: false`) ama gerçekte GEÇİCİDİR: Cloudinary'ye yeni
    # yüklenen dosya, Meta'nın fetcher'ının bastığı CDN kenarına henüz
    # yayılmamıştır.
    #
    # 7 Ağustos 2026 canlı olayı: kullanıcı 4 içerik onayladı, ilk ikisi
    # yayınlandı, son ikisi bu hatayla düştü. Aradaki tek fark zamanlama —
    # başarısızlarda Cloudinary yüklemesi ile container isteği arasında
    # 1 saniye vardı. Reddedilen üç URL'yi sonradan test ettim: üçü de
    # HTTP 200 ve geçerli PNG. Yani dosyalar sağlamdı, tek eksik zamandı.
    # Aynı hata 1 Ağustos'ta da olmuştu; yeniden deneme olmadığı için
    # içerik kalıcı olarak `approved` durumunda takılı kalıyordu.
    _MEDIA_FETCH_ERROR_SUBCODES = {2207052, 2207003}
    _MEDIA_FETCH_ERROR_CODES = {9004}

    @classmethod
    def _is_media_fetch_error(cls, response) -> bool:
        """Yanıt, Meta'nın medyayı çekemediği (yeniden denenebilir) hata mı?"""
        try:
            hata = (response.json() or {}).get("error") or {}
        except ValueError:
            return False
        return (hata.get("code") in cls._MEDIA_FETCH_ERROR_CODES
                or hata.get("error_subcode") in cls._MEDIA_FETCH_ERROR_SUBCODES)

    def _post_container(self, endpoint: str, params: dict,
                        max_retries: int = 3, delay: float = 8.0) -> str | None:
        """Container isteğini at; CDN yayılım hatasında bekleyip yeniden dene."""
        for attempt in range(max_retries):
            try:
                response = self.session.post(endpoint, data=params, timeout=30)
                response.raise_for_status()
                container_id = (response.json() or {}).get("id")
                if container_id:
                    logger.debug(f"Container oluşturuldu: {container_id}")
                return container_id

            except requests.RequestException as e:
                yanit = getattr(e, "response", None)
                yeniden_denenebilir = (
                    yanit is not None
                    and self._is_media_fetch_error(yanit)
                    and attempt < max_retries - 1
                )
                if yeniden_denenebilir:
                    logger.warning(
                        f"Medya henüz CDN'de yayılmamış (deneme "
                        f"{attempt + 1}/{max_retries}), {delay:.0f}s sonra "
                        f"yeniden denenecek."
                    )
                    time.sleep(delay)
                    continue

                logger.error(f"Container oluşturma hatası: {e}")
                if yanit is not None:
                    logger.error(f"API yanıtı: {yanit.text}")
                return None
        return None

    def _create_carousel_container(self, children_ids: list[str],
                                    caption: str) -> str | None:
        """Carousel ana container'ı oluştur."""
        endpoint = f"{self.graph_url}/{self.user_id}/media"
        
        params = {
            "access_token": self.access_token,
            "media_type": "CAROUSEL",
            "caption": caption,
            "children": ",".join(children_ids)
        }

        try:
            response = self.session.post(endpoint, data=params, timeout=30)
            response.raise_for_status()
            return response.json().get("id")
        except requests.RequestException as e:
            logger.error(f"Carousel container hatası: {e}")
            return None

    def _wait_for_container(self, container_id: str,
                            max_attempts: int = None) -> bool:
        """Container'ın hazır olmasını bekle."""
        max_attempts = max_attempts or INSTAGRAM_API["max_poll_attempts"]
        poll_interval = INSTAGRAM_API["poll_interval"]
        
        endpoint = f"{self.graph_url}/{container_id}"
        params = {
            "access_token": self.access_token,
            "fields": "status_code,status"
        }

        for attempt in range(max_attempts):
            try:
                response = self.session.get(endpoint, params=params, timeout=20)
                response.raise_for_status()
                data = response.json()
                
                status = data.get("status_code", "")
                
                if status == "FINISHED":
                    logger.debug(f"Container hazır: {container_id}")
                    return True
                elif status == "ERROR":
                    error_msg = data.get("status", "Bilinmeyen hata")
                    logger.error(f"Container hatası: {error_msg}")
                    return False
                elif status in ("IN_PROGRESS", "PUBLISHED"):
                    logger.debug(f"Container durumu: {status} (deneme {attempt + 1}/{max_attempts})")
                
                time.sleep(poll_interval)

            except requests.RequestException as e:
                logger.warning(f"Container kontrol hatası: {e}")
                time.sleep(poll_interval)

        logger.error(f"Container zaman aşımı: {container_id}")
        return False

    def _publish_media(self, container_id: str) -> dict:
        """Container'ı yayınla."""
        endpoint = f"{self.graph_url}/{self.user_id}/media_publish"
        
        params = {
            "access_token": self.access_token,
            "creation_id": container_id
        }

        try:
            response = self.session.post(endpoint, data=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            media_id = data.get("id")
            if media_id:
                permalink = self._get_permalink(media_id)
                logger.info(f"✅ Medya yayınlandı: {media_id}")
                return {
                    "success": True,
                    "media_id": media_id,
                    "permalink": permalink
                }
            
            return {"success": False, "error": "Medya ID alınamadı"}

        except requests.RequestException as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("error", {}).get("message", error_msg)
                except Exception:
                    pass
            logger.error(f"Yayınlama hatası: {error_msg}")
            return {"success": False, "error": error_msg}

    def _get_permalink(self, media_id: str) -> str | None:
        """Medyanın permalink'ini al."""
        try:
            endpoint = f"{self.graph_url}/{media_id}"
            params = {
                "access_token": self.access_token,
                "fields": "permalink"
            }
            response = self.session.get(endpoint, params=params, timeout=20)
            response.raise_for_status()
            return response.json().get("permalink")
        except Exception:
            return None

    # =============================================
    # HESAP BİLGİLERİ
    # =============================================

    def get_account_info(self) -> dict | None:
        """Instagram hesap bilgilerini al."""
        if not self.is_configured():
            return None

        try:
            endpoint = f"{self.graph_url}/{self.user_id}"
            params = {
                "access_token": self.access_token,
                "fields": "id,username,name,biography,followers_count,follows_count,media_count"
            }
            response = self.session.get(endpoint, params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Hesap bilgisi hatası: {e}")
            return None

    # Meta, (#10) kodunu BİRDEN FAZLA farklı durum için kullanıyor:
    #
    #   "Application does not have permission for this action"
    #       -> gerçek izin sorunu; tüm hesabı ilgilendirir, tur kesilmeli
    #   "Not enough viewers for the media to show insights"
    #       -> o medyaya ÖZEL; Instagram az izlenen içerikte gizlilik gereği
    #          metrik vermiyor. Diğer medyalar sorunsuz çalışabilir.
    #
    # Yalnızca koda bakmak bu ikisini karıştırıyordu: tek bir düşük erişimli
    # gönderi tüm senkronizasyonu durduruyordu (5 Ağustos 2026). 2 takipçili
    # bir hesapta gönderilerin çoğu "not enough viewers" alacağı için bu,
    # insights'ı pratikte tamamen kullanılamaz hale getiriyordu.
    PERMISSION_ERROR_CODE = 10
    PERMISSION_ERROR_MARKERS = ("permission", "not have permission")

    def get_media_insights(self, media_id: str, post_type: str = "post") -> dict | None:
        """
        Yayınlanmış bir medyanın performans metriklerini al.
        Geçerli metrik adları medya tipine göre farklıdır (feed/story/reels).

        İzin eksikse `InsightsPermissionError` fırlatır — çağıran taraf turu
        kesebilsin diye. 2026-08-04 denetiminde 58 yayının 58'i başarısızdı ve
        sebep metrik adı değil, token'da `instagram_manage_insights` izninin
        hiç olmamasıydı; sistem bunu her medya için ayrı ayrı uyarı basarak
        gizliyordu.
        """
        metrics_by_type = {
            "post": "reach,likes,comments,saved,shares",
            "reels": "plays,reach,likes,comments,shares,saved",
            "story": "reach,replies",
        }
        metrics = metrics_by_type.get(post_type, metrics_by_type["post"])

        try:
            endpoint = f"{self.graph_url}/{media_id}/insights"
            params = {"access_token": self.access_token, "metric": metrics}
            response = self.session.get(endpoint, params=params, timeout=20)

            if response.status_code == 400:
                hata = (response.json().get("error") or {})
                mesaj = hata.get("message", "")
                if (hata.get("code") == self.PERMISSION_ERROR_CODE
                        and any(m in mesaj.lower() for m in self.PERMISSION_ERROR_MARKERS)):
                    raise InsightsPermissionError(mesaj)
                if hata.get("code") == self.PERMISSION_ERROR_CODE:
                    # Aynı kod, farklı sebep (ör. "not enough viewers").
                    # O medyayı atla, turu kesme.
                    logger.info(f"Insights yok [media_id={media_id}]: {mesaj[:80]}")
                    return None

            response.raise_for_status()
            data = response.json().get("data", [])

            result = {}
            for item in data:
                name = item.get("name")
                values = item.get("values", [])
                if name and values:
                    result[name] = values[0].get("value")
            return result

        except (ValueError, requests.RequestException) as e:
            logger.warning(f"Insights alınamadı [media_id={media_id}]: {e}")
            return None

    def get_rate_limit_status(self) -> dict | None:
        """
        Instagram'ın günlük içerik yayınlama kotasını (content_publishing_limit)
        sorgular ve Dashboard'da doğrudan gösterilebilecek sade bir sözlüğe
        ({"used": N, "total": N, "remaining": N}) dönüştürür. API yapılandırılmamışsa
        veya istek başarısız olursa None döner — Dashboard bu durumda kartı
        gizler, "0/0" gibi yanıltıcı bir değer göstermez.
        """
        if not self.is_configured():
            return None
        try:
            endpoint = f"{self.graph_url}/{self.user_id}/content_publishing_limit"
            params = {
                "access_token": self.access_token,
                "fields": "config,quota_usage"
            }
            response = self.session.get(endpoint, params=params, timeout=20)
            response.raise_for_status()
            data = response.json().get("data") or []
            if not data:
                return None

            entry = data[0]
            used = entry.get("quota_usage")
            total = (entry.get("config") or {}).get("quota_total")
            if used is None or total is None:
                return None
            return {"used": used, "total": total, "remaining": max(total - used, 0)}
        except Exception as e:
            logger.warning(f"Rate limit kontrol hatası: {e}")
            return None

    # =============================================
    # YARDIMCI METOTLAR
    # =============================================

    @staticmethod
    def _build_caption(caption: str, hashtags: list[str] = None) -> str:
        """Caption ve hashtag'leri birleştir."""
        full_text = caption or ""
        
        if hashtags:
            hashtag_text = " ".join(hashtags)
            full_text = f"{full_text}\n\n{hashtag_text}"
        
        # Instagram 2200 karakter limiti
        if len(full_text) > 2200:
            full_text = full_text[:2197] + "..."
        
        return full_text

    def _wait_for_url_ready(self, url: str, max_retries: int = 5, delay: float = 2.0) -> bool:
        """
        Az önce yüklenen bir medyanın, Instagram'ın onu çekeceği URL üzerinden
        GERÇEKTEN dışarıdan erişilebilir olduğunu doğrular.

        Gerçek olay: cloudinary.uploader.upload() başarıyla dönüp secure_url
        verse bile, o URL Meta'nın sunucularının erişebileceği şekilde CDN'de
        HEMEN tam yayılmış olmayabiliyor — Instagram medya container'ı
        oluştururken "Medya URI'sinden alınamadı" / "Only photo or video can
        be accepted as media type" hatasıyla patlayıp paylaşımı düşürüyordu
        (upload log'u ile hata arasında sadece birkaç saniye vardı). Bu
        kontrol, URL'yi Instagram'a vermeden önce birkaç kez kısa aralıklarla
        deneyip 200 dönene kadar bekler.
        """
        for attempt in range(max_retries):
            try:
                response = requests.head(url, timeout=10, allow_redirects=True)
                if response.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(delay)
        return False

    def upload_media_to_host(self, local_path: str) -> str | None:
        """
        Yerel medya dosyasını hosting servisine yükle.
        Instagram API, medya için public URL gerektirir.

        Öncelik: Cloudinary (yapılandırılmışsa) → MEDIA_HOST_URL (statik sunucu
        kullanıyorsanız basit bir kaçış kapısı) → None.
        """
        cloudinary_configured = bool(
            CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET
        )

        if CLOUDINARY_AVAILABLE and cloudinary_configured:
            try:
                logger.info(f"📤 Medya Cloudinary'ye yükleniyor: {local_path}")
                cloudinary.config(
                    cloud_name=CLOUDINARY_CLOUD_NAME,
                    api_key=CLOUDINARY_API_KEY,
                    api_secret=CLOUDINARY_API_SECRET,
                    secure=True,
                )
                resource_type = "video" if Path(local_path).suffix.lower() in VIDEO_EXTENSIONS else "image"
                result = cloudinary.uploader.upload(
                    local_path,
                    folder=CLOUDINARY_UPLOAD_FOLDER,
                    resource_type=resource_type,
                )
                url = result.get("secure_url")
                if url:
                    logger.info(f"✅ Medya yüklendi: {url}")
                    if not self._wait_for_url_ready(url):
                        logger.warning(
                            f"Medya URL'si {url} birkaç denemeden sonra hâlâ doğrulanamadı "
                            f"(CDN henüz tam yayılmamış olabilir) — yine de devam ediliyor."
                        )
                    return url
                logger.error("Cloudinary yanıtında secure_url bulunamadı.")
                return None
            except Exception as e:
                logger.error(f"Cloudinary yükleme hatası: {e}")
                return None

        if not CLOUDINARY_AVAILABLE:
            logger.warning("cloudinary kütüphanesi kurulu değil (pip install cloudinary).")
        elif not cloudinary_configured:
            logger.warning("Cloudinary kimlik bilgileri (.env) yapılandırılmamış.")

        # Kaçış kapısı: kullanıcı kendi statik medya sunucusunu kullanıyorsa
        if MEDIA_HOST_URL:
            url = f"{MEDIA_HOST_URL.rstrip('/')}/{Path(local_path).name}"
            logger.info(f"📤 MEDIA_HOST_URL üzerinden URL oluşturuldu: {url}")
            return url

        logger.warning("Medya hosting yapılandırılmamış. Medya yüklenemez.")
        return None
