"""
Telegram Onay Botu
Üretilen içerikler için Telegram üzerinden onay/red isteği gönderir; kullanıcı
'✅ Onayla' / '❌ Reddet' butonuna bastığında sonucu veritabanına işler.
Çalıştırma: python main.py --telegram-bot
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, REELS_OUTPUT_DIR,
    DAILY_POST_LIMIT, DAILY_STORY_LIMIT, DAILY_REELS_LIMIT,
)
from src.database import Database
from src.schedule_utils import immediate_schedule_time
from src.image_prompts import build_image_prompt
from src.image_generator import ImageGenerator
from src.story_generator import StoryGenerator
from src.video_generator import VideoGenerator
from src.manual_image import apply_manual_image

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"

CATEGORY_LABELS = {"ai": "🤖 Yapay Zeka", "gaming": "🎮 Oyun Dünyası"}
CONTENT_TYPE_LABELS = {"post": "Feed Gönderisi", "story": "Hikaye", "reels": "Reels"}


def _api_url(method: str) -> str:
    return API_BASE.format(token=TELEGRAM_BOT_TOKEN, method=method)


def _request_with_retry(method: str, url: str, max_retries: int = 3,
                         retry_on_400: bool = False, **kwargs) -> requests.Response:
    """
    Telegram API'sine (POST veya GET) istek atar, geçici hatalarda artan
    bekleme ile yeniden dener.

    Gerçek olay: toplu Telegram bildirimi gönderiminde (20 sendPhoto isteği)
    9'u "400 Client Error" ile başarısız oldu, ama AYNI isteği elle tekrar
    göndermek HER SEFERİNDE başarılı oldu — kalıcı bir istek hatası değil,
    geçici bir Telegram/ağ sorunuydu; kök nedeni tam olarak belirlenemedi
    ama basit bir yeniden deneme tamamen çözdü. retry_on_400, SADECE bu
    tarz medya gönderimlerinde (bkz. send_approval_request) True verilmeli
    — genel olarak 400 gerçek bir bozuk istek anlamına gelebileceğinden
    varsayılan olarak yeniden denenmez.
    """
    # Varsayılan timeout: şu an tüm çağıranlar kendi timeout'unu veriyor,
    # ama biri unutursa istek SONSUZA KADAR asılır ve bot sessizce ölür
    # (uzun-yoklama döngüsü bir daha dönmez). Güvenli bir taban değer
    # konuyor; açıkça verilen değer her zaman kazanır.
    kwargs.setdefault("timeout", 30)

    last_exc = None
    response = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.request(method, url, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            if attempt < max_retries:
                wait = 2 * (attempt + 1)
                logger.warning(
                    f"Telegram istek hatası ({e}), {wait}s bekleyip yeniden deneniyor "
                    f"(deneme {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
                continue
            raise
        else:
            last_exc = None

        retryable = response.status_code == 429 or response.status_code >= 500 or \
            (retry_on_400 and response.status_code == 400)
        if retryable and attempt < max_retries:
            retry_after = 2 * (attempt + 1)
            if response.status_code == 429:
                try:
                    retry_after = response.json().get("parameters", {}).get("retry_after", retry_after)
                except ValueError:
                    pass
            logger.warning(
                f"Telegram geçici hata (HTTP {response.status_code}), {retry_after}s bekleyip "
                f"yeniden deneniyor (deneme {attempt + 1}/{max_retries})"
            )
            time.sleep(retry_after)
            continue
        return response

    if last_exc:
        raise last_exc
    return response


def send_approval_request(content: dict, is_reminder: bool = False) -> int | None:
    """
    Bir içerik için Telegram'a onay/red butonlu bir bildirim gönder.
    Başarılıysa gönderilen mesajın ID'sini döndürür (telegram_message_id
    olarak kaydedilip callback geldiğinde eşleştirmek için kullanılır).

    is_reminder=True: aynı içerik DAHA ÖNCE gönderilmiş ama cevapsız kalmış
    (bkz. send_reminder / Scheduler.send_reminders). Caption'a bunu belirten
    bir başlık eklenir — aksi halde kullanıcı bunun yeni mi yoksa
    unutulmuş eski bir öğe mi olduğunu ayırt edemez.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID yapılandırılmamış, bildirim gönderilemedi.")
        return None

    content_id = content["id"]
    category_label = CATEGORY_LABELS.get(content.get("category"), content.get("category", ""))
    type_label = CONTENT_TYPE_LABELS.get(content.get("content_type"), content.get("content_type", ""))
    score = content.get("relevance_score")

    caption_lines = []
    if is_reminder:
        caption_lines.append("🔔 Hatırlatma — hâlâ onay bekliyor")
    caption_lines += [f"{type_label} — {category_label}", "", str(content.get("news_title", ""))[:200]]
    carousel_paths = content.get("carousel_paths")
    if carousel_paths and len(carousel_paths) > 1:
        caption_lines.append(f"\n📸 {len(carousel_paths)} slaytlı carousel (burada sadece kapak gösteriliyor)")
    if score is not None:
        caption_lines.append(f"\nSkor: {score:.2f}")
    caption = "\n".join(caption_lines)[:1024]  # Telegram caption limiti

    keyboard = [[
        {"text": "✅ Onayla", "callback_data": f"approve:{content_id}"},
        {"text": "❌ Reddet", "callback_data": f"reject:{content_id}"},
    ]]
    # Reels tek bir video dosyası olduğundan (birden fazla segment görseli
    # içerir) manuel görsel değişimi bu sürümde desteklenmiyor — sadece
    # post/story için gösterilir.
    if content.get("content_type") != "reels":
        keyboard.append([{"text": "🎨 Farklı Görsel İste", "callback_data": f"image:{content_id}"}])

    reply_markup = {"inline_keyboard": keyboard}

    media_path = content.get("media_path")
    media_file = Path(media_path) if media_path else None
    is_video = media_file is not None and media_file.suffix.lower() == ".mp4"

    try:
        if media_file and media_file.exists():
            method = "sendVideo" if is_video else "sendPhoto"
            field_name = "video" if is_video else "photo"
            # Dosya içeriği bir kez belleğe okunuyor — retry sırasında AYNI açık
            # dosya tanıtıcısı yeniden kullanılsaydı, ilk (başarısız) denemeden
            # sonra imleç dosya sonunda kalıp ikinci denemede BOŞ bir dosya
            # gönderilirdi. retry_on_400=True: gerçek olay — toplu gönderimde
            # 20 sendPhoto isteğinden 9'u geçici "400 Client Error" ile
            # başarısız oldu, aynı isteğin elle tekrarı her seferinde başarılı
            # oldu (bkz. _request_with_retry docstring'i).
            media_bytes = media_file.read_bytes()
            response = _request_with_retry(
                "POST", _api_url(method),
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "reply_markup": json.dumps(reply_markup),
                },
                files={field_name: (media_file.name, media_bytes)},
                timeout=60,
                retry_on_400=True,
            )
        else:
            response = _request_with_retry(
                "POST", _api_url("sendMessage"),
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": caption,
                    "reply_markup": json.dumps(reply_markup),
                },
                timeout=30,
            )

        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            message_id = result["result"]["message_id"]
            logger.info(f"📲 Telegram onay isteği gönderildi: ID={content_id} → message_id={message_id}")
            return message_id

        logger.error(f"Telegram API hatası: {result}")
        return None

    except requests.RequestException as e:
        logger.error(f"Telegram gönderim hatası: {e}")
        return None


