"""
Merkezi Konfigürasyon Dosyası
Instagram AI & Gaming News Otomasyon Sistemi
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()


def _env(key: str, default: str = "") -> str:
    """
    os.getenv sarmalayıcısı: .env.example'dan kopyalanıp değeri hiç
    değiştirilmemiş "your_xxx_here" gibi placeholder'ları boş string
    olarak ele alır. Aksi halde bu değerler gerçek bir anahtarmış gibi
    API'lere gönderilip sürekli 401/403 hatası ve log gürültüsü üretir
    (bkz. NEWS_API_KEY/CURRENTS_API_KEY — .env.example'dan kopyalanıp
    doldurulmadan bırakıldığında yaşanan gerçek vaka).
    """
    value = os.getenv(key, default)
    if value.lower().startswith("your_"):
        return ""
    return value

# ============================================
# PROJE YOLLARI
# ============================================
BASE_DIR = Path(__file__).parent.resolve()
SRC_DIR = BASE_DIR / "src"
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "templates"

# Alt dizinler
FONTS_DIR = ASSETS_DIR / "fonts"
MUSIC_DIR = ASSETS_DIR / "music"
LOGOS_DIR = ASSETS_DIR / "logos"
POSTS_OUTPUT_DIR = OUTPUT_DIR / "posts"
STORIES_OUTPUT_DIR = OUTPUT_DIR / "stories"
REELS_OUTPUT_DIR = OUTPUT_DIR / "reels"

# Veritabanı
DB_PATH = DATA_DIR / "news.db"

# ============================================
# API ANAHTARLARI
# ============================================
INSTAGRAM_ACCESS_TOKEN = _env("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_USER_ID = _env("INSTAGRAM_USER_ID")
INSTAGRAM_APP_ID = _env("INSTAGRAM_APP_ID")
INSTAGRAM_APP_SECRET = _env("INSTAGRAM_APP_SECRET")

# Panel önizlemesinde gösterilen hesap adı. Sadece görseldir — Instagram'a
# gönderilmez, API çağrılarında kullanılmaz. Boşsa yer tutucu gösterilir.
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "").strip()

# ============================================
# BOT KİMLİĞİ (RSS kaynaklarına gönderilen)
# ============================================
# Bu ikisi User-Agent başlığını kurar. Marka adı koda gömülü DEĞİL çünkü
# depoyu kuran herkes kendi kimliğini göndermeli — başkasının alan adıyla
# tarama yapmak hem yanıltıcı hem de o alan adının itibarını riske atar.
#
# Neden kimlikli bir User-Agent: genel/kimliksiz bir bot dizesi (ya da
# tarayıcı taklidi) WAF/anti-bot sistemlerince şüpheli görülüp engellenmeye
# daha yatkın. +URL, site yöneticisinin bizi "saldırgan" değil meşru bir
# haber toplayıcı olarak tanıyıp iletişime geçebilmesini sağlar.
# Panel başlığında, mobil başlıkta ve dashboard'un HTTP `Server` başlığında
# görünen ad. Marka adı koda gömülü olmasın diye buradan geliyor.
BRAND_NAME = os.getenv("BRAND_NAME", "").strip() or "Instagram News Automation"

# ÜRETİLEN GÖRSELE/VİDEOYA BASILAN hesap etiketi.
#
# Logo dosyası varsa logo basılır; yoksa bunun metni basılır. Bu değer daha
# önce üç yerde sabit yazılıydı ve depoyu klonlayan herkes BAŞKASININ hesap
# adıyla içerik üretiyordu — yayınlanan her görsele giden bir etiket için
# ciddi bir hata.
#
# Hiçbiri ayarlı değilse boş kalır ve etiket hiç çizilmez: yanlış bir ad
# basmaktansa hiç basmamak doğru.
BRAND_HANDLE = (
    os.getenv("BRAND_HANDLE", "").strip()
    or (f"@{INSTAGRAM_USERNAME}" if INSTAGRAM_USERNAME else "")
)

BOT_NAME = os.getenv("BOT_NAME", "InstagramNewsAutomation").strip() or "InstagramNewsAutomation"
BOT_CONTACT_URL = (
    os.getenv("BOT_CONTACT_URL", "").strip()
    or "https://github.com/VreBey/instagram-news-automation"
)

NEWS_API_KEY = _env("NEWS_API_KEY")
CURRENTS_API_KEY = _env("CURRENTS_API_KEY")

GEMINI_API_KEY = _env("GEMINI_API_KEY")

# NVIDIA NIM (https://build.nvidia.com) — OpenAI-uyumlu, ücretsiz katalog.
#
# 10 Ağustos 2026: metin üretiminde Gemini'nin yerine denendi, GERİ ÇEKİLDİ
# — daha yavaş (1sn'e karşı 3-60sn) ve iki modelde uydurma/yabancı dil
# sızıntısı ölçüldü (bkz. proje hafızası). Burada SADECE embedding için
# kullanılıyor: (1) haber tekilleştirme, (2) görsel-metin alaka kontrolü.
# İkisi de Gemini ile YARIŞMIYOR, onu tamamlıyor — ayrı bir karar.
NVIDIA_API_KEY = _env("NVIDIA_API_KEY")
NVIDIA_EMBED_TEXT_MODEL = "nvidia/nv-embedqa-e5-v5"
NVIDIA_EMBED_VL_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"
NVIDIA_EMBED_TIMEOUT_SECONDS = int(os.getenv("NVIDIA_EMBED_TIMEOUT_SECONDS", "15"))

# SequenceMatcher'ın kaçırdığı paraphrase'leri yakalamak için ikinci basamak.
# Ölçüm (10 Ağustos 2026, 3 gerçek + kurgu örnek): aynı olayın farklı
# ifadeleri 0.71-0.84 arası, tamamen farklı haberler 0.38 skorluyordu. Eşik
# bilerek düşük tutuldu (ikisi arasında, alt uca yakın) — yanlışlıkla farklı
# bir haberi "aynı" sayıp içerik kaybetmek, kaçırılan bir dubleyi
# yakalayamamaktan daha pahalı.
DEDUP_EMBEDDING_SIMILARITY_THRESHOLD = float(
    os.getenv("DEDUP_EMBEDDING_SIMILARITY_THRESHOLD", "0.65"))

# Görsel gerçekten haberle alakalı mı — CLIP tarzı embedding kosinüs skoru.
# Ölçüm (10 Ağustos 2026, 3 doğru + 2 kasıtlı yanlış eşleşme, gerçek
# fetch_article_photo_from_page görselleri): doğru eşleşmeler 0.133-0.197,
# yanlış eşleşmeler 0.063-0.069. Örneklem küçük; eşik bilerek DÜŞÜK ve
# temkinli — amaç sadece bariz kötü eşleşmeleri elemek, sınırdaki gerçek
# görselleri reddetmemek (reddedilen görsel = kaybedilen içerik).
IMAGE_RELEVANCE_THRESHOLD = float(os.getenv("IMAGE_RELEVANCE_THRESHOLD", "0.10"))

MEDIA_HOST_URL = os.getenv("MEDIA_HOST_URL", "")
CLOUDINARY_CLOUD_NAME = _env("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = _env("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = _env("CLOUDINARY_API_SECRET")
CLOUDINARY_UPLOAD_FOLDER = os.getenv("CLOUDINARY_UPLOAD_FOLDER", "instagram_otomasyon")

# ============================================
# İÇERİK AYARLARI
# ============================================
CONTENT_LANGUAGE = os.getenv("CONTENT_LANGUAGE", "tr")

DAILY_POST_LIMIT = int(os.getenv("DAILY_POST_LIMIT", "2"))
DAILY_STORY_LIMIT = int(os.getenv("DAILY_STORY_LIMIT", "5"))
DAILY_REELS_LIMIT = int(os.getenv("DAILY_REELS_LIMIT", "1"))

# ============================================
# OTOMATİK YAYIN VE İÇERİK KALİTE AYARLARI
# ============================================
# relevance_score bu eşiğin üzerindeyse içerik otomatik zamanlanıp yayınlanır.
# Altındaki içerik taslak (draft) olarak kalır ve dashboard'da manuel incelemeyi bekler.
AUTO_PUBLISH_THRESHOLD = float(os.getenv("AUTO_PUBLISH_THRESHOLD", "0.75"))

# relevance_score bu eşiğin altındaysa AI/caption üretimi tamamen atlanır (quota tasarrufu).
MIN_PROCESSING_SCORE = float(os.getenv("MIN_PROCESSING_SCORE", "0.35"))

# Medya üretimi, günlük yayın limitinin kaç katı taslak üzerinden çalışsın.
# (En iyi skorlular auto-publish'e gidince manuel inceleme kuyruğunda da içerik kalsın diye.)
MEDIA_GENERATION_MULTIPLIER = int(os.getenv("MEDIA_GENERATION_MULTIPLIER", "3"))

# Haber tekilleştirme (aynı olayın farklı kaynaklarca tekrar toplanmasını önler)
DEDUP_TITLE_SIMILARITY_THRESHOLD = float(os.getenv("DEDUP_TITLE_SIMILARITY_THRESHOLD", "0.82"))
DEDUP_WINDOW_HOURS = int(os.getenv("DEDUP_WINDOW_HOURS", "72"))

# ============================================
# KATEGORİLER — GÖRÜNEN AD, EMOJİ, YEDEK HASHTAG'LER
# ============================================
# Bu projeyi KENDİ NİŞİNİZE uyarlarken ilk değiştireceğiniz yer burası.
#
# Daha önce bu metinler dört ayrı dosyaya gömülüydü: etiket hem
# image_generator'da hem story_generator'da (çoğaltılmış — birini
# değiştiren diğerini unutuyordu), hashtag'ler content_processor'da, CTA
# video_generator'da. Uyarlamak isteyen biri kod kazımak zorundaydı.
#
# Anahtarlar ("ai", "gaming") YAPISALDIR: RSS_FEEDS, NEWS_API_QUERIES ve
# alaka filtreleri aynı anahtarları kullanır. Yeni kategori ekliyorsanız
# oralara da eklemeniz gerekir; burada yalnızca GÖRÜNEN kısım tanımlı.
#
# Renkler COLORS["ai_color"] / COLORS["gaming_color"] altında.
#
# Hashtag'ler bilerek KISA (4 etiket): Instagram 2026'da 3-5 niş etiketi
# standart kabul ediyor, fazlası spam sinyali sayılıp erişimi düşürüyor.
# "#keşfet", "#takipet", "#viral" gibi etkileşim yemi etiketler koymayın —
# işe yaramıyor ve hesabı spam gibi gösteriyor. Bunlar yalnızca YEDEK;
# asıl etiketleri Gemini habere özel üretiyor.
CATEGORIES = {
    "ai": {
        "label": "YAPAY ZEKA",
        "emoji": "🤖",
        "caption_name": "AI",          # Reels caption'ında kısa ad
        "hashtags": ["#yapayZeka", "#AI", "#teknoloji", "#teknolojiHaber"],
    },
    "gaming": {
        "label": "OYUN DÜNYASI",
        "emoji": "🎮",
        "caption_name": "Gaming",      # Reels caption'ında kısa ad
        "hashtags": ["#oyun", "#gaming", "#oyunHaber", "#oyunDünyası"],
    },
}

# Reels çıkış slaytındaki çağrı metni (senaryoda outro yoksa bu basılır).
REELS_CTA_TEXT = os.getenv("REELS_CTA_TEXT", "Takip et, hiçbir haberi kaçırma!")

# Hikaye görselinin üst rozeti.
STORY_BADGE_TEXT = os.getenv("STORY_BADGE_TEXT", "📢 SON DAKİKA")

# ============================================
# HABER KAYNAKLARI - RSS FEED'LERİ
# ============================================
RSS_FEEDS = {
    "ai": [
        # Yapay Zeka Kaynakları
        {"name": "The Verge - AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en"},
        {"name": "TechCrunch - AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en"},
        {"name": "MIT Tech Review - AI", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "lang": "en"},
        {"name": "VentureBeat - AI", "url": "https://venturebeat.com/category/ai/feed/", "lang": "en"},
        {"name": "Ars Technica - AI", "url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "lang": "en"},
        {"name": "Wired - AI", "url": "https://www.wired.com/feed/tag/ai/latest/rss", "lang": "en"},
    ],
    "gaming": [
        # Oyun Dünyası Kaynakları
        {"name": "IGN", "url": "https://feeds.feedburner.com/ign/all", "lang": "en"},
        {"name": "Kotaku", "url": "https://kotaku.com/rss", "lang": "en"},
        {"name": "PC Gamer", "url": "https://www.pcgamer.com/rss/", "lang": "en"},
        {"name": "Eurogamer", "url": "https://www.eurogamer.net/feed", "lang": "en"},
        {"name": "GameSpot", "url": "https://www.gamespot.com/feeds/mashup/", "lang": "en"},
        {"name": "Rock Paper Shotgun", "url": "https://www.rockpapershotgun.com/feed", "lang": "en"},
        {"name": "Polygon", "url": "https://www.polygon.com/rss/index.xml", "lang": "en"},
        # Stüdyo/endüstri + donanım (oyun stüdyoları, 3D motorlar, PC parçaları)
        {"name": "GamesIndustry.biz", "url": "https://www.gamesindustry.biz/feed", "lang": "en"},
        {"name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all", "lang": "en"},
        {"name": "VG247", "url": "https://www.vg247.com/feed", "lang": "en"},
        # Kullanıcı geri bildirimi: MMORPG/FPS güncellemeleri ve mobil oyun
        # haberleri hiç görünmüyordu — genel oyun kaynakları bunları zaten
        # az kapsıyor. MMOs.com (MMORPG'ye özel) ve PocketGamer.biz (mobil
        # oyun endüstrisi) eklendi. NOT: pocketgamer.com (tüketici sitesi)
        # ve toucharcade.com kasıtlı olarak eklenmedi — ilki "ücretsiz kod/
        # spin" tarzı düşük kaliteli içerikle dolu, ikincisi ise sitenin
        # kendisi kapanış sürecinde ve haber akışı durmuş durumda.
        {"name": "MMOs.com", "url": "https://mmos.com/feed", "lang": "en"},
        {"name": "PocketGamer.biz", "url": "https://www.pocketgamer.biz/index.rss", "lang": "en"},
        # Bu ikisi ÖNCE yalnızca harici araştırma yoluyla geliyordu ve o yol
        # görsel taşımadığı için gönderileri stok fotoğrafa/gradyana
        # düşüyordu. Makale sayfasını kazımak da işe yaramıyordu: WAF 403
        # veriyor. Oysa ikisi de kendi RSS beslemesini yayınlıyor ve
        # robots.txt makine erişimine açıkça izin veriyor
        # (mmorpg.com "Allow: /", massivelyop yalnızca /uploads/assets/'i
        # kapatıyor — görseller orada değil). Yani doğru çözüm 403'ü aşmak
        # değil, sitenin kendi sunduğu kanalı kullanmaktı.
        # Görselleri standart RSS alanlarında değil içerik HTML'inde
        # taşıyorlar; _extract_image bunun için <img> geri dönüşü içeriyor.
        {"name": "MMORPG.com", "url": "https://www.mmorpg.com/rss", "lang": "en"},
        {"name": "MassivelyOP", "url": "https://massivelyop.com/feed", "lang": "en"},
    ],
}

# "gaming" kategorisi alaka filtresi — RSS kaynakları (IGN, Kotaku, Polygon
# vb.) ve NewsAPI'nin geniş OR sorguları oyun dışı içerik de getiriyor
# (spor maçı sonuçları, TV/dizi/anime haberleri, reality show'lar). Bu
# kaynaklardan toplanan bir haber, aşağıdaki anahtar kelimelerden EN AZ
# birini başlık/açıklamasında içermiyorsa oyunla alakasız kabul edilip
# toplama aşamasında elenir — bkz. news_collector.py:_is_gaming_relevant.
GAMING_RELEVANCE_KEYWORDS = [
    # NOT: Düz "game"/"games" kelimesi kasıtlı olarak eklenmedi — "Today's
    # top games to watch: Braves vs Nationals" gibi spor bahis haberleri de
    # "games" geçiriyor (bkz. CBS Sports sızıntısı). Bunun yerine spesifik
    # ama daha geniş bir platform/franchise listesi tutuluyor; nadir bir
    # gerçek oyun haberinin (ör. çok genel "demand for games" başlığı)
    # kaçması, spor sızıntısını geri açmaktan daha tercih edilir.
    # Platformlar — NOT: düz "switch" kasıtlı olarak yok, "switching
    # contexts" gibi tamamen alakasız metinlerde bile eşleşiyordu (bkz.
    # "Best Mac Launcher Apps" sızıntısı). "nintendo switch"/"switch 2"
    # gerçek başlıklarda zaten hemen hemen her zaman bu şekilde geçiyor.
    "playstation", "ps4", "ps5", "ps6", "xbox", "nintendo",
    "nintendo switch", "switch 2", "switch console",
    "steam deck", "steam", "epic games", "meta quest", "vr headset", "oculus",
    # Endüstri / geliştirme
    "video game", "videogame", "game studio", "game developer",
    "game development", "game engine", "unreal engine", "unity engine",
    "indie game", "game awards", "gamescom", "pax ", "game convention",
    "gaming", "esports", "e-sports", "oyun ",
    # Donanım (oyun bağlamında)
    "gpu", "graphics card", "geforce", "rtx ", "radeon", "nvidia",
    "gaming laptop", "gaming pc", "gaming monitor", "gaming chair",
    # Bilinen büyük oyun/franchise adları
    "fortnite", "minecraft", "call of duty", "grand theft auto", "gta ",
    "zelda", "mario", "pokemon", "pokémon", "league of legends", "valorant",
    "overwatch", "elden ring", "diablo", "world of warcraft",
    "counter-strike", "apex legends", "roblox", "ea sports", "madden",
    "genshin impact", "final fantasy", "resident evil", "assassin's creed",
    "cyberpunk 2077", "starfield", "baldur's gate", "hogwarts legacy",
    "helldivers", "mortal kombat", "street fighter", "halo infinite",
    # Platform/stüdyo/yayıncı adları — bunlar olmadan "Bulletstorm",
    # "Palworld", "Ubisoft'un NFT oyunu kapanıyor" gibi spesifik oyun
    # başlığı içermeyen ama gerçekten oyunla ilgili gazetecilik (ör. "What
    # are we all playing this weekend?" tarzı köşe yazıları hariç, onlar
    # için güvenli bir anahtar kelime yok) kaçırılıyordu.
    "twitch", "ubisoft", "early access", "blizzard entertainment",
    "activision", "capcom", "sega", "bethesda", "square enix",
    "riot games", "rockstar games", "naughty dog", "bandai namco",
    # Tür (genre) terimleri — kullanıcı geri bildirimi: "yeni çıkacak bir
    # oyun", "popüler bir MMORPG/FPS oyununa gelen güncelleme", "yeni
    # çıkacak rol yapma/simülasyon oyunları" gibi içerikler hiç
    # görünmüyordu. Eskiden filtre sadece SABİT bir franchise listesine
    # dayanıyordu — listede olmayan (ör. yeni duyurulan) bir MMORPG/FPS/
    # simülasyon oyunu, tür adı içermesine rağmen elenip gidiyordu.
    # NOT: Yalın "rpg" ve "fallout" kasıtlı olarak eklenmedi — "RPG" askeri
    # bağlamda "rocket-propelled grenade" kısaltması, "fallout" ise gündelik
    # İngilizce'de "sonuç/yansıma" anlamında çok sık kullanılan bir kelime
    # (ör. "political fallout"); ikisi de "switch" kelimesindeki gibi yanlış
    # eşleşme riski taşıyor. Yerine spesifik, yanlış anlaşılmaya kapalı
    # varyantlar kullanılıyor.
    "mmorpg", "mmo", "massively multiplayer",
    "fps game", "first-person shooter", "shooter game", "battle royale",
    "looter shooter", "role-playing game", "jrpg", "action rpg",
    "simulation game", "life sim", "farming sim", "city builder",
    "survival game", "open-world game", "sandbox game",
    "mobile game", "mobile gaming", "ios game", "android game",
    "upcoming game", "game release date", "game trailer", "game demo",
    # MMORPG/FPS/simülasyon türlerinde bilinen büyük oyun adları
    "final fantasy xiv", "elder scrolls online", "guild wars 2",
    "lost ark", "new world", "black desert", "runescape",
    "path of exile", "destiny 2", "albion online", "elder scrolls",
    "skyrim", "fallout 4", "fallout 76", "fallout new vegas",
    "battlefield", "rainbow six", "pubg", "escape from tarkov",
    "warzone", "titanfall", "team fortress", "sea of thieves",
    "the sims", "stardew valley", "animal crossing", "farming simulator",
    "cities: skylines", "planet zoo", "planet coaster", "two point",
    "house flipper", "powerwash simulator", "euro truck simulator",
    "dark souls", "monster hunter", "ark: survival", "palworld",
    "rocket league", "fall guys", "dead by daylight",
    # Popüler mobil oyunlar
    "clash of clans", "clash royale", "candy crush", "honkai",
    "pubg mobile", "call of duty mobile", "brawl stars",
    "subway surfers", "pokemon go", "monopoly go", "coin master",
]

# "ai" kategorisi alaka filtresi — NewsAPI/Currents'ın "artificial
# intelligence"/"AI breakthrough" gibi sorguları bazen tamamen alakasız
# sonuçlar da getiriyor (ör. futbol transfer haberleri "ai" etiketiyle
# gelmiş — bkz. Onefootball.com kaynağı). Başlık/açıklamada aşağıdaki
# anahtar kelimelerden biri (ya da "ai" kelimesi tek başına, kelime
# sınırıyla) yoksa alakasız kabul edilir — bkz. news_collector.py:
# _is_ai_relevant.
AI_RELEVANCE_KEYWORDS = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "chatbot", "large language model", "generative ai",
    "openai", "chatgpt", "gemini", "claude", "anthropic", "deepmind",
    "meta ai", "copilot", "nvidia", "xai", "grok", "midjourney",
    "stable diffusion", "hugging face", "llama", "mistral", "perplexity",
    "deepseek", "data center", "semiconductor", " gpu", " tpu",
    "robot", "robotics", "automation", "algorithm", "computer vision",
    "natural language processing", "yapay zeka", "makine öğrenmesi",
    "llm", "genai",
    # AI-özel güvenlik terimleri — Ars Technica gibi kaynaklar genel siber
    # güvenlik haberlerini de "AI" RSS etiketiyle veriyor; bunların çoğu
    # gerçekten AI'yla ilgisiz (ör. "Windows 0-day") ama "prompt injection"
    # gibi terimler özellikle LLM güvenliğiyle ilgili, kaçırılmamalı.
    "prompt injection", "jailbreak", "adversarial attack", "model poisoning",
]

# PR bülteni / reklam-affiliate içeriği filtresi — NewsAPI/Currents'ın geniş
# taraması gerçek haberlerin yanına basın bülteni dağıtım servislerinden
# (GlobeNewswire, PR Newswire vb.) şirket duyurularını ve doğrudan reklam/
# affiliate metinlerini ("sadece $X'a ömür boyu erişim" gibi) de getiriyor.
# Bunlar habermiş gibi işlenip Türkçeye çevrilip paylaşılırsa hem marka
# güvenilirliğini zedeler hem de Türkiye'deki reklam/ticari ileti
# mevzuatı (örtülü reklam yasağı) açısından risk oluşturur — bu yüzden
# toplama aşamasında elenir (bkz. news_collector.py:_is_pr_or_ad_content).
PR_WIRE_SOURCES = [
    # "Globe Newswire" boşluklu/boşluksuz karışık kullanıldığı için ikisi de
    # eksikti (bkz. daha önce kaçırılan "Relief AI Inc." duyurusu) — geri
    # kalanlar için de aynı riski önceden kapatmak adına her ikisi eklendi.
    "globenewswire", "globe newswire", "pr newswire", "prnewswire",
    "business wire", "businesswire", "prweb", "accesswire", "access wire",
    "einpresswire", "ein presswire", "newsfilecorp", "newsfile corp",
    "newsfile",
]
# PR bülteni servisleri sık sık BAŞKA sitelerce (ör. "Financial Post") aynen
# alıntılanıyor — bu durumda source_name artık wire servisi değil, alıntılayan
# site oluyor ama açıklama metninin içinde hâlâ orijinal dateline kalıyor:
# bazen "(GLOBE NEWSWIRE)" gibi parantezli, bazen "/PRNewswire/" gibi eğik
# çizgili (bkz. "Relief AI Inc." hisse senedi duyurusu ve "AccuFACE 2" basın
# bülteni — ikisi de "Financial Post" gibi alıntılayan bir kaynak üzerinden
# gelmiş). Bu yüzden PR_WIRE_SOURCES artık noktalama işaretine bakılmaksızın
# açıklama metninde de aranıyor — bkz. news_collector.py:_is_pr_or_ad_content.
AD_CONTENT_KEYWORDS = [
    "% off", "% indirim", "only $", "sadece $", "lifetime access",
    "ömür boyu erişim", "limited time offer", "sınırlı süre", "promo code",
    "discount code", "coupon code", "buy now", "shop now", "satın al",
    "affiliate", "sponsored post", "use code",
]

# GÜVENİLMEZ/HİCİV KAYNAKLARI — bugüne kadarki filtreler hep KONU alakasına
# bakıyordu (gaming/ai/reklam mı), kaynağın GÜVENİLİRLİĞİNE değil. Gerçek bir
# olay olarak bulundu: Thedailymash.co.uk'nin (İngiliz hiciv sitesi, The
# Onion benzeri) Elon Musk hakkında uydurma bir "haberi" konu filtresini
# geçip Gemini tarafından işlendi ve AI, hiciv olduğunu anlamayıp "The
# Economist ile röportaj" gibi HİÇ VAR OLMAYAN detaylar uydurarak gerçek bir
# haber gibi Türkçe caption ürettı (şans eseri sonradan başka bir nedenle
# reddedildi, ama konu filtresine takılmadı). Bu, "yanlış haber paylaşma"
# riskinin somut örneği. Kaynak adı bu listedeyse kategori/PR kontrolünden
# BAĞIMSIZ olarak, konusu ne olursa olsun toplama aşamasında elenir.
# Güvenilir ama KONU DIŞI kaynaklar. UNRELIABLE_SOURCES'tan ayrı tutuluyor
# çünkü mesele güvenilirlik değil alaka: Pypi gerçek bir kaynak, sadece
# yayınladığı şey haber değil.
#
# Canlı ölçüm (son 7 gün): Pypi.org 29, The Times of India 29 haber üretmiş —
# IGN (36) ve PC Gamer (33) ile aynı seviyede. Pypi paket sürüm listeleri
# ("calibre 0.8.0", "mlflow-skinny 3.15.0") AI haberi sanılıp işleniyor,
# boşuna Gemini kotası harcıyor ve onay kuyruğunu kirletiyordu.
OFF_TOPIC_SOURCES = [
    "pypi.org", "pypi", "npmjs.com", "rubygems.org", "packagist.org",
    "crates.io", "hex.pm", "nuget.org", "sourceforge.net",
    "the times of india", "timesofindia", "hindustan times", "ndtv",
    "dailyhunt", "jagran", "amarujala",
]

UNRELIABLE_SOURCES = [
    # Hiciv/parodi siteleri (gerçek haber gibi görünen uydurma içerik)
    "thedailymash.co.uk", "the daily mash", "theonion.com", "the onion",
    "babylonbee.com", "babylon bee", "clickhole.com", "reductress.com",
    "waterfordwhispersnews.com", "newsthump.com", "thebetootaadvocate.com",
    "el deforma", "worldnewsdailyreport.com", "empirenews.net",
    "nationalreport.net", "realrawnews.com",
    # Yanlış bilgi/komplo teorisi ile bilinen siteler
    "naturalnews.com", "infowars.com", "beforeitsnews.com",
    "thefreedomfrequency.org", "worldtruth.tv", "newspunch.com",
    "yournewswire.com",
    # Korsan/warez dağıtım siteleri. Cgpersia "Udemy – Unreal Engine 5"
    # kurslarının kırılmış sürümlerini paylaşıyor; bu içeriği haber diye
    # yeniden yayınlamak hem telif ihlaline aracılık hem de marka riski.
    "cgpersia.com", "cgpeers.com", "gfxdomain.net", "nulled.to",
    "rutracker.org", "1337x", "fitgirl-repacks",
    # İçerik kazıyıcı / otomatik toplayıcılar: başka sitelerin metnini
    # yeniden yayınlıyorlar, özgün gazetecilik yok ve neredeyse hiçbirinde
    # kullanılabilir bir görsel bulunmuyor.
    "biztoc.com", "newsbreak.com", "headtopics.com", "newsbomb.gr",
    # Bahis/fantezi spor gelir siteleri — "gaming" kelimesini kumar
    # anlamında kullanıp oyun filtresini atlatıyorlar.
    "mmapayout.com", "legalsportsreport.com", "coveringthecage.com",
]

# Kumar/casino içeriği "gaming" kelimesinin endüstri anlamını (iGaming =
# çevrimiçi kumar) kullanarak GAMING_RELEVANCE_KEYWORDS filtresini atlatıyor
# (bkz. "Lightning Link... BetMGM Casino", "World Cup Gaming Surges" gibi PR
# bültenleri — ikisi de "gaming" geçiriyor ama video oyunuyla alakası yok).
# ÖNEMLİ — Türkiye'de yasadışı bahis/kumar reklamı sosyal medyada paylaşmak
# 7258 sayılı Kanun kapsamında 1-3 yıl HAPİS cezası gerektiriyor (BTK/MASAK
# tarafından hesaba erişim engeli de uygulanabiliyor) — bu yalnızca bir
# "marka güvenilirliği" meselesi değil, gerçek bir cezai risk. Bu yüzden bu
# terimlerden biri varsa "gaming" eşleşmesi olsa bile alakasız sayılır.
# Oyun uyarlamaları (film/dizi/anime) OYUN DÜNYASININ PARÇASIDIR —
# kullanıcı isteği. Aşağıdaki sinyallerden biri varsa içerik, bir film/dizi
# derlemesi kalıbına uysa bile oyun haberi sayılır ve elenmez.
GAME_ADAPTATION_KEYWORDS = [
    "game adaptation", "video game adaptation", "game-to-film",
    "based on the game", "based on the video game", "based on the hit game",
    "game movie", "videogame movie", "game anime", "anime adaptation",
    "live-action adaptation", "live action adaptation",
    "oyun uyarlaması", "oyun uyarlamasi", "oyundan uyarlama",
]

# Ekranda uyarlaması olan (ya da duyurulmuş) oyun serileri. Bu adların
# TEK BAŞINA geçmesi yeterli DEĞİLDİR — aşağıdaki SCREEN_MEDIA_CONTEXT_WORDS
# listesinden bir kelimeyle BİRLİKTE geçmeleri gerekir. Sebep: "fallout",
# "arcane", "halo", "persona" gibi adlar günlük İngilizcede de kullanılıyor
# ("political fallout from the merger", "halo effect"). İki sinyal şartı,
# "Fallout Season 3 renewed" gibi kısa uyarlama başlıklarını yakalarken
# alakasız haberleri dışarıda bırakır.
GAME_ADAPTATION_FRANCHISES = [
    "the last of us", "fallout", "arcane", "castlevania", "cyberpunk edgerunners",
    "silent hill", "resident evil", "tomb raider", "uncharted", "halo",
    "the witcher", "sonic the hedgehog", "super mario", "minecraft",
    "five nights at freddy", "borderlands", "gran turismo", "mortal kombat",
    "street fighter", "tekken", "god of war", "horizon zero dawn", "bioshock",
    "mass effect", "assassin's creed", "assassins creed", "devil may cry",
    "dragon age", "death stranding", "detective pikachu", "pokemon",
    "metal gear", "monster hunter", "ghost of tsushima", "helldivers",
    "angry birds", "warcraft", "diablo", "final fantasy", "persona",
    "nier", "dead space", "animal crossing", "splinter cell", "twisted metal",
    "knuckles", "like a dragon", "yakuza", "elden ring", "zelda",
]

# Bir içeriğin film/dizi/anime bağlamında olduğunu gösteren kelimeler.
SCREEN_MEDIA_CONTEXT_WORDS = [
    "series", "season", "episode", "movie", "film", "anime", "tv show",
    "live-action", "live action", "adaptation", "trailer", "casting",
    "cast as", "netflix", "hbo", "prime video", "disney+", "showrunner",
    "premiere", "dizi", "sezon", "film uyarlaması", "animesi",
]

# Film/dizi "ne izlesem" derlemeleri. Gerçek vaka: Screen Rant'ten
# "3 Best Movies To Watch On Prime Video This Weekend" başlıklı bir film
# derlemesi, açıklamasında "a video game adventure adaptation" geçtiği için
# "video game" anahtar kelimesine takılıp OYUN haberi sayıldı ve hakkında
# gönderi üretildi.
#
# Liste kasıtlı olarak YALNIZCA "tüketim rehberi" kalıplarını içerir; düz
# "tv series"/"prime video" gibi ifadeler BİLEREK yok, çünkü onlar
# "Fallout dizisi 2. sezon onayı" gibi meşru oyun-uyarlaması haberlerini de
# elerdi. Ayrıca yalnızca BAŞLIĞA bakılır ve GAME_ADAPTATION_KEYWORDS
# eşleşirse hiç uygulanmaz (bkz. news_collector._is_gaming_relevant).
ENTERTAINMENT_ROUNDUP_TITLE_KEYWORDS = [
    "movies to watch", "shows to watch", "series to watch",
    "best movies", "new movies this", "movies on prime",
    "to stream this", "streaming this weekend", "what to watch",
    "box office", "episode recap", "recap:",
]

GAMBLING_BLOCKLIST_KEYWORDS = [
    "casino", "igaming", "i-gaming", "betmgm", "bet365", "sportsbook",
    "wagering", "slot machine", "slot brand", "blackjack", "roulette",
    "jackpot", "sports betting", "online betting", "poker tournament",
    "bookmaker", "betting site", "betting app", "online gambling",
    "gambling site", "bahis sitesi", "kaçak bahis", "canlı bahis",
    "bahis şirketi", "bettilt", "bahsegel", "casibom",
]

# 2026'da yürürlüğe giren Ticaret Bakanlığı reklam yönetmeliği, "kâr garantisi"
# içeren ya da sermaye piyasası aracı niteliğindeki kripto/finansal reklamları
# ve lisanssız yatırım tavsiyesini açıkça yasaklıyor (6362 sayılı Kanun/SPK).
# "Motley Fool", "MarketBeat" gibi ABD borsa-tavsiyesi siteleri "AI" arama
# sorgularımıza "Is There a Better Way To Play The AI Boom?" tarzı hisse
# senedi/opsiyon tavsiyesi içerikleri sızdırıyor — bunlar gerçek AI haberi
# değil, lisanssız yatırım tavsiyesi sayılabilecek içerik; Türkçeye çevirip
# paylaşmak hem SPK hem reklam mevzuatı açısından risk oluşturuyor.
FINANCIAL_ADVICE_SOURCES = [
    "motley fool", "marketbeat", "zacks", "seeking alpha", "benzinga",
    "investorplace", "tipranks", "simply wall st", "insider monkey",
    "investing.com",
]
# İlk listeden sonra hâlâ sızan gerçek örnekler bulundu: "Investing.com"
# kaynaklı borsa analizleri, ünlü bir yatırımcının hisse hamlesini haber
# yapan içerikler ("Cathie Wood bought Nvidia..."), Yahoo/Bloomberg tarzı
# genel borsa özetleri ("Stock market today: Dow, S&P 500, Nasdaq..."). Bu
# hesap AI/Gaming haberleri için, borsa endeks özeti/yatırımcı hareketi de
# konu dışı olduğundan (sadece hukuki risk değil, alaka sorunu da) eklendi.
FINANCIAL_ADVICE_KEYWORDS = [
    "call options", "put options", "price target", "buy rating", "sell rating",
    "should you buy", "is it a buy", "stock pick", "better way to play",
    "guaranteed profit", "guaranteed return", "garantili kazanç",
    "garantili getiri", "yatırım tavsiyesi",
    "stock market today", "s&p 500", "nasdaq", "dow jones",
    "high-conviction investor", "catch-all trade for stocks",
]

# News API Sorguları
NEWS_API_QUERIES = {
    "ai": [
        "artificial intelligence",
        "machine learning",
        "ChatGPT OR Gemini OR Claude",
        "AI breakthrough",
        "yapay zeka",
        "new AI model",
        "AI chip OR AI hardware",
    ],
    "gaming": [
        "video game release",
        "gaming industry",
        "PlayStation OR Xbox OR Nintendo",
        "esports",
        "oyun dünyası",
        "game studio OR game developer",
        "Unreal Engine OR Unity OR game engine",
        "Steam OR Epic Games",
        "graphics card OR GPU OR processor launch",
        "game development tools",
        "gaming event OR game convention OR game awards",
        "new game announcement",
        # Kullanıcı geri bildirimi: MMORPG/FPS güncellemeleri, mobil oyunlar
        # ve simülasyon/rol yapma oyunları hiç görünmüyordu — eski sorgu
        # listesi tür (genre) bazlı hiçbir arama içermiyordu.
        "MMORPG OR MMO game update",
        "FPS game update OR shooter game",
        "mobile game OR mobile gaming",
        "simulation game OR life sim game",
        "role-playing game OR JRPG",
        "upcoming game 2026 OR game release date",
    ],
}

# ============================================
# GÖRSEL AYARLARI
# ============================================

# Feed Gönderi Boyutları
POST_SIZE_SQUARE = (1080, 1080)       # Kare gönderi
POST_SIZE_PORTRAIT = (1080, 1350)     # Dikey gönderi (4:5)
POST_SIZE_LANDSCAPE = (1080, 608)     # Yatay gönderi (1.91:1)

# Hikaye ve Reels Boyutu
STORY_SIZE = (1080, 1920)             # 9:16 dikey
REELS_SIZE = (1080, 1920)             # 9:16 dikey

# Renk Paleti (Marka Renkleri)
COLORS = {
    # Ana renkler
    "primary": "#6C5CE7",          # Mor (Ana tema rengi)
    "secondary": "#00D2D3",        # Camgöbeği
    "accent": "#FF6B6B",           # Mercan kırmızı
    "accent_2": "#FECA57",         # Altın sarı

    # Arka plan renkleri
    "bg_dark": "#0D1117",          # Koyu arka plan
    "bg_card": "#161B22",          # Kart arka planı
    "bg_gradient_start": "#1A1A2E",  # Gradient başlangıç
    "bg_gradient_end": "#16213E",    # Gradient bitiş

    # Metin renkleri
    "text_primary": "#FFFFFF",     # Beyaz metin
    "text_secondary": "#8B949E",   # Gri metin
    "text_accent": "#58A6FF",      # Mavi vurgu metin

    # Kategori renkleri
    "ai_color": "#6C5CE7",         # AI haberleri için mor
    "gaming_color": "#00D2D3",     # Gaming haberleri için camgöbeği

    # Gradient tanımları
    "gradient_ai": ["#6C5CE7", "#A29BFE"],       # AI gradient
    "gradient_gaming": ["#00D2D3", "#55E6C1"],    # Gaming gradient
    "gradient_mixed": ["#6C5CE7", "#00D2D3"],     # Karışık gradient
}

# Logo / Marka Varlıkları
LOGO_HORIZONTAL_PATH = LOGOS_DIR / "logo_horizontal.png"   # Üst/alt bar için (şeffaf)
LOGO_MARK_PATH = LOGOS_DIR / "logo_mark.png"               # Reels/story için küçük rozet

# Feed gönderisi görsel şablon çeşitleri (döngüsel/deterministik seçim)
POST_LAYOUT_VARIANTS = ["gradient_classic", "split_panel", "card_frame"]

# Stok fotoğraf zenginleştirme (Pexels) — anahtar yoksa mesh-gradient'e düşülür
PEXELS_API_KEY = _env("PEXELS_API_KEY")
STOCK_PHOTO_CACHE_DIR = ASSETS_DIR / "stock_cache"

# Haberin kendi kaynağındaki gerçek görseli (RSS/NewsAPI/Currents'tan zaten
# toplanan news_items.image_url) — Pexels'ten önce denenir, konuya özel
# olduğu için. Bulunamaz/uygun değilse Pexels'e, o da yoksa mesh'e düşülür.
ARTICLE_PHOTO_CACHE_DIR = ASSETS_DIR / "article_cache"
# Kabul eşiği. Eskiden tek bir kural vardı: "her iki kenar da ≥ 400".
# Bu fazla katıydı — 4 Ağustos 2026 canlı ölçümünde bu kurala takılan
# görsellerin TAMAMI gerçek haber fotoğrafıydı (690x388, 660x330, 600x315,
# 550x315), elenmesi istenen logo/ikon değil. Haber fotoğrafları geniş ve
# kısa olur; logolar ise küçük ve kareye yakındır. Bu yüzden kural iki
# eşiğe ayrıldı: uzun kenar bir arka planı taşıyacak kadar büyük olmalı,
# kısa kenar ise şerit/ikon olmadığını gösterecek kadar.
ARTICLE_PHOTO_MIN_LONG_SIDE = 500
ARTICLE_PHOTO_MIN_SHORT_SIDE = 250
# Oyun kapağı dikey ve tam çözünürlükte geliyor (600x900), orada iki kenar
# için de tek eşik anlamlı.
GAME_COVER_MIN_DIMENSION = 400

# Telegram "🎨 Farklı Görsel İste" ile kullanıcının Gemini'den ürettiği ve
# geri gönderdiği görsellerin kaydedildiği dizin.
MANUAL_IMAGE_CACHE_DIR = ASSETS_DIR / "manual_cache"

# Gaming kategorisi için: haberin kendi görseli (image_url) yoksa/reddedilmişse,
# jenerik Pexels stok fotoğrafına ("gaming keyboard neon" vb.) düşmeden önce,
# haber başlığından tahmin edilen OYUNUN GERÇEK kapak/ekran görüntüsü denenir —
# kullanıcı geri bildirimi: "oyunlarda hep aynı görsel kullanılıyor, gerçek
# ve bağlantılı görseller kullanılmalı" (bkz. src/game_cover_art.py). Önce
# Steam (anahtarsız) denenir — sadece PC/Steam'de satılan oyunları kapsar.
# RAWG_API_KEY ayarlanmışsa, Steam'de bulunamayan (konsol-exclusive, henüz
# çıkmamış vb.) oyunlar için RAWG.io veritabanı ikinci kademe olarak denenir.
# Anahtar yoksa bu ikinci kademe sessizce atlanır (Pexels'in mevcut opsiyonel
# davranışıyla aynı desen) — ücretsiz anahtar: https://rawg.io/apidocs
GAME_COVER_CACHE_DIR = ASSETS_DIR / "game_cover_cache"
RAWG_API_KEY = _env("RAWG_API_KEY")

# Font Ayarları
FONTS = {
    "title": "Inter-Bold",
    "subtitle": "Inter-SemiBold",
    "body": "Inter-Regular",
    "accent": "Inter-ExtraBold",
    # Fallback fontlar (sistemde yüklü olanlar)
    "fallback": ["Arial", "Helvetica", "DejaVu Sans"],
}

FONT_SIZES = {
    "headline": 72,
    # Manşet artık 5-8 kelimeyle sınırlı (bkz. PROMPTS["summarize_news"]),
    # bu yüzden başlangıç puntosu büyütüldü: akıştaki küçük önizlemede
    # okunabilirlik için sektör standardı 60-90px aralığı. Metin yine de
    # uzun gelirse otomatik küçültme devrede kalıyor.
    "title": 84,
    "subtitle": 40,
    "body": 32,
    "caption": 24,
    "small": 18,
    "hashtag": 22,
}

# ============================================
# VİDEO AYARLARI (REELS)
# ============================================
REELS_CONFIG = {
    "fps": 30,
    # Her haber kartı için saniye. Video SESSİZ (REELS_SILENT) — yani bu süre
    # izleyicinin metni OKUMASI için ayrılan süre, dinlemesi için değil.
    # 4 sn'de okunabilen ~9 kelime bir haber cümlesi için fazla dardı
    # (isim + tarih + rakam sığmıyordu), 5 sn ~11 kelimeye çıkarıyor.
    # 7 slaytta toplam ~35 sn — max_duration'ın hayli altında.
    "duration_per_slide": 5,
    "transition_duration": 0.5,      # Geçiş efekti süresi
    "max_duration": 60,              # Maksimum Reels süresi (saniye)
    "min_duration": 15,              # Minimum Reels süresi (saniye)
    "codec": "libx264",
    "audio_codec": "aac",
    "bitrate": "8000k",
    "tts_voice": "tr-TR-AhmetNeural",   # Edge-TTS Türkçe ses
    "tts_voice_en": "en-US-GuyNeural",  # Edge-TTS İngilizce ses
    "bg_music_volume": 0.15,         # Arka plan müzik ses seviyesi
}

# Bir reels segmenti ekranda kaç KELİME olabilir.
#
# Sessiz okuma hızı ~200 kelime/dk (3,3 kelime/sn). İzleyici aynı anda
# görsele de baktığı için %70 verimle hesaplandı. Sabit yazılmıyor, slayt
# süresinden TÜRETİLİYOR: süre değişirse bütçe kendiliğinden değişsin,
# prompt ile gerçek arasında sessiz bir tutarsızlık oluşmasın.
REELS_SEGMENT_MAX_KELIME = max(
    6, int(REELS_CONFIG["duration_per_slide"] * 3.3 * 0.7)
)

# Reels videoları SESSİZ üretilir (TTS anlatım yok, arka plan müziği yok).
#
# Sebep: müzik Instagram uygulamasında, yayınlama sırasında ekleniyor.
# Instagram'ın lisanslı müzik kütüphanesi Graph API'den erişilebilir DEĞİL —
# Meta'nın referansında reels için tek ses parametresi `audio_name` ve o da
# yalnızca videodaki MEVCUT sesi yeniden adlandırıyor. Yani "otomatik yayın"
# ile "Instagram müziği" birbirini dışlıyor; bu kurulumda müzik tercih edildi.
#
# Videoda ses olsaydı uygulamada müzik eklerken altta kalıp çakışırdı.
#
# TTS ayrıca bilinçli olarak kapalı: Türkçe için ticari kullanıma açık,
# CPU'da çalışan ve edge-tts'ten (Azure neural) daha doğal duyan bir açık
# kaynak seçenek 2026 itibarıyla yok — araştırıldı. Ekran yazısı + müzik,
# reels'in zaten baskın formatı.
REELS_SILENT = os.getenv("REELS_SILENT", "true").lower() in ("1", "true", "yes")

# ============================================
# ZAMANLAMA AYARLARI
# ============================================
SCHEDULE = {
    "collect_news": "08:30",         # Haberleri topla
    # Telegram onay bildirimi (ya da auto modda otomatik zamanlama) günde
    # TEK sefer yerine sabah/öğle/akşam olmak üzere 3 kez kontrol edilir —
    # kullanıcı isteği. Böylece medyası ilk turda hazır olmayan ya da 20'lik
    # gönderim limitine takılan içerikler de aynı gün içinde, fazla
    # beklemeden Telegram'a ulaşır.
    "notify_morning": "09:45",       # Sabah onay bildirimi (haber toplamadan 75dk sonra)
    "notify_noon": "13:00",          # Öğle onay bildirimi
    "notify_evening": "19:00",       # Akşam onay bildirimi
    "morning_post": "10:00",        # Sabah feed gönderisi
    "noon_stories": "12:30",        # Öğle hikaye serisi
    "afternoon_reels": "15:00",     # Öğleden sonra Reels
    "evening_stories": "18:00",     # Akşam hikaye güncellemesi
    "night_post": "21:00",          # Gece feed gönderisi
}

# Auto-schedule sırasında her içerik türü hangi SCHEDULE zaman dilimleri
# arasında round-robin dağıtılsın.
# Telegram onay bildirimleri arasındaki bekleme. Telegram bir botun AYNI
# sohbete gönderimini kabaca dakikada 20 mesajla sınırlıyor; 20 içerik
# arka arkaya gönderildiğinde bu limitin tam sınırına geliniyordu.
TELEGRAM_NOTIFY_DELAY_SECONDS = 2.5

# Telegram onay kuyruğuna alınacak içeriğin azami yaşı. Gerçek kullanıcı
# şikayeti: "eski içeriklerden gönderdi". Medyası üretilmiş ama kotalar
# dolduğu için bildirilememiş taslaklar kuyrukta birikip günler sonra
# haber değeri kalmamışken gönderiliyordu. Bu yaştan eski haberler artık
# hiç bildirilmez (taslak olarak kalır, dashboard'dan hâlâ görülebilir).
TELEGRAM_NOTIFY_MAX_AGE_DAYS = int(os.getenv("TELEGRAM_NOTIFY_MAX_AGE_DAYS", "3"))

# Onay bekleyen ama cevapsız kalmış içerikler için hatırlatma eşiği.
#
# Gerçek olay (13 Ağustos 2026): 10-12 Ağustos'ta gönderilen 26 onay
# isteği Telegram akışında fark edilmedi. Kullanıcı "hiç bildirim
# gelmiyor" dedi ama mesajlar aslında başarıyla gönderilmişti — sadece
# kimse bir daha hatırlatmıyordu. notify_pending_approvals SADECE hiç
# gönderilmemiş (telegram_message_id IS NULL) içerikleri bulur; zaten
# gönderilip cevapsız kalanlar sessizce kuyrukta birikti ve üretim freni
# (backlog_is_full) günlerce devrede kaldı. MAX_ITEMS düşük tutuluyor:
# amaç kullanıcıyı sel gibi mesajla boğmak değil, unutulmuş olabilecek
# birkaç öğeyi öne çıkarmak.
TELEGRAM_REMINDER_AFTER_HOURS = int(os.getenv("TELEGRAM_REMINDER_AFTER_HOURS", "24"))
TELEGRAM_REMINDER_MAX_ITEMS = int(os.getenv("TELEGRAM_REMINDER_MAX_ITEMS", "10"))

# Sağlık kontrolü: bu kadar saattir yeni haber toplanmadıysa besleme/ağ
# tarafında sessiz bir arıza var demektir (toplama günde bir çalışıyor,
# bu yüzden eşik bir günden belirgin uzun tutuldu).
HEALTHCHECK_STALE_HOURS = int(os.getenv("HEALTHCHECK_STALE_HOURS", "30"))

# Kaç gün hiç paylaşım olmazsa uyarılsın. Sağlık kontrolünün diğer maddeleri
# boru hattının PARÇALARINA bakıyor (haber geliyor mu, kuyruk tıkalı mı,
# token geçerli mi); hiçbiri "bu hesapta bir şey yayınlanıyor mu?" diye
# sormuyordu. Yayın tamamen dursa bütün kontroller yeşil kalıyordu.
#
# 2 gün seçildi çünkü onay manuel: bir gün ara vermek normal, iki gün
# sessizlik ise bir şeyin takıldığını gösterir.
HEALTHCHECK_NO_PUBLISH_DAYS = int(os.getenv("HEALTHCHECK_NO_PUBLISH_DAYS", "2"))

SCHEDULE_SLOTS_BY_TYPE = {
    "post": ["morning_post", "night_post"],
    "story": ["noon_stories", "evening_stories"],
    "reels": ["afternoon_reels"],
}

# ============================================
# INSTAGRAM API AYARLARI
# ============================================
INSTAGRAM_API = {
    "base_url": "https://graph.instagram.com/v21.0",
    "graph_url": "https://graph.facebook.com/v21.0",
    "rate_limit": 200,               # İstek/saat
    "poll_interval": 5,              # Container durum kontrolü (saniye)
    "max_poll_attempts": 60,         # Maksimum kontrol sayısı
}

# ============================================
# DASHBOARD AYARLARI
# ============================================
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_DEBUG = os.getenv("FLASK_DEBUG", "False").lower() == "true"

# Panel şifreli girişi — DASHBOARD_PASSWORD boşsa giriş ekranı devre dışı
# kalır (yerel kullanım için sorun değil). Panel bir VDS'e taşınıp internete
# açılmadan önce mutlaka doldurulmalı.
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = _env("DASHBOARD_PASSWORD")

# Flask oturum (session) imzalama anahtarı. Boş bırakılırsa süreç başına
# rastgele üretilir (her yeniden başlatmada tüm oturumlar düşer) — .env'e
# kalıcı bir değer girilmesi önerilir:
#   python -c "import secrets; print(secrets.token_hex(32))"
DASHBOARD_SECRET_KEY = _env("DASHBOARD_SECRET_KEY")

# DASHBOARD_PASSWORD doluyken art arda başarısız giriş denemesi limiti
# (basit bellek-içi brute-force koruması — bkz. src/dashboard.py).
DASHBOARD_MAX_LOGIN_ATTEMPTS = int(os.getenv("DASHBOARD_MAX_LOGIN_ATTEMPTS", "5"))
DASHBOARD_LOGIN_LOCKOUT_SECONDS = int(os.getenv("DASHBOARD_LOGIN_LOCKOUT_SECONDS", "300"))

# ============================================
# LOG AYARLARI
# ============================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "app.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

# ============================================
# BAKIM: YEDEKLEME, RETENTION, DİSK
# ============================================
# 2026-08-03 denetiminin bulguları:
#   - news.db'nin HİÇ yedeği yoktu; tek kopya, yeniden üretilemez (onay
#     geçmişi, yayın kaydı, ayarlar).
#   - output/ 8 günde 4.3 GB'a ulaşmıştı (~540 MB/gün) ve hiçbir şey
#     silinmiyordu. Disk dolduğunda beş servis birden durur, ayrıca SQLite
#     dolu diskte yazma sırasında bozulabilir.

BACKUP_DIR = DATA_DIR / "backups"
# Kaç günlük yedek saklanacak. 14 gün: bozulmanın fark edilmesi günler
# alabiliyor (yayın hatası ancak Instagram'a bakınca görülüyor), tek günlük
# yedek "bozuk veriyi bozuk yedekle üzerine yazma" riskini taşır.
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "14"))

# Üretilmiş medyanın diskte kalma süresi. Yayımlanan içerik Cloudinary'ye
# yüklendiği için yerel kopya yalnızca hata ayıklama/yeniden deneme içindir.
MEDIA_RETENTION_DAYS = int(os.getenv("MEDIA_RETENTION_DAYS", "14"))
# İndirilen görsel önbellekleri (Pexels/makale/kapak) — istenirse yeniden
# indirilebilir, bu yüzden daha kısa tutulabilir.
CACHE_RETENTION_DAYS = int(os.getenv("CACHE_RETENTION_DAYS", "30"))

# Disk doluluğu bu yüzdeyi aşarsa sağlık kontrolü uyarı üretir.
DISK_USAGE_WARN_PERCENT = int(os.getenv("DISK_USAGE_WARN_PERCENT", "80"))

# İşlenmemiş haber bu kadar gün sonra emekliye ayrılır (kuyruktan düşer).
#
# 2026-08-03 ölçümü: 839 haberin 290'ı (%35) hiç işlenmemişti ve fark
# açılıyordu — son 24 saatte 75 haber toplandı, 30'u işlendi (+45/gün).
# Sebep: process_all_news tur başına ~15 haber alıyor, get_unprocessed_news
# ise `collected_at DESC` ile EN YENİDEN başlıyor. Dolayısıyla en eski
# işlenmemiş haberler sürekli gelen yenilerin altında kalıp asla sıraya
# gelmiyordu; birikmenin 171'i hâlâ 31 Temmuz'dandı.
#
# Kapasiteyi artırmak yanlış cevap: 15/tur limiti medya kotasıyla bilinçli
# olarak hizalı ve Gemini israfını kesiyor. Doğru cevap eskiyen haberi
# AÇIKÇA düşürmek — 2 günlük haberin yayın değeri zaten yok, sessizce
# birikmesi ise her turda sorgulanan tabloyu şişiriyordu.
#
# 2 gün → 1 güne indirildi (kullanıcı talebi, 21 Ağustos 2026): "eski
# haberleri sil, 24 saatte paylaşılmadıysa boşta sırada yer kaplamasın".
# Gerekçe aynı zamanda backlog freniyle de örtüşüyor — kuyruk günlerce
# tavanın altına inmiyordu (bkz. proje hafızası: account_reality_and_
# capacity), 24 saatlik raf ömrü kuyruğun kendiliğinden daha sık erimesini
# sağlar.
UNPROCESSED_NEWS_EXPIRY_DAYS = int(os.getenv("UNPROCESSED_NEWS_EXPIRY_DAYS", "1"))

# Onay bekleyen TASLAĞIN raf ömrü. Haber emekliye ayrılıyordu ama taslak
# ayrılmıyordu; 7 Ağustos 2026 ölçümünde 466 taslak birikmişti ve en
# eskisi 1 Ağustos'tandı. Altı günlük bir oyun haberini yayınlamanın değeri
# zaten yok — bayat taslak kuyrukta yer tutmaktan başka bir şey yapmıyor.
#
# 3 gün → 1 güne indirildi (kullanıcı talebi, 21 Ağustos 2026): aynı
# gerekçe — "24 saatte paylaşılmadıysa sil".
DRAFT_EXPIRY_DAYS = int(os.getenv("DRAFT_EXPIRY_DAYS", "1"))

# Üretim freni: onay kuyruğunda kaç GÜNLÜK yayın kapasitesi birikince yeni
# içerik üretimi duraklatılsın. Günlük bütçe tek başına yetmiyordu çünkü
# bütçe her gün sıfırlanıyor, kuyruk sıfırlanmıyor. Kuyruk eridikçe fren
# kendiliğinden açılır.
DRAFT_BACKLOG_DAYS = int(os.getenv("DRAFT_BACKLOG_DAYS", "2"))

# İşlenmemiş haber kuyruğunda tazeliğin ağırlığı: haber her gün eskidikçe
# önceliğinden bu kadar düşülür.
#
# Seçim artık öneme göre yapılıyor (bkz. get_unprocessed_news), ama tazelik
# tamamen atılmadı: eşit puanlı iki haberden yeni olan öne geçmeli, ve bayat
# ama yüksek puanlı bir haber kuyruğu sonsuza kadar tıkamamalı. 0.05/gün,
# ölçülen puan yayılımına (std ≈ 0.11) göre kasıtlı olarak yumuşak — iki
# günlük bir haber ancak 0.10 puan kaybeder, yani gerçekten değerliyse
# hâlâ yarışta kalır.
RELEVANCE_RECENCY_DECAY_PER_DAY = float(
    os.getenv("RELEVANCE_RECENCY_DECAY_PER_DAY", "0.05")
)

# Bir işleme partisinde tek bir kaynaktan gelebilecek azami haber oranı.
#
# Seçim puana çevrildikten sonra ölçüldü: bir turluk ilk 30 haberin 16'sı
# TEK kaynaktan geliyordu (kuyrukta 33 farklı kaynak varken ilk 30'da yalnızca
# 9'u temsil ediliyordu). Yani puan sıralaması, açlık problemini çözmek yerine
# başka bir kaynağa taşımıştı.
#
# Sınır gerekli, çünkü puan kaynak düzeyinde onayı ÖNGÖRMÜYOR: Heavy.com
# ortalama 0.767 puan alıp %0 onaylanıyor, GlobeNewswire 0.683 ile yine %0.
# Bu kaynaklar puanı "kazanıyor" ama yayınlanabilir içerik üretmiyor —
# sınırsız bıraksak slotları doldurup gerçek haberi dışarıda bırakırlardı.
#
# Sert bir kota değil bir tavan: kaynak gerçekten baskınsa yine de partinin
# dörtte birini alır.
MAX_NEWS_PER_SOURCE_RATIO = float(os.getenv("MAX_NEWS_PER_SOURCE_RATIO", "0.25"))
# Küçük partilerde oran anlamsızlaşır (15'in %25'i 3.75); en az bu kadarına
# her zaman izin verilir.
MIN_NEWS_PER_SOURCE = int(os.getenv("MIN_NEWS_PER_SOURCE", "2"))

# ============================================
# GÜNLÜK DERLEME (kaydırmalı özet gönderi)
# ============================================
# Kapsam sorununun asıl çözümü: günde ~75 haber toplanıyor ama feed kotası
# 2 gönderi. Tek tek yayınlayarak kapsamı büyütmek Instagram'da spam sinyali
# (2 Ağustos'ta 18 gönderi yayınlanmıştı). Derleme, TEK feed slotunda birden
# çok habere yer vererek kapsamı gönderi sayısını artırmadan büyütür.
#
# Derleme ham haberden değil, zaten üretilmiş TASLAKLARDAN kurulur: Türkçe
# özet hazır olduğu için ek Gemini maliyeti yok ve birikmiş taslaklar
# (ölçümde 255 adet) değerlendirilmiş olur.
ROUNDUP_ENABLED = os.getenv("ROUNDUP_ENABLED", "true").lower() == "true"
# Slayt sayısı: 1 kapak + bu kadar haber. Instagram carousel'i 10 slaytla
# sınırlı; 6 haber kapakla birlikte 7 slayt eder ve kaydırma yorgunluğu
# yaratmadan iyi bir kapsam sağlar.
ROUNDUP_ITEM_COUNT = int(os.getenv("ROUNDUP_ITEM_COUNT", "6"))
# Derlemenin anlamlı olması için gereken asgari haber sayısı. Altındaysa
# derleme üretilmez — 2 haberlik bir "günün haberleri" gönderisi zayıf durur.
ROUNDUP_MIN_ITEMS = int(os.getenv("ROUNDUP_MIN_ITEMS", "4"))
# Derlemenin hazırlanacağı saat (Türkiye saati). Gün içindeki haberlerin
# çoğu toplanmış olsun diye akşama yakın.
ROUNDUP_TIME = os.getenv("ROUNDUP_TIME", "18:30")

# Sağlık kontrolünün çalışacağı SABİT saatler. Eskiden `every(6).hours` idi
# ama o, ilk çalışmayı kurulumdan 6 saat sonrasına yazıyor ve telafi
# mekanizması saatsiz işleri kapsamıyor — yani her deploy sayacı sıfırlıyor
# ve deploy'lar 6 saatten sık olduğu sürece kontrol HİÇ çalışmıyordu.
# Sabit saatler hem yeniden başlatmaya dayanıklı hem de telafi kapsamında.
HEALTHCHECK_TIMES = tuple(
    s.strip() for s in os.getenv("HEALTHCHECK_TIMES", "07:00,13:00,19:00,01:00").split(",")
    if s.strip()
)

# ============================================
# INSTAGRAM TOKEN YENİLEME
# ============================================
# Uzun ömürlü Instagram/Facebook token'ları ~60 günde bir yenilenmelidir.
# Süresi dolmasına bu kadar gün kalınca otomatik yenileme denenir.
TOKEN_REFRESH_WARNING_DAYS = int(os.getenv("TOKEN_REFRESH_WARNING_DAYS", "10"))

# ============================================
# MCP SUNUCUSU (Gemini Spark / harici araştırma köprüsü)
# ============================================
# Bu sunucu Cloudflare Tunnel gibi bir araçla dışa açılıp Gemini'nin
# "Bağlı Uygulamalar" özelliğine MCP sunucusu olarak eklenmek üzere tasarlandı.
MCP_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8765"))
# URL'ye gömülü paylaşılan sır — üretmek için:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
MCP_SHARED_SECRET = _env("MCP_SHARED_SECRET")
MCP_PATH = f"/mcp/{MCP_SHARED_SECRET}" if MCP_SHARED_SECRET else "/mcp"

# ============================================
# HARİCİ ARAŞTIRMA API'Sİ (Claude Code Routine köprüsü)
# ============================================
# claude.ai "Routines" (zamanlanmış bulut ajanları) özel MCP connector'ları
# desteklemediğinden (bkz. gerçek deneme — Gemini Spark MCP köprüsü rutin
# sisteminde hiç görünmedi), harici araştırma ajanları için Dashboard'da
# ayrı, sınırlı yetkili bir REST uç noktası (/api/research/submit) kullanılır.
# Üretmek için: python -c "import secrets; print(secrets.token_urlsafe(32))"
RESEARCH_API_TOKEN = _env("RESEARCH_API_TOKEN")

# ============================================
# TELEGRAM ONAY BOTU
# ============================================
# @BotFather ile bot oluşturup token alın, bota /start yazıp chat_id'nizi
# scripts/get_telegram_chat_id.py ile öğrenin.
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

# "auto": relevance_score eşiğini geçen içerik otomatik yayınlanır (Faz 2).
# "telegram": hiçbir içerik otomatik yayınlanmaz, hepsi için Telegram'dan onay istenir.
APPROVAL_MODE = os.getenv("APPROVAL_MODE", "telegram")

# ============================================
# AI PROMPT ŞABLONLARI
# ============================================

# Caption üretiminde her seferinde aynı ("kanca + açıklama + çağrı") kalıba
# düşülmesin diye, içerik başına deterministik olarak (seed'e göre — diğer
# rotasyonlarla aynı desen) bunlardan biri seçilip prompt'a enjekte edilir.
# Caption açılış tarzları — içeriğe göre deterministik olarak dönülür.
#
# ÖRNEK CÜMLE VERİLMİYOR, bilerek. Önceki sürüm tarzları örnekle
# anlatıyordu (ör. "Bunu bekliyor muydunuz?", "Vay be") ve model o
# örnekleri KELİMESİ KELİMESİNE kopyalıyordu.
#
# Ölçüm (8 Ağustos 2026, son 14 günün 446 gönderi caption'ı):
#   "vay ..."                  → 52 caption (%12)
#   "bunu ..."                 → 69 caption (%15)
#   "Bunu bekliyor muydunuz?"  → 20 kez, birebir aynı
#   "Bunu okuyunca açıkçası"   → 15 kez, birebir aynı
# Yani rotasyon çeşitlilik üretmek için vardı ama örnekler tam tersini
# yapıp yeni bir kalıp yaratmıştı. Tarz artık NE YAPILACAĞINI tarif
# ediyor, kopyalanacak bir cümle vermiyor.
CAPTION_STYLES = [
    "Haberin ortaya attığı gerçek bir soruyla başla; okuyucunun merak "
    "edeceği şeyi sor, sonra haberi anlat. Kalıplaşmış bir soru kullanma.",
    "Kişisel ve samimi bir ilk tepkiyle başla — ama hazır bir kalıp değil, "
    "bu habere özgü bir tepki olsun.",
    "Hiç giriş cümlesi kullanma — haberin en çarpıcı detayına/rakamına "
    "direkt gir.",
    "Kısa bir bağlam/geri plan cümlesiyle başla, sonra asıl gelişmeyi ver.",
    "Gündelik, sohbet havasında bir ünlemle başla; ünlem bu haberin "
    "kendisine uysun, genel bir şaşırma ifadesi olmasın.",
    "Haberi kısaca özetleyip ardından kendi kısa bir yorumunu/çıkarımını ekle.",
]

PROMPTS = {
    "summarize_news": """
