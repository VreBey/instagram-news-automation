"""
Oyun Kapak Görseli Entegrasyonu (Steam + RAWG)
Gaming kategorisindeki haberler için, haberin kendi görseli (article_photos)
yoksa/reddedilmişse, jenerik Pexels stok fotoğrafına ("gaming keyboard neon"
vb.) düşmeden önce, haber başlığından tahmin edilen OYUNUN GERÇEK kapak/ekran
görüntüsü denenir — kullanıcı geri bildirimi: "oyunlarda hep aynı görsel
kullanılıyor, gerçek ve bağlantılı görseller kullanılmalı".

İki kaynak sırayla denenir:
1. Steam Store'un herkese açık, API anahtarı gerektirmeyen arama uç noktası —
   sadece PC/Steam'de satılan oyunları kapsar ama anahtarsız çalışır.
2. RAWG.io oyun veritabanısı (RAWG_API_KEY ayarlıysa) — konsol-exclusive,
   Steam dışı ya da Steam'de henüz bulunamayan oyunları da kapsar. Anahtar
   yoksa bu kademe sessizce atlanır (Pexels'in mevcut opsiyonel davranışıyla
   aynı desen).

ÖNEMLİ (canlı Steam API'siyle doğrulanmış bir bulgu): arama uç noktaları ham
bir haber başlığını (ör. "Cyberpunk 2077 gets massive update with new
content") sorgu olarak kabul etmiyor — tek bir fazla kelime bile sıfır sonuç
döndürmesine yeter. Bu yüzden önce başlıktan OYUN ADI ADAYLARI (büyük harfle
başlayan/rakamlı ardışık kelime öbekleri) çıkarılır, sonra her aday her
kaynakta ayrı ayrı denenir.

Yanlış eşleşme riski gerçek ve ciddidir (canlı testte görüldü): tek kelimelik
zayıf adaylar (ör. yayıncı adı "Rockstar") alakasız bir oyunla ("Santa
Rockstar") ya da bir rakamla sonlanan adaylar (ör. "Diablo 4") tamamen
alakasız bir üründe geçen bir rakamla ("...4... El Diablo" içeren bir Funko
paketi) tesadüfen "eşleşebiliyor". YANLIŞ ama GERÇEK bir görsel göstermek,
jenerik bir stok fotoğraftan daha kötü/yanıltıcı olduğundan (bkz. proje
genelindeki "asla uydurma/yanıltma" ilkesi), doğrulama kasıtlı olarak KATI
tutulur: normalize edilmiş aday metni, sonucun normalize edilmiş adına TAM
EŞİT olmalı ya da onun BAŞINDA yer almalı — gevşek "ortak kelime" kontrolü
değil. Bu, isabet oranını (recall) biraz düşürür ama yanlış-pozitif riskini
pratikte ortadan kaldırır (bkz. tests/test_game_cover_art.py).

Oyun hiçbir kaynakta bulunamaz/eşleşmezse (ör. belirli bir oyuna atıfta
bulunmayan genel bir sektör haberi) None döner — çağıran taraf
(image_generator.py) Pexels'e, o da olmazsa mesh-gradient'e düşer.
"""

import hashlib
import io
import logging
import re
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import GAME_COVER_CACHE_DIR, GAME_COVER_MIN_DIMENSION, RAWG_API_KEY

logger = logging.getLogger(__name__)

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
RAWG_SEARCH_URL = "https://api.rawg.io/api/games"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Bir kelime öbeğinin ortasında geçebilen, oyun adının parçası olan küçük
# harfli bağlaçlar (ör. "Legend of Zelda", "Call of Duty", "God of War").
_CONNECTORS = {"of", "and", "the", "in", "vs", "vs.", "&"}

# Bir oyun adının makul azami kelime sayısı ("The Legend of Zelda: Echoes of
# Wisdom" 7). Daha uzun ön ekler üretmek hem boşuna sorgu hem de yanlış
# eşleşme riski.
_MAX_NAME_WORDS = 7

# Tek kelimelik aday OLAMAYACAK kelimeler. Gerçek yanlış eşleşme: "EA UFC 6
# top selling video game in June" başlığındaki "June", Steam'de gerçekten
# "June" adlı bir oyunla TAM eşleşip o oyunun kapağını getirdi.
_SINGLE_WORD_STOPLIST = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "news", "update", "patch", "launch", "review", "trailer", "season",
    "series", "gameplay", "studio", "console", "players", "player",
    "sale", "deal", "price", "free", "beta", "demo", "early", "access",
    "steam", "xbox", "playstation", "nintendo", "switch", "epic", "valve",
    "today", "week", "month", "year", "best", "top", "everything", "here",
}