def send_reminder(content: dict) -> int | None:
    """Cevapsız kalmış bir onay isteğini hatırlatma olarak YENİDEN gönderir.

    Telegram eski mesajları öne çıkarmaz; kullanıcının akışın ortasında
    kaybolmuş bir mesajı fark etmesini beklemek yerine yeni bir mesaj
    olarak tekrar gönderiliyor (bkz. Scheduler.send_reminders). Eski
    mesajın butonları kaldırılır — aksi halde aynı içerik için iki canlı
    Onayla/Reddet çifti dolaşır ve hangisine basıldığı kullanıcı için
    belirsiz olur.
    """
    old_message_id = content.get("telegram_message_id")
    new_message_id = send_approval_request(content, is_reminder=True)
    if new_message_id and old_message_id:
        _remove_buttons(TELEGRAM_CHAT_ID, old_message_id)
    return new_message_id


def _answer_callback_query(callback_query_id: str, text: str = ""):
    try:
        _request_with_retry(
            "POST", _api_url("answerCallbackQuery"),
            data={"callback_query_id": callback_query_id, "text": text},
            timeout=15,
        )
    except requests.RequestException as e:
        logger.warning(f"answerCallbackQuery hatası: {e}")


def _remove_buttons(chat_id, message_id: int):
    """Onaylandıktan/reddedildikten sonra butonları kaldır (tekrar tıklanmasın diye)."""
    try:
        _request_with_retry(
            "POST", _api_url("editMessageReplyMarkup"),
            data={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": json.dumps({"inline_keyboard": []}),
            },
            timeout=15,
        )
    except requests.RequestException as e:
        logger.warning(f"editMessageReplyMarkup hatası: {e}")