Sen bir sosyal medya içerik uzmanısın. Aşağıdaki haberi Instagram gönderisi
için Türkçe bir MANŞETE çevir. Bu metin doğrudan gönderi GÖRSELİNİN üzerine
büyük puntoyla basılacak — bir gazete manşeti / dijital billboard gibi
düşün, bir özet paragrafı gibi DEĞİL.

EN ÖNEMLİ KURAL — UZUNLUK:
- 5-8 KELİME, en fazla 60 KARAKTER. Bu bir tavsiye değil, sert bir sınırdır.
- Tek bir cümle (ya da cümle bile olmayan bir manşet ifadesi). İki cümle YAZMA.
- Neden: metin uzadıkça görseldeki font otomatik küçülüyor ve akışta
  telefondan okunamaz hale geliyor. Kısa manşet = büyük, okunaklı punto.
- Haberin TEK en çarpıcı bilgisini seç; geri kalan detay caption'a kalacak,
  onları buraya sıkıştırmaya çalışma.

Diğer kurallar:
- En dikkat çekici somut bilgiyi (isim, rakam, tarih) manşete koy.
  Ör. kötü: "Bir üniversitenin öğrencileri sektörde iş buldu"
      iyi : "182 öğrenci VFX ve oyun stüdyolarında işe başladı"
