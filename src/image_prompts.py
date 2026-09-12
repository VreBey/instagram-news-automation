"""
Manuel Gemini Görsel Prompt Üretimi
Telegram'da "🎨 Farklı Görsel İste" butonuna basıldığında kullanıcıya
gönderilecek İngilizce Gemini görsel-üretim prompt'unu oluşturur. Kullanıcı
bu prompt'u Gemini'nin ücretsiz web arayüzünde kendisi çalıştırıp ürettiği
görseli bota geri gönderir (bkz. src/telegram_bot.py _handle_photo_reply).

Prompt bilinçli olarak gerçek logo/marka/kişi İSTEMEZ — özgün, stilize bir
kavramsal illüstrasyon ister. Bu, projenin telif riski değerlendirmesiyle
tutarlıdır: haberin kendi gerçek görseli (article_photos.py) ayrı bir
kaynak olarak zaten kullanılıyor; bu prompt sadece kullanıcının Gemini'den
üreteceği ORİJİNAL alternatif için.
"""

_STYLE_BY_CATEGORY = {
    "ai": "futuristic, abstract artificial-intelligence concept art — glowing "
          "neural-network motifs, soft blue/purple lighting, clean sci-fi aesthetic",
    "gaming": "vibrant, dynamic video-game-culture concept art — bold neon "
              "accents, energetic composition, modern esports aesthetic",
}


def build_image_prompt(content: dict) -> str:
    """Haber başlığı ve kategoriye göre İngilizce bir Gemini görsel prompt'u üretir."""
    category = content.get("category", "ai")
    title = content.get("summary_text") or content.get("news_title", "")
    style = _STYLE_BY_CATEGORY.get(category, _STYLE_BY_CATEGORY["ai"])

    return (
        f'Create an original, stylized concept illustration representing this news theme: '
        f'"{title}". Style: {style}, cinematic lighting, 4:5 portrait orientation. '
        f'Do NOT include any real company logos, trademarks, or real people\'s faces — '
        f'this must be a generic, original artistic interpretation, not a real photo. '
        f'No text or watermarks in the image.'
    )