def _send_text(chat_id, text: str, reply_to_message_id: int = None) -> int | None:
    """Metin mesajı gönderir, başarılıysa gönderilen mesajın message_id'sini döndürür."""
    try:
        data = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        response = _request_with_retry("POST", _api_url("sendMessage"), data=data, timeout=15)
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        return None
    except requests.RequestException as e:
        logger.warning(f"sendMessage hatası: {e}")
        return None


_CONTENT_TYPE_LABELS = {"post": "gönderi", "story": "hikaye", "reels": "reels"}


def _quota_warning(db: Database, content_type: str) -> str | None:
    """Bugün için taahhüt edilen içerik günlük limiti aştıysa uyarı metni döndür.

    Manuel onay kotayı BİLİNÇLİ olarak atlıyor — insan kararı, ve "ben onay
    verince paylaş" açık bir kullanıcı isteğiydi. Bu fonksiyon engellemiyor,
    yalnızca görünür kılıyor.

    Gerekçe: 2 Ağustos 2026'da `DAILY_POST_LIMIT=2` ayarlıyken 18 feed
    gönderisi yayınlandı. Kota kontrolü sadece `auto_schedule_content`
    içindeydi, o da AUTO_PUBLISH_THRESHOLD (0.75) yüzünden neredeyse hiç
    tetiklenmiyordu; yayınlanan içeriğin tamamı manuel onaydan geçiyordu ve
    limitin aşıldığını gösteren hiçbir işaret yoktu.

    Sayım için `get_scheduled_count_today` TEK BAŞINA kullanılıyor: her onay
    (telegram/dashboard/auto) bir scheduled_posts satırı yaratıyor, dolayısıyla
    bugünün taahhütlerinin tamamını zaten kapsıyor. Buna ayrıca
    `get_today_publish_count` eklemek, yayınlanmış bir gönderiyi iki kez
    sayardı.
    """
    limits = {
        "post": DAILY_POST_LIMIT,
        "story": DAILY_STORY_LIMIT,
        "reels": DAILY_REELS_LIMIT,
    }
    limit = limits.get(content_type)
    if not limit:
        return None

    try:
        committed = db.get_scheduled_count_today(content_type)
    except Exception as e:
        logger.warning(f"Kota uyarısı hesaplanamadı: {e}")
        return None

    if committed <= limit:
        return None

    label = _CONTENT_TYPE_LABELS.get(content_type, content_type)
    return (
        f"⚠️ Günlük limit aşıldı: bugün {committed} {label} paylaşılacak "
        f"(ayarlı limit {limit}). Onayın iptal edilmedi — sadece bilgin olsun "
        f"diye: Instagram'da yoğun paylaşım spam sinyali sayılabiliyor."
    )