- Emoji kullanma ya da en fazla 1 tane kullan (kısa manşette emoji yer çalar).
- Clickbait olmamalı, bilgilendirici olmalı.
- Kusursuz, doğal Türkçe yaz: doğru yazım, doğru ekler, doğru noktalama;
  İngilizceden birebir çevrilmiş gibi hissettirmesin.
- Başlık/açıklamada yer almayan hiçbir rakam, tarih veya bilgi UYDURMA.
- "Detaylar profilde", "detaylar bio'da", "detaylar için tıkla", "detaylar
  hikayede/içeride/yayında", "kaydır", "linke tıkla" gibi var olmayan bir
  hedefe (bio linki, ayrı bir hikaye, ayrı bir sayfa vb.) yönlendiren HİÇBİR
  ifade KULLANMA — böyle bir hedef sistemde yok, kullanırsan takipçiyi
  yanıltmış olursun.
- SOMUT OL: "Yeni oyunlar çıktı", "büyük bir gelişme oldu" gibi konuyu
  isimsiz/tarihsiz anlatan, başka herhangi bir habere de uyabilecek genel
  geçer bir manşet YASAK. Açıklamada somut bir isim/tarih/rakam YOKSA,
  uydurmadan haberin konusunu olabildiğince net söyle.

Haber Başlığı: {title}
Haber Özeti: {description}