def _normalize(text: str) -> str:
    """Karşılaştırma için: küçük harfe çevir, harf/rakam dışındaki her şeyi at."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is_cap_token(token: str) -> bool:
    """Bir kelimenin oyun-adı-parçası 'büyük harfli' sayılıp sayılmayacağını belirler."""
    if token.isdigit():
        return True
    if re.fullmatch(r"[IVXLCDM]+", token) and len(token) <= 6:
        return True
    if token.isupper() and len(token) >= 2:
        return True
    return token[0].isupper()


def _has_enough_content(phrase: str) -> bool:
    """
    En az 2 'içerik kelimesi' (bağlaç olmayan, tek harften uzun ya da rakam)
    içeren öbekler adayı olur — tek kelimelik adaylar (ör. sadece bir stüdyo/
    yayıncı adı) yanlış eşleşme riski çok yüksek olduğundan hiç denenmez.
    """
    words = re.findall(r"[a-zA-Z0-9]+", phrase.lower())
    content = [w for w in words if w not in _CONNECTORS and (w.isdigit() or len(w) >= 2)]
    # Tek kelimelik adaylar da denenir (ör. "Palia", "RuneScape", "Valorant" —
    # gerçek oyun adlarının önemli bir kısmı tek kelime). Yanlış eşleşme
    # riski _is_valid_match'te TAM EŞİTLİK şartıyla kapatılır; sadece
    # 4+ harfli kelimeler aday olur ki "The"/"New" gibi parçalar elensin.
    if len(content) == 1:
        return len(content[0]) >= 4
    return len(content) >= 2


# Ön ek stratejisi daha fazla aday ürettiği için sınır yükseltildi: 6 aday,
# 6→2 kelimelik ön ekleri kapsayacak kadar geniş. Bu kademe zaten yalnızca
# haberin kendi görseli yokken çalışıyor, ekstra sorgu maliyeti sınırlı.
def _extract_game_name_candidates(title: str, max_candidates: int = 7) -> list[str]:
    """
    Başlıktan, gerçek oyun adı olma ihtimali yüksek kelime öbeklerini çıkarır:
    ardışık "büyük harfli" (veya rakam/rakam+bağlaç) token'lardan oluşan
    öbekler. En uzun (dolayısıyla en özgün/az riskli) öbekler önce denenir.
    """
    tokens = re.findall(r"[A-Za-z0-9][\w'-]*", title)
    if not tokens:
        return []

    runs: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if _is_cap_token(token):
            current.append(token)
        elif token.lower() in _CONNECTORS and current:
            current.append(token)
        else:
            if current and current[-1].lower() not in _CONNECTORS:
                runs.append(current)
            current = []
    if current and current[-1].lower() not in _CONNECTORS:
        runs.append(current)

    # Haber başlıklarının çoğu Title Case ("Albion Online Dragonfire August 31
    # Launch Date Dragon Raids and New Region Explained") — bu durumda büyük
    # harfli kelime dizisi TÜM BAŞLIĞI kapsıyor ve hiçbir mağazada eşleşmiyor.
    # Bu yüzden her diziden, baştan başlayan giderek kısalan ÖN EKLER de aday
    # üretilir; oyun adı başlıkta neredeyse her zaman başta geçtiği için en
    # uzun ön ekten kısaya doğru denenir (ilk güvenilir eşleşme kazanır).
    phrases: list[str] = []
    for run_index, run in enumerate(runs):
        for length in range(min(len(run), _MAX_NAME_WORDS), 0, -1):
            prefix = run[:length]
            if prefix[-1].lower() in _CONNECTORS:
                continue
            phrase = " ".join(prefix)
            if not _has_enough_content(phrase):
                continue
            if length == 1:
                # Tek kelimelik adaylar SADECE başlığın ilk öbeğinden alınır —
                # oyun adı haber başlığında neredeyse her zaman başta geçer,
                # sondaki tek kelimeler ise "June"/"Gunna" gibi alakasız
                # sözcükler oluyor (bkz. _SINGLE_WORD_STOPLIST).
                if run_index != 0 or phrase.lower() in _SINGLE_WORD_STOPLIST:
                    continue
            phrases.append(phrase)

    # Uzun adaylar önce (daha özgün, yanlış eşleşme riski düşük)
    phrases.sort(key=lambda p: len(p.split()), reverse=True)

    seen: set[str] = set()
    candidates: list[str] = []
    for phrase in phrases:
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(phrase)
        if len(candidates) >= max_candidates:
            break
    return candidates


def _is_valid_match(candidate: str, result_name: str) -> bool:
    """
    Katı doğrulama: normalize edilmiş aday, bulunan sonucun normalize edilmiş
    adına tam eşit olmalı ya da onun başında yer almalı (ör. aday "Hollow
    Knight Silksong", sonuç "Hollow Knight: Silksong" → kabul; aday "Diablo 4",
    sonuç "...Funko...4...El Diablo" → red — bkz. modül docstring'i).
    """
    nc, nn = _normalize(candidate), _normalize(result_name)
    if not nc or not nn:
        return False
    # Tek kelimelik adaylarda ÖN EK eşleşmesi tehlikeli: "Rockstar" adayı
    # "Rockstar Life" gibi alakasız bir oyunla eşleşirdi (canlı testte
    # görüldü). Bu yüzden tek kelime için tam eşitlik aranır.
    if len(candidate.split()) == 1:
        return nn == nc
    return nn == nc or nn.startswith(nc)


def _search_steam(term: str) -> list[dict]:
    try:
        response = requests.get(
            STEAM_SEARCH_URL,
            params={"term": term, "l": "english", "cc": "us"},
            headers=_HEADERS, timeout=10,
        )
        response.raise_for_status()
        return response.json().get("items", [])
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"Steam arama hatası ('{term}'): {e}")
        return []


def _resolve_via_steam(candidates: list[str]) -> tuple[str, str] | None:
    """Eşleşirse (oyun_adı, kapak_görsel_url) döner, aksi halde None."""
    for candidate in candidates:
        for item in _search_steam(candidate)[:5]:
            name, appid = item.get("name"), item.get("id")
            if name and appid and _is_valid_match(candidate, name):
                # "_2x" son eki olmadan Steam CDN'i bazı oyunlar için 300x450
                # gibi düşük çözünürlüklü bir varyant döndürüyor (canlı API
                # ile doğrulandı, GAME_COVER_MIN_DIMENSION eşiğinin
                # altında kalıp reddediliyordu) — "_2x" gerçek 600x900 tam
                # çözünürlüğü garantiliyor.
                url = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/library_600x900_2x.jpg"
                return name, url
    return None


def _search_rawg(term: str) -> list[dict]:
    try:
        response = requests.get(
            RAWG_SEARCH_URL,
            params={"search": term, "key": RAWG_API_KEY, "page_size": 5},
            headers=_HEADERS, timeout=10,
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"RAWG arama hatası ('{term}'): {e}")
        return []


def _resolve_via_rawg(candidates: list[str]) -> tuple[str, str] | None:
    """Eşleşirse (oyun_adı, kapak_görsel_url) döner, aksi halde None. Anahtar yoksa hemen None."""
    if not RAWG_API_KEY:
        return None
    for candidate in candidates:
        for item in _search_rawg(candidate)[:5]:
            name, image_url = item.get("name"), item.get("background_image")
            if name and image_url and _is_valid_match(candidate, name):
                return name, image_url
    return None


def fetch_game_cover_art(query: str | None) -> str | None:
    """
    Haber başlığında geçen oyunu Steam'de (bulunamazsa RAWG'da) arayıp gerçek
    kapak/ekran görüntüsünü indirir (yerel önbellekten varsa oradan), yerel
    dosya yolunu döndürür.
    """
    if not query:
        return None

    # Önbellek anahtarı SORGU metninden türetilir (çözülen oyun ID'sinden
    # değil) — böylece aynı haber başlığı için ikinci çağrı (ör. hem post hem
    # story üretimi) hiçbir kaynağa ağ isteği atmadan önbellekten döner.
    cache_key = hashlib.md5(query.strip().lower().encode("utf-8")).hexdigest()
    GAME_COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = GAME_COVER_CACHE_DIR / f"{cache_key}.jpg"

    if cache_path.exists():
        return str(cache_path)

    candidates = _extract_game_name_candidates(query)
    if not candidates:
        return None

    resolved = _resolve_via_steam(candidates) or _resolve_via_rawg(candidates)
    if resolved is None:
        logger.debug(f"Hiçbir kaynakta güvenilir bir eşleşme bulunamadı, atlanıyor: '{query}'")
        return None
    matched_name, cover_url = resolved

    try:
        img_response = requests.get(cover_url, headers=_HEADERS, timeout=15)
        img_response.raise_for_status()

        img = Image.open(io.BytesIO(img_response.content))
        img.load()
        if min(img.width, img.height) < GAME_COVER_MIN_DIMENSION:
            return None

        img.convert("RGB").save(cache_path, "JPEG", quality=90)
        logger.info(f"🎮 Oyun kapak görseli indirildi: '{matched_name}' → {cache_path.name}")
        return str(cache_path)
    except (requests.RequestException, UnidentifiedImageError, OSError) as e:
        logger.debug(f"Kapak görseli indirilemedi ('{matched_name}'): {e}")
        return None