def _is_authorized_chat(chat_id) -> bool:
    """Güncellemenin yapılandırılmış TELEGRAM_CHAT_ID'den geldiğini doğrular.

    Bot herkesin bulup /start yazabileceği bir Telegram kullanıcı adına sahip
    olduğundan, gelen her callback/mesajın gerçekten sahibinin sohbetinden
    geldiği burada kontrol edilmeden işlenmemeli — aksi halde başka bir
    kullanıcı content_id'leri veya mesaj ID'lerini tahmin ederek onay/red/
    görsel-değiştirme akışını tetikleyebilir.
    """
    return bool(TELEGRAM_CHAT_ID) and str(chat_id) == str(TELEGRAM_CHAT_ID)


def _handle_callback(callback_query: dict, db: Database):
    """
    Bir 'callback_query' güncellemesini işle: onay/red kararını veritabanına
    yaz, butonları kaldır, kullanıcıya sonucu bildir.
    """
    callback_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message", {}) or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    try:
        action, content_id_str = data.split(":", 1)
        content_id = int(content_id_str)
    except (ValueError, AttributeError):
        _answer_callback_query(callback_id, "Geçersiz istek")
        return

    content = db.get_content_by_id(content_id)
    if not content:
        _answer_callback_query(callback_id, "İçerik bulunamadı")
        return

    if action == "approve":
        content_type = content["content_type"]
        scheduled_time = immediate_schedule_time()
        db.schedule_post(content_id, scheduled_time, content_type, source="telegram")
        db.update_content_status(content_id, "approved")
        _answer_callback_query(callback_id, "Onaylandı ✅")
        result_text = "✅ Onaylandı — birkaç dakika içinde paylaşılacak."

        # Günlük kota BİLİNÇLİ olarak engellenmiyor: manuel onay insan
        # kararıdır ve "ben onay verince paylaş" açık bir kullanıcı isteğiydi.
        # Ama sessizce aşılması da doğru değil — 2 Ağustos 2026'da
        # DAILY_POST_LIMIT=2 iken 18 feed gönderisi yayınlandı, çünkü her
        # onay kotadan bağımsız olarak anında zamanlanıyordu ve bunu
        # gösteren hiçbir işaret yoktu. Instagram'da günde 18 gönderi gerçek
        # bir spam sinyali. Karar kullanıcıda kalıyor, ama artık görünür.
        uyari = _quota_warning(db, content_type)
        if uyari:
            result_text += f"\n\n{uyari}"

    elif action == "reject":
        db.update_content_status(content_id, "rejected")
        _answer_callback_query(callback_id, "Reddedildi ❌")
        result_text = "❌ Reddedildi."

    elif action == "image":
        # Mevcut onayla/reddet/farklı-görsel butonları kasıtlı olarak
        # kaldırılmıyor — kullanıcı Gemini'de görsel üretirken beklemek
        # zorunda değil, dilerse mevcut görselle onaylayabilir/reddedebilir.
        prompt = build_image_prompt(content)
        instruction = (
            "🎨 Aşağıdaki prompt'u Gemini'ye yapıştırıp bir görsel üretin, "
            "sonra ürettiğiniz görseli BU MESAJA YANIT olarak gönderin:\n\n"
            f"{prompt}"
        )
        prompt_message_id = _send_text(chat_id, instruction)
        if prompt_message_id:
            db.set_manual_image_prompt(content_id, prompt_message_id)
            _answer_callback_query(callback_id, "Prompt gönderildi")
        else:
            _answer_callback_query(callback_id, "Prompt gönderilemedi")
        return

    elif action == "manual_done":
        # Meta feed post yayınını API'den engellediği için gönderilen elle
        # paylaşım paketindeki onay butonu (bkz. send_manual_publish_fallback).
        db.mark_manually_published(content_id, content["content_type"])
        _answer_callback_query(callback_id, "Kaydedildi ✅")
        result_text = "✅ Elle paylaşıldı olarak işaretlendi."

    else:
        _answer_callback_query(callback_id, "Bilinmeyen işlem")
        return

    if chat_id and message_id:
        _remove_buttons(chat_id, message_id)
        _send_text(chat_id, result_text)