Sadece manşet metnini yaz, başka bir şey ekleme.
""",

    "generate_caption": """
Bu Instagram hesabını yöneten gerçek bir kişi gibi yaz — yapay zeka ve oyun
dünyasını takip etmeyi seven, gördüğü ilginç bir haberi takipçileriyle
paylaşan biri gibi. Yapay zeka tarafından üretilmiş, kalıplaşmış bir metin
GİBİ HİSSETTİRMEMELİ; her caption'ın aynı yapıda (şok edici kanca cümlesi +
özet + "yorumlara yaz" çağrısı) olması tam olarak kaçınman gereken şey.

Bu sefer şu tarzda yaz: {style}

TARZ BİR KALIP DEĞİL: yukarıdaki tarif nasıl BAŞLAYACAĞINI söylüyor,
hangi kelimeleri kullanacağını değil. Açılış cümlesini bu habere özel
kur. Ölçüldü (446 caption): hazır bir açılış kalıbı verildiğinde model
onu birebir tekrarlıyor ve caption'ların %12'si aynı iki kelimeyle
başlıyordu — çeşitlilik için var olan rotasyon yeni bir kalıba
dönüşmüştü.

İLK SATIR (en kritik kural):
- Instagram caption'ın yalnızca ilk ~125 karakterini gösterir, gerisini
  "… daha fazla" ile gizler. Bu yüzden İLK SATIR tek başına anlamlı,
  TAMAMLANMIŞ bir cümle olmalı ve 120 KARAKTERİ GEÇMEMELİ.
