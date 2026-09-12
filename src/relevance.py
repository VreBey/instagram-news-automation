"""Haber önem puanlaması — toplama anında hesaplanır, seçimde kullanılır.

Neden ayrı modül: puan eskiden yalnızca `ContentProcessor` içinde, haber
İŞLENİRKEN hesaplanıyordu — yani seçimden SONRA. Seçim ise
`collected_at DESC` ile, tamamen tazeliğe göre yapılıyordu. Sonuç ölçüldü
(2026-08-03): emekliye ayrılan haberlerin ortalama puanı (0.728),
yayınlananlardan (0.671) YÜKSEKTİ — elimizdekinden iyisini çöpe atıyorduk.
162 yüksek değerli haberin %34'ü hiç işlenmedi.

Simülasyon: aynı kapasiteyle, seçimi tazelik yerine puana göre yapmak yüksek
değerli haber yakalama oranını **%16'dan %56'ya** çıkarıyor. Puanlama saf
Python — hiç API çağırmıyor — dolayısıyla toplama anında hesaplamanın
maliyeti yok. `Database` bu modülü kullanabilsin diye `ContentProcessor`'dan
çıkarıldı (aksi halde dairesel import olurdu).

Puanın yer gerçeğine karşı ölçülmüş ayırt etme gücü (onaylanan vs reddedilen
içeriğin ortalama puan farkı) `scripts/measure_relevance.py` ile takip edilir.
Bu dosyada bir şey değiştirirken o ölçümü çalıştırın: puanın işi haberi
sıralamak, güzel görünmek değil.
"""

# Katsayılar TAHMİNLE değil, yer gerçeğine karşı ölçülerek seçildi
# (scripts/measure_relevance.py). Bileşen bazında ölçülen ayırt etme gücü
# (Cohen's d, onaylanan vs reddedilen içerik):
#
#   yüksek-ilgi kelime SAYISI  +1.388   <- açık ara en güçlü sinyal
#   açıklama uzunluğu          +0.870
#   mention_count              +0.685
#   image_url var mı           -0.725   <- TERS korelasyon, bkz. aşağısı
#   düşük-ilgi kelimeler       -0.065   <- pratikte hiçbir şey ayırmıyor
#
# İlk denemede (v2) mention_count güçlendirilip anahtar kelimeler kısılmıştı;
# ölçüm bunun daha KÖTÜ olduğunu gösterdi (d 1.334 -> 1.080) ve geri alındı.
# Bu dosyadaki her katsayı değişikliği ölçümle doğrulanmalı.

HIGH_INTEREST_KEYWORDS = (
    "breakthrough", "launch", "release", "reveal", "exclusive",
    "first", "new", "major", "record",
    "çığır açan", "yeni", "duyuruldu", "rekor", "lansman",
    "gpt-5", "gpt5", "gemini", "claude", "ps6", "gta 6",
    "nintendo switch 2", "unreal engine", "ai model",
)
# "announce" bilinçli olarak çıkarıldı: 32 haberde geçiyor ama ayırt etme
# gücü +0.022, yani onaylanan ve reddedilen içerikte eşit sıklıkta. Sinyal
# taşımayan bir kelime, taşıyanların katkısını sulandırıyor.

LOW_INTEREST_KEYWORDS = (
    "opinion", "editorial", "review", "rumor", "leak",
    "sponsored", "advertisement",
)
# NOT: "ad " kaldırıldı — alt dize eşleşmesi olduğu için "read ", "ahead ",
# "instead ", "dead " gibi masum kelimelerde tetikleniyordu.

BASE_SCORE = 0.5
KEYWORD_WEIGHT = 0.1

# Düşük-ilgi cezası pratikte hiçbir şey ayırmıyor (d=-0.065) ama zararsız ve
# editoryal olarak savunulabilir (görüş yazısı haber değildir), o yüzden
# küçük bir ağırlıkla duruyor.
LOW_INTEREST_PENALTY = 0.05

# Çok kaynaklı doğrulama gerçek bir sinyal (d=+0.685) ama kelimelerden zayıf.
# v2'de 0.45'e kadar çıkarılmıştı, ölçüm bunun aşırı olduğunu gösterdi.
MENTION_WEIGHT = 0.05
MAX_MENTION_STEPS = 4

# Açıklama uzunluğu güçlü bir sinyal (onaylanan 282 karakter, reddedilen 192)
# ama eskiden "50 karakterden uzun mu?" diye İKİLİ soruluyordu — 60 karakterlik
# bir tek cümleyle 400 karakterlik dolu bir özet aynı puanı alıyordu. Artık
# kademeli.
DESCRIPTION_BONUS = 0.10
DESCRIPTION_FULL_LENGTH = 250

# image_url BİLİNÇLİ olarak puanlanmıyor. Ölçümde TERS korelasyon çıktı
# (d=-0.725): görseli olan haberler daha çok reddediliyor — muhtemelen
# görseli besleme ile birlikte gelen yüksek hacimli/düşük kaliteli kaynaklar
# baskın. Eskiden +0.05 BONUS veriliyordu, yani puanı yanlış yöne itiyordu.
# Ceza da verilmiyor: bu bir korelasyon, nedensellik değil ve 59 onay
# örneğine aşırı uyum riski var.


def calculate_relevance(news: dict) -> float:
    """Haberin önem puanını hesapla (0.0 - 1.0). Saf fonksiyon, yan etkisiz."""
    text = ((news.get("title") or "") + " " + (news.get("description") or "")).lower()

    score = BASE_SCORE

    # En güçlü sinyal: kaç farklı yüksek-ilgi kelimesi geçiyor.
    for keyword in HIGH_INTEREST_KEYWORDS:
        if keyword in text:
            score += KEYWORD_WEIGHT

    for keyword in LOW_INTEREST_KEYWORDS:
        if keyword in text:
            score -= LOW_INTEREST_PENALTY

    mention_count = news.get("mention_count") or 1
    score += MENTION_WEIGHT * min(MAX_MENTION_STEPS, max(0, mention_count - 1))

    # Kademeli açıklama bonusu: 250 karakter ve üzeri tam puan alır.
    description = news.get("description") or ""
    if description:
        score += DESCRIPTION_BONUS * min(1.0, len(description) / DESCRIPTION_FULL_LENGTH)

    return max(0.0, min(1.0, score))