def send_reels_for_manual_publish(content: dict, video_path: str) -> int | None:
    """Otomatik üretilen SESSİZ reels videosunu elle yayınlanmak üzere gönderir.

    Neden onay butonu yok: bu video Instagram'a API ile YAYINLANMIYOR.
    Instagram'ın müzik kütüphanesi Graph API'den erişilemediği için
    (Meta referansında reels'in tek ses parametresi `audio_name` ve o da
    yalnızca mevcut sesi yeniden adlandırıyor), müzik uygulamada ekleniyor.
    API ile yayınlansaydı müziksiz çıkardı.

    Akış: video Telegram'a gelir → indirirsin → Instagram uygulamasında
    müziği ekleyip paylaşırsın. Caption da kopyalanmaya hazır gönderilir.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram yapılandırılmamış, reels videosu gönderilemedi.")
        return None

    category_label = CATEGORY_LABELS.get(content.get("category"), content.get("category", ""))
    caption = (content.get("caption") or "").strip()
    hashtags = content.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [hashtags]

    video_file = Path(video_path) if video_path else None
    if not video_file or not video_file.exists():
        logger.error(f"Reels videosu bulunamadı: {video_path}")
        return None

    aciklama = (
        f"🎬 Reels hazır — {category_label}\n\n"
        f"1) Videoyu indir\n"
        f"2) Instagram'da paylaşırken müziği uygulamadan ekle\n"
        f"3) Bir sonraki mesajdaki açıklamayı yapıştır\n\n"
        f"Video sessiz üretildi — müziği uygulamada seçeceğin için."
    )

    try:
        # Dosya bir kez belleğe okunuyor: retry sırasında aynı açık tanıtıcı
        # yeniden kullanılsaydı imleç dosya sonunda kalıp ikinci denemede boş
        # dosya gönderilirdi (bkz. send_approval_request'teki aynı gerekçe).
        response = _request_with_retry(
            "POST", _api_url("sendVideo"),
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": aciklama},
            files={"video": (video_file.name, video_file.read_bytes())},
            timeout=120,
            retry_on_400=True,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            logger.error(f"Telegram API hatası (reels): {result}")
            return None
        message_id = result["result"]["message_id"]
    except (requests.RequestException, OSError) as e:
        logger.error(f"Reels videosu gönderilemedi: {e}")
        return None

    # Caption AYRI mesaj olarak gidiyor: Telegram'da medya açıklamasını
    # kopyalamak zahmetli, düz metin mesajına uzun basınca tek dokunuşla
    # kopyalanıyor.
    if caption:
        metin = caption
        if hashtags:
            metin += "\n\n" + " ".join(hashtags)
        _send_text(TELEGRAM_CHAT_ID, metin)

    logger.info(f"🎬 Reels videosu elle yayın için gönderildi: message_id={message_id}")
    return message_id


def send_manual_publish_fallback(content: dict) -> int | None:
    """API üzerinden yayınlanamayan bir POST ya da STORY için elle paylaşım
    paketi gönderir: görsel + ayrı, kopyalanabilir caption metni + '✅ Elle
    Paylaştım' onay butonu.

    Neden var: 15 Ağustos 2026'dan beri Meta feed post yayınlama çağrılarını
    (media_publish) code 4 / subcode 2207051 ile reddediyor — teknik bir
    kota değil, Meta'nın belgelemediği bir anti-spam kararı (bkz. proje
    hafızası: instagram_feed_publish_blocked). 23 Ağustos'ta engel
    GENİŞLEDİ: container oluşturmanın İLK adımında "API access blocked"
    (code 200) hatası başladı ve story da etkilenir oldu (eskiden yalnızca
    post etkileniyordu, story'ler "Story'ler etkilenmiyor" diye
    belgelenmişti — o not artık geçerli değil). Otomatik yeniden deneme
    yoktu; onaylanan içerik kullanıcı Instagram'dan ELLE paylaşana kadar
    hiçbir yere gitmiyordu ve tek bildirim düz metindi — görsel/caption'a
    ayrıca ulaşmak gerekiyordu. Bu fonksiyon paylaşıma hazır paketi
    doğrudan Telegram'a taşır (story'nin caption'ı yoktur, o durumda
    ikinci metin mesajı sessizce atlanır).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram yapılandırılmamış, elle paylaşım paketi gönderilemedi.")
        return None

    content_id = content["id"]
    media_path = content.get("media_path")
    media_file = Path(media_path) if media_path else None
    if not media_file or not media_file.exists():
        logger.error(f"Elle paylaşım için medya bulunamadı: {media_path}")
        return None

    aciklama = (
        "📤 Otomatik paylaşım Meta tarafından engellendi (feed post kısıtlaması)\n\n"
        "1) Görseli indir\n"
        "2) Bir sonraki mesajdaki açıklamayı kopyala\n"
        "3) Instagram'dan elle paylaş\n"
        "4) Paylaştıktan sonra aşağıya bas"
    )
    keyboard = [[{"text": "✅ Elle Paylaştım", "callback_data": f"manual_done:{content_id}"}]]

    try:
        media_bytes = media_file.read_bytes()
        response = _request_with_retry(
            "POST", _api_url("sendPhoto"),
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": aciklama,
                "reply_markup": json.dumps({"inline_keyboard": keyboard}),
            },
            files={"photo": (media_file.name, media_bytes)},
            timeout=60,
            retry_on_400=True,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            logger.error(f"Telegram API hatası (elle paylaşım): {result}")
            return None
        message_id = result["result"]["message_id"]
    except requests.RequestException as e:
        logger.error(f"Elle paylaşım paketi gönderilemedi: {e}")
        return None

    # Caption AYRI mesaj olarak gidiyor: fotoğraf açıklamasını kopyalamak
    # zahmetli, düz metin mesajına uzun basınca tek dokunuşla kopyalanıyor
    # (bkz. send_reels_for_manual_publish'teki aynı gerekçe).
    caption = (content.get("caption") or "").strip()
    hashtags = content.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    if caption:
        metin = caption
        if hashtags:
            metin += "\n\n" + " ".join(hashtags)
        _send_text(TELEGRAM_CHAT_ID, metin)

    logger.info(f"📤 Elle paylaşım paketi gönderildi: content_id={content_id} message_id={message_id}")
    return message_id