- İlk satırı bir noktada/soru işaretinde bitir; cümleyi ikinci satıra
  SARKITMA. Kesilirse takipçi kelime ortasında kopuk bir metin görür.
- Haberin en çarpıcı bilgisini ya da gerçek bir merak sorusunu bu ilk
  satıra koy — "asıl olay" ikinci satırda başlasın.
- Ardından boş satır bırakıp detayları yaz.

Dil kalitesi (ÖNEMLİ — bunlara özenle uy):
- Kusursuz, düzgün Türkçe yaz: doğru yazım, doğru ekler (-de/-da, -mi/-mı
  bitişik/ayrı yazımı, büyük/küçük harf), doğru noktalama.
- İngilizceden birebir/kelimesi kelimesine çevrilmiş gibi HİSSETTİRMESİN —
  "bunu duyduğunuzda" yerine doğal bir Türkçe cümle kur, İngilizce cümle
  yapısını Türkçeye taşıma.
- Terim/marka/oyun adlarını olduğu gibi bırak (ör. "GPU", "PlayStation"),
  ama çevresindeki cümleyi tamamen doğal Türkçe kur.
- Emin olmadığın hiçbir rakam, tarih, isim veya alıntıyı UYDURMA — sadece
  aşağıda verilen başlık/açıklamada geçen bilgiyi kullan.
