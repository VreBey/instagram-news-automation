"""Günlük derleme: birden çok haberi TEK feed gönderisinde toplayan carousel.

Neden var (3 Ağustos 2026 ölçümü): günde ~75 haber toplanıyor ama feed kotası
2 gönderi. Kapsamı tek tek yayınlayarak büyütmek Instagram'da spam sinyali —
2 Ağustos'ta `DAILY_POST_LIMIT=2` ayarlıyken 18 gönderi yayınlanmıştı.
Derleme, kapsamı gönderi SAYISINI artırmadan büyütür: 1 feed slotu, 6 haber.

İki tasarım kararı:

* **Ham haberden değil, üretilmiş taslaklardan kurulur.** `summary_text`
  zaten Türkçe ve hazır olduğu için ek Gemini çağrısı gerekmez. Ayrıca
  üretilip hiç yayınlanmayan taslak birikmesi (ölçümde 255 adet, üretilen
  içeriğin ~%70'i) böylece değerlendirilmiş olur — o birikme saf israftı.
* **Derlemeye giren taslak `used_in_roundup` ile işaretlenir**, `rejected`
  ile değil. Aksi halde puanlama ölçümünün yer gerçeği bozulurdu: kullanıcı
  o içeriği reddetmedi, derlemeye girdiği için tekil yayınlanmadı.
"""

import logging

from config import ROUNDUP_ITEM_COUNT, ROUNDUP_MIN_ITEMS
from src.content_language import is_translated

logger = logging.getLogger(__name__)

MAX_HEADLINE_LENGTH = 70
# Kaynak adları besleme kalitesine göre çok değişiyor: canlı veride 107
# karakterlik yazar künyeleri var ("Wang; Bo; Tang; Sijin; Tan; ..."). Caption'a
# olduğu gibi konsa satırı okunmaz hale getirirdi.
MAX_SOURCE_LENGTH = 24
# NOT: `is_translated` src/content_language.py'ye taşındı. Aynı kontrolün iki
# kopyası olması, birinin düzeltilip diğerinin eskide kalmasına yol açardı —
# bu hata zaten tam olarak öyle tekrarlandı.


def clean_source_name(source: str | None) -> str:
    """Kaynak adını caption'da kullanılabilir hale getir.

    Bazı beslemeler `source_name` alanına yazar künyesini dolduruyor
    (noktalı virgülle ayrılmış isim listeleri). İlk parça alınır ve
    kısaltılır.
    """
    if not source:
        return ""
    ilk = source.split(";")[0].split(",")[0].strip()
    if len(ilk) > MAX_SOURCE_LENGTH:
        ilk = ilk[:MAX_SOURCE_LENGTH].rstrip() + "…"
    return ilk


def build_roundup_slides(items: list[dict]) -> list[dict]:
    """Taslaklardan carousel slaytları üret: 1 kapak + her haber için 1 slayt.

    `generate_carousel_images` haber biçimli sözlük listesi beklediği için
    slaytlar o şekle uyarlanır. Her slayt kendi haberinin görselini ve
    kaynağını taşır — paylaşılan tek görsel kullanmak, alakasız eşleşmelere
    yol açardı.
    """
    if not items:
        return []

    kategoriler = {i.get("category") for i in items if i.get("category")}
    kapak_kategori = kategoriler.pop() if len(kategoriler) == 1 else "ai"

    # `display_title` sözleşmesi (bkz. image_generator.generate_carousel_images):
    # çizilecek metin AÇIKÇA verilir, kaynak alanlara düşülmez.
    slides = [{
        "display_title": f"Günün {len(items)} Haberi",
        "display_subtitle": None,
        "category": kapak_kategori,
        "source_name": "",
        # Kapakta bilinçli olarak görsel YOK: haberlerden birinin görselini
        # kapağa koymak, derlemeyi o haberin gönderisi gibi gösterirdi.
        "image_url": None,
    }]

    for item in items:
        slides.append({
            "display_title": (item.get("summary_text") or "").strip(),
            "display_subtitle": None,
            "category": item.get("category", "ai"),
            "source_name": item.get("source_name") or "",
            "image_url": item.get("image_url"),
            # Beslemede görsel yoksa slayt haberin kendi sayfasındaki
            # og:image'e düşebilsin (yoksa doğrudan stok fotoğrafa iniyordu).
            "news_url": item.get("news_url"),
        })

    return slides


def build_roundup_caption(items: list[dict]) -> str:
    """Derleme için caption üret.

    İçerik kalitesi kurallarına uyar: ilk satır (kanca) kısa tutulur —
    Instagram ~125 karakterde "… daha" ile kesiyor — ve etiket sayısı
    sınırlıdır. Hiçbir sayı/iddia uydurulmaz; yalnızca gerçekten derlemeye
    giren haberler listelenir.
    """
    hook = f"🗞️ Günün {len(items)} önemli haberi — kaydırarak hepsine göz at"

    satirlar = [hook, ""]
    for i, item in enumerate(items, 1):
        baslik = (item.get("summary_text") or "").strip()
        if len(baslik) > MAX_HEADLINE_LENGTH:
            kesme = baslik[:MAX_HEADLINE_LENGTH].rsplit(" ", 1)[0]
            baslik = kesme + "…"
        kaynak = clean_source_name(item.get("source_name"))
        satirlar.append(f"{i}. {baslik}" + (f" — {kaynak}" if kaynak else ""))

    satirlar.append("")
    satirlar.append("💬 Hangisi ilgini çekti?")
    satirlar.append("")

    # Etiketler ContentProcessor'ın yedek listesinden alınır: 3-5 niş etiket
    # kuralı ve yasaklı etiket listesi orada tek yerde tanımlı, burada
    # kopyalamak ikisinin zamanla ayrışmasına yol açardı.
    from src.content_processor import ContentProcessor

    kategoriler = [i.get("category") for i in items]
    ana_kategori = "gaming" if kategoriler.count("gaming") >= len(items) / 2 else "ai"
    satirlar.append(" ".join(ContentProcessor._default_hashtags(ana_kategori)))

    return "\n".join(satirlar)


def select_roundup_items(db, limit: int | None = None) -> list[dict]:
    """Derlemeye girecek taslakları seç; yeterli değilse boş liste döndür.

    Havuz bilinçli olarak geniş çekilir: çevrilmemiş öğeler elendikten ve
    kaynak çeşitliliği uygulandıktan sonra hâlâ `limit` kadar haber kalması
    gerekiyor.
    """
    from src.database import _apply_source_diversity

    limit = limit or ROUNDUP_ITEM_COUNT
    havuz = db.get_roundup_candidates(limit=limit * 5)

    cevrilmis = [i for i in havuz if is_translated(i)]
    if len(cevrilmis) < len(havuz):
        logger.info(
            f"ℹ️ Derleme: {len(havuz) - len(cevrilmis)} taslak çevrilmemiş "
            f"(özet İngilizce başlığın kopyası), elendi."
        )

    # Çeşitlilik burada da gerekli: tavan `get_unprocessed_news`'e eklenmişti
    # ama derleme adayları oradan geçmiyor. İlk üretilen derlemede 6 haberin
    # 3'ü tek kaynaktandı — aynı yoğunlaşma deseni.
    secim = _apply_source_diversity(cevrilmis, limit)

    if len(secim) < ROUNDUP_MIN_ITEMS:
        logger.info(
            f"ℹ️ Derleme için yeterli taslak yok ({len(secim)}/{ROUNDUP_MIN_ITEMS}), atlanıyor."
        )
        return []
    return secim