def _download_telegram_file_bytes(file_id: str) -> bytes | None:
    """Telegram file_id'sinden dosyanın (foto/video) byte içeriğini indirir."""
    try:
        response = _request_with_retry("GET", _api_url("getFile"), params={"file_id": file_id}, timeout=15)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            return None

        file_path = result["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        img_response = _request_with_retry("GET", file_url, timeout=30)
        img_response.raise_for_status()
        return img_response.content

    except requests.RequestException as e:
        logger.warning(f"Telegram dosya indirme hatası: {e}")
        return None


def _handle_photo_reply(message: dict, db: Database,
                         img_gen: ImageGenerator, story_gen: StoryGenerator):
    """
    Kullanıcının '🎨 Farklı Görsel İste' prompt mesajına YANIT olarak
    gönderdiği fotoğrafı işler: indirir, ilgili içeriğe kaydeder, içeriği
    bu görselle yeniden render eder ve taze bir onay mesajı gönderir.
    Eşleşen bir prompt bulunamazsa (ör. rastgele bir foto, yanlış mesaja
    yanıt) sessizce yok sayılır.
    """
    chat_id = message.get("chat", {}).get("id")
    photo = message.get("photo")
    reply_to = message.get("reply_to_message")
    if not photo or not reply_to:
        return

    content = db.get_content_by_manual_prompt_message(reply_to.get("message_id"))
    if not content:
        return

    content_id = content["id"]

    file_id = photo[-1]["file_id"]  # Telegram en büyük çözünürlüğü sonda döner
    image_bytes = _download_telegram_file_bytes(file_id)
    if image_bytes is None:
        _send_text(chat_id, "⚠️ Görsel indirilemedi, lütfen tekrar gönderin.")
        return

    # Eski onay mesajının butonlarını kaldır — aynı içerik için iki canlı
    # buton seti (eski görsel + yeni görsel) aynı anda kalmasın diye.
    old_message_id = content.get("telegram_message_id")
    if chat_id and old_message_id:
        _remove_buttons(chat_id, old_message_id)

    new_path = apply_manual_image(content_id, image_bytes, db, img_gen, story_gen)

    if not new_path:
        _send_text(chat_id, "⚠️ Görsel kaydedildi ama içerik yeniden oluşturulamadı.")
        return

    refreshed_content = db.get_content_by_id(content_id)
    new_message_id = send_approval_request(refreshed_content)
    if new_message_id:
        db.set_content_telegram_message(content_id, new_message_id)


def _handle_video_reply(message: dict, db: Database, video_gen: "VideoGenerator"):
    """
    Kullanıcının Telegram'a gönderilen reels videosuna YANIT
    olarak Gemini web'de (Veo) kendi ürettiği videoyu gönderdiğinde işler:
    indirir, sağ üst köşesine marka logosunu bindirir (otomatik MoviePy
    slaytları kaldırıldığından marka sürekliliğini sağlayan tek nokta budur),
    ilgili reels içeriğine media_path olarak kaydeder ve normal onay akışını
    tetikler. Eşleşen bir prompt bulunamazsa sessizce yok sayılır.
    """
    chat_id = message.get("chat", {}).get("id")
    video = message.get("video")
    reply_to = message.get("reply_to_message")
    if not video or not reply_to:
        return

    content = db.get_content_by_manual_video_prompt_message(reply_to.get("message_id"))
    if not content:
        return

    content_id = content["id"]
    file_id = video["file_id"]
    video_bytes = _download_telegram_file_bytes(file_id)
    if video_bytes is None:
        _send_text(chat_id, "⚠️ Video indirilemedi (Telegram bot API'si 20MB üzerini "
                             "indiremiyor olabilir), lütfen sıkıştırıp tekrar gönderin.")
        return

    REELS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    category = content.get("category", "ai")
    raw_path = REELS_OUTPUT_DIR / f"reels_{category}_{timestamp}_raw.mp4"
    raw_path.write_bytes(video_bytes)

    final_path = REELS_OUTPUT_DIR / f"reels_{category}_{timestamp}.mp4"
    if video_gen.add_logo_watermark(str(raw_path), str(final_path)):
        raw_path.unlink(missing_ok=True)
    else:
        raw_path.rename(final_path)

    db.update_content_media(content_id, str(final_path))

    refreshed_content = db.get_content_by_id(content_id)
    new_message_id = send_approval_request(refreshed_content)
    if new_message_id:
        db.set_content_telegram_message(content_id, new_message_id)


def _handle_command(message: dict, db: Database):
    """Salt-okunur teşhis komutlarını işle.

    Eskiden "/" ile başlayan her mesaj sessizce yok sayılıyordu — `/start`
    yazınca hiçbir şey olmamasının sebebi buydu. Artık bilinen komutlar
    cevaplanıyor, bilinmeyenler için yardım gösteriliyor.

    Bu komutların HİÇBİRİ sistemde değişiklik yapmaz. Bilinçli bir sınır:
    Telegram kanalını ele geçiren biri sunucuda iş yaptıramamalı. Onay/red
    gibi durum değiştiren işlemler butonlarla ve content_id doğrulamasıyla
    yapılıyor, serbest metin komutlarıyla değil.
    """
    from src import diagnostics

    chat_id = message.get("chat", {}).get("id")
    # "/durum@botadi" biçimini de kabul et (gruplarda Telegram böyle gönderir).
    komut = (message.get("text") or "").strip().split()[0].lstrip("/").split("@")[0].lower()

    işleyiciler = {
        "durum": lambda: diagnostics.system_status(db),
        "status": lambda: diagnostics.system_status(db),
        "huni": lambda: diagnostics.funnel_report(db),
        "loglar": lambda: diagnostics.recent_errors(),
        "kota": lambda: diagnostics.quota_report(db),
        "yardim": diagnostics.help_text,
        "yardım": diagnostics.help_text,
        "help": diagnostics.help_text,
        "start": diagnostics.help_text,
    }

    işleyici = işleyiciler.get(komut)
    if not işleyici:
        _send_text(chat_id, f"Bilinmeyen komut: /{komut}\n\n{diagnostics.help_text()}")
        return

    try:
        _send_text(chat_id, işleyici())
    except Exception as e:
        # Teşhis komutu botu düşürmemeli — raporlanamıyorsa bunu söyle.
        logger.warning(f"Teşhis komutu başarısız [/{komut}]: {e}")
        _send_text(chat_id, f"⚠️ Rapor üretilemedi (/{komut}): {e}")


def _handle_manual_research_text(message: dict, db: Database):
    """
    Telegram'a düz metin olarak yapıştırılan bir araştırma bulgusunu (ör.
    Gemini Spark'ın ürettiği bir haberi kopyala-yapıştır ile) external_research
    tablosuna kaydeder. MCP köprüsünün add_research_finding tool'uyla aynı
    tabloyu kullanır — Gemini'nin bağlı-uygulama bağlantısı çalışmadığında
    (ör. custom connector reddi) manuel bir yedek giriş kapısı sağlar.
    İlk satır başlık, kalan satırlar özet olarak alınır; tek satırsa aynı
    metin hem başlık hem özet olur.
    """
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return

    title = lines[0][:200]
    summary = "\n".join(lines[1:]) if len(lines) > 1 else lines[0]

    research_id = db.add_external_research(
        source="telegram_manual", title=title, summary=summary,
    )
    _send_text(
        chat_id,
        f"✅ Araştırma bulgusu kaydedildi (ID: {research_id}).\n"
        f"Dashboard'daki 🔬 Spark Araştırmaları sayfasından \"Haberlere Aktar\" "
        f"ile pipeline'a alabilirsin."
    )


def run_bot_polling():
    """Telegram'dan gelen buton tıklamalarını ve foto yanıtlarını sonsuz döngüyle (long polling) dinle."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN yapılandırılmamış. Bot başlatılamıyor.")
        return

    db = Database()
    img_gen = ImageGenerator(db=db)
    story_gen = StoryGenerator(db=db)
    video_gen = VideoGenerator(db=db)
    offset = 0
    logger.info("📲 Telegram bot polling başladı... (durdurmak için Ctrl+C)")

    while True:
        try:
            response = requests.get(
                _api_url("getUpdates"),
                params={
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": json.dumps(["callback_query", "message"]),
                },
                timeout=40,
            )
            response.raise_for_status()
            data = response.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                callback_query = update.get("callback_query")
                if callback_query:
                    chat_id = (callback_query.get("message") or {}).get("chat", {}).get("id")
                    if not _is_authorized_chat(chat_id):
                        logger.warning(f"Yetkisiz chat_id'den callback_query görmezden gelindi: {chat_id}")
                        _answer_callback_query(callback_query["id"], "Yetkisiz")
                        continue
                    _handle_callback(callback_query, db)
                    continue

                message = update.get("message")
                if message:
                    chat_id = message.get("chat", {}).get("id")
                    if not _is_authorized_chat(chat_id):
                        logger.warning(f"Yetkisiz chat_id'den mesaj görmezden gelindi: {chat_id}")
                        continue
                    if message.get("photo"):
                        _handle_photo_reply(message, db, img_gen, story_gen)
                    elif message.get("video"):
                        _handle_video_reply(message, db, video_gen)
                    elif message.get("text", "").startswith("/"):
                        _handle_command(message, db)
                    elif message.get("text"):
                        _handle_manual_research_text(message, db)

        except requests.RequestException as e:
            logger.warning(f"Telegram polling hatası: {e}")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("⏹️ Telegram bot durduruldu.")
            break


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    # Bu modül tek başına çalıştırıldığında da sır maskeleme devrede olmalı:
    # polling hatalarının metni bot token'ını içeren tam URL'yi taşıyor.
    from src.log_redaction import install as install_log_redaction
    install_log_redaction()
    run_bot_polling()