- SOMUT OL: Açıklamada geçen isim, ürün/oyun adı, tarih veya rakam varsa
  caption'da MUTLAKA yer versin. "Birkaç yeni oyun duyuruldu" gibi hangi
  oyun/tarih olduğunu belirtmeyen genel bir cümle YETERSİZ — başlıkta
  değinilen konuyu somut isim/tarihle karşılamalı, boş bir vaat gibi kalmamalı.

Diğer kurallar:
- Doğal, sohbet havasında bir Türkçe kullan — basın bülteni gibi resmi olma.
- Emoji istersen kullan ama her cümlede/satır başında olması şart değil, abartma.
- Bir çağrı (yorum yap, kaydet, paylaş vb.) eklemek istersen ekle, ama bunu
  HER SEFERİNDE yapma ve hep aynı kalıpla yazma — bazen hiç olmasın.
- "Detaylar profilde", "detaylar bio'da", "detaylar için tıkla", "detaylar
  hikayede/içeride/yayında", "kaydır", "linke tıkla", "bağlantıya göz at" gibi
  var olmayan bir hedefe (bio linki, ayrı bir hikaye, ayrı bir sayfa vb.)
  yönlendiren HİÇBİR ifade KULLANMA — böyle bir hedef sistemde yok,
  kullanırsan takipçiyi yanıltmış olursun.
- Toplam 2200 karakteri geçmesin.
- Hashtag'leri caption metninin içine gömme, ayrı bir liste olarak ver.
- Dil: Türkçe

HASHTAG KURALLARI (SADECE 3-5 ADET):
- En fazla 5, en az 3 hashtag ver. Daha fazlası Instagram'da spam sinyali
  sayılıyor ve erişimi düşürüyor; artık algoritma etiket yığınını değil
  caption'daki anahtar kelimeleri dikkate alıyor.
- Etiketler NİŞ ve habere ÖZEL olsun: oyunun/ürünün/şirketin adı, alt tür.
  Ör. iyi: #EldenRing #FromSoftware #SoulsLike
- "#keşfet", "#kesfet", "#takipet", "#viral", "#trending", "#instagood"
  gibi genel/etkileşim yemi etiketleri ASLA KULLANMA — bunlar hem işe
  yaramıyor hem de hesabı spam gibi gösteriyor.

Kategori: {category}
Başlık: {title}
Açıklama: {description}
Kaynak: {source}

Yanıtını TAM OLARAK şu formatta ver (başka hiçbir şey ekleme):
CAPTION:
<caption metni, birden fazla satır olabilir>
HASHTAGS: #tag1 #tag2 #tag3
""",

    "detect_list_content": """
Aşağıdaki haberi incele. Haberin KENDİSİ, AYNI TÜRDEN birden fazla şeyi
(ör. birden fazla OYUN, birden fazla İPUCU, birden fazla GÜNCELLEME) yan
yana sayıp karşılaştıran/listeleyen bir İÇERİK MİYDİ (ör. "Ağustos'un en
iyi 5 oyunu", "Bilmeniz gereken 3 kritik gelişme", "Bu haftaki güncellemeler")?
Yoksa TEK BİR konuyu (tek bir oyun/ürün/olay) mı anlatıyor?

ÇOK ÖNEMLİ — bunlarla YANILMA:
- Tek bir oyun/ürünü tanıtan bir haberde geliştirici stüdyo adı, yayıncı
  adı, hangi platformlarda satıldığı (Steam/Epic/Xbox vb.), veya "bu oyun
  X ve Y'den ilham aldı" gibi KARŞILAŞTIRMA amaçlı anılan başka oyun
  isimleri geçebilir. BUNLAR LİSTE ÖGESİ DEĞİLDİR — hepsi TEK bir konunun
  (o tek oyunun) destekleyici detaylarıdır. Böyle bir haberde LİSTE: HAYIR
  demelisin, geliştirici/yayıncı/platform adlarını asla ayrı "öge" gibi
  çıkarma.
- Sadece haberin KONUSUNUN KENDİSİ birden fazla AYRI ürünü/oyunu/ipucunu
  sıralamak olduğunda (ör. "Bu hafta çıkan 5 oyun") LİSTE: EVET de ve
  ÖGELER o listelenen ürünler/oyunlar olsun — geliştirici/yayıncı/platform
  gibi yan bilgiler asla öge olmasın.
- Emin değilsen HAYIR de — yanlış bir LİSTE:EVET, tek bir haberi anlamsız
  parçalara böler (ör. bir oyun haberini "Steam", "Epic Games Store" gibi
  platform adlarından oluşan saçma bir carousel'e çevirmek).

Kurallar (LİSTE: EVET ise):
- Sadece Haber Özeti'nde GERÇEKTEN isimlendirilmiş, birbirinden ayrı en az 2
  öge varsa LİSTE say.
- Uydurma öge EKLEME — sadece açıklamada geçen isimleri kullan.
- En fazla 8 öge al (fazlaysa en önemli/ilk 8'ini seç).
- Kusursuz, doğal Türkçe yaz.

Haber Başlığı: {title}
Haber Özeti: {description}

Yanıtını TAM OLARAK şu formatta ver (başka hiçbir şey ekleme):
LİSTE: EVET veya HAYIR

Eğer LİSTE: EVET ise devamında (HAYIR ise başka bir şey yazma):
KAPAK: <carousel'in ilk slaydı için kısa, dikkat çekici başlık, max 150 karakter>
ÖGE 1: <öge adı> | <o ögeyle ilgili 1 kısa cümle, varsa tarih/rakam>
ÖGE 2: <öge adı> | <1 kısa cümle>
(açıklamada kaç öge varsa o kadar ÖGE satırı, en fazla 8)
""",

    "generate_story_text": """
Aşağıdaki haber için Instagram hikayesi metni oluştur. Bu metin görselin
üzerine tek başlık olarak basılacak — görüntülenen tek bilgi bu. Bu, feed
gönderisi manşetiyle (bkz. summarize_news) TAM AYNI görsel rolü oynuyor —
aynı sert uzunluk kuralı burada da geçerli.

EN ÖNEMLİ KURAL — UZUNLUK:
- 5-8 KELİME, en fazla 60 KARAKTER. Bu bir tavsiye değil, sert bir sınırdır.
- Tek bir cümle. İki cümle YAZMA.
- Neden: metin uzadıkça görseldeki font okunaksızlaşır ya da satır taşar.
  Kısa manşet = büyük, okunaklı punto.

Diğer kurallar:
- En fazla 1 emoji kullan (kısa manşette emoji yer çalar).
- Kusursuz, doğal Türkçe yaz — İngilizceden birebir çevrilmiş gibi
  hissettirmesin, uydurma bilgi ekleme.
- "Detaylar profilde", "detaylar bio'da", "detaylar için tıkla", "detaylar
  hikayede/içeride/yayında", "kaydır", "linke tıkla" gibi var olmayan bir
  hedefe yönlendiren HİÇBİR ifade KULLANMA — sistemde böyle bir hedef yok.
- SOMUT OL: Açıklamada geçen isim, ürün/oyun adı, tarih veya rakam varsa,
  en az birini metne ekle. "Yeni oyunlar çıktı!" gibi hangi oyun/tarih
  olduğunu belirtmeyen boş/genel bir cümle YETERSİZ — karakter sınırı dar
  diye somutluktan ödün verme, gerekirse süslemeyi/emojiyi azalt.
- Dil: Türkçe

Başlık: {title}
Açıklama: {description}

Sadece hikaye metnini yaz.
""",

    # Bu metin DUYULMUYOR, OKUNUYOR. Video sessiz (bkz. REELS_SILENT) ve her
    # segment ekranda yalnızca REELS_CONFIG["duration_per_slide"] saniye
    # duruyor. Prompt eskiden "sesli okuma için" diyordu; model de anlatıma
    # göre, süslü ve uzun cümleler yazıyordu.
    #
    # Ölçüm (8 Ağustos 2026): 165 segmentin 152'si (%92) okunabilir bütçeyi
    # aşıyordu — ortalama 13,5 kelime / 96 karakter. İzleyici aynı anda
    # görsele de baktığı için bunların çoğu bitmeden slayt geçiyordu.
    # Süsleme de ölçülebilir bir sorundu: "oyuncuları harika sürprizler
    # bekliyor" gibi hiçbir bilgi taşımayan, üstelik haberde olmayan bir
    # değer yargısı uyduran cümleler.
    "generate_reels_script": """
Aşağıdaki haberler için kısa bir Reels senaryosu yaz.

ÖNEMLİ: Bu metin SESLİ OKUNMAYACAK. Videoda sessizce, ekranda YAZI olarak
görünecek ve her satır ekranda sadece {slayt_saniye} saniye duracak. Bu
yüzden anlatım diliyle değil, MANŞET gibi kısa ve doğrudan yaz.

Kurallar:
- Her haber TEK CÜMLE, en fazla {segment_kelime} kelime. Sığmıyorsa süslemeyi at,
  bilgiyi tut.
- SOMUT OL: haberdeki isim, ürün/oyun adı, tarih veya rakamdan en az birini
  cümleye koy. "Yeni oyunlar çıktı!" gibi hangi oyun olduğunu söylemeyen
  cümle yetersiz.
- SADECE haberde YAZAN bilgiyi kullan. Rakam, tarih, fiyat, özellik veya
  değer yargısı UYDURMA. "Harika sürprizler bekliyor", "efsane olacak",
  "heyecanla beklenen" gibi haberde geçmeyen yorumları yazma.
- İçi boş sıfat ve zarf kullanma (muhteşem, inanılmaz, çılgın, nihayet...).
- "Detaylar profilde", "linke tıkla", "kaydır" gibi var olmayan bir hedefe
  yönlendirme YAPMA — sistemde böyle bir hedef yok.
- Kusursuz, doğal Türkçe yaz — İngilizceden birebir çevrilmiş gibi
  hissettirmesin.
- Giriş: "Öne çıkan [AI/Oyun] haberleri!" — "bugünün", "son dakika",
  "az önce" gibi TAZELİK İDDİASI KULLANMA. Reels, son 7 günde
  yayınlanmış gönderilerden derleniyor (bkz. get_published_for_reels);
  "bugünün haberleri" demek ekranda doğru olmayan bir iddia olurdu.
- Çıkış: "Takip et, hiçbir haberi kaçırma!"
- Her haber metni TEK SATIR olacak (içinde satır sonu olmasın)
- Dil: Türkçe

Haberler:
{news_list}

Yanıtını TAM OLARAK şu formatta ver (başka hiçbir şey ekleme, her haber için
ayrı bir SEGMENT satırı, haberlerle aynı sırada):
INTRO: <giriş cümlesi>
SEGMENT 1: <haber1 metni>
SEGMENT 2: <haber2 metni>
OUTRO: <çıkış cümlesi>
""",
}
