# İçerik üretimi

[← README](../README.md)

Günlük derleme, Reels, görsel tasarım ve Telegram botu — boru hattının
üretim tarafındaki ayrıntılar.

## Günlük derleme: kapsamı gönderi sayısını artırmadan büyütmek

Temel gerilim: günde ~75 haber toplanıyor, feed kotası 2 gönderi. Kapsamı
tek tek yayınlayarak büyütmek Instagram'da spam sinyali — 2 Ağustos 2026'da
`DAILY_POST_LIMIT=2` ayarlıyken 18 gönderi yayınlandı.

Derleme bu gerilimi çözer: **tek feed slotu, 6 haber.** Her gün
`ROUNDUP_TIME` saatinde (18:30) kaydırmalı bir gönderi hazırlanır —
1 kapak + her haber için 1 slayt — ve normal onay akışından geçer.
Otomatik yayınlanmaz; Telegram'a bildirilir, onaylarsanız çıkar.

İki tasarım kararı:

- **Ham haberden değil, üretilmiş taslaklardan kurulur.** `summary_text`
  zaten Türkçe ve hazır olduğu için **ek Gemini maliyeti yok**. Ayrıca
  üretilip hiç yayınlanmayan taslak birikmesi (ölçümde 255 adet, üretilen
  içeriğin ~%70'i) değerlendirilmiş olur — o birikme saf israftı.
- **Dahil edilen taslak `used_in_roundup` ile işaretlenir**, `rejected` ile
  değil. Aksi halde puanlama ölçümünün yer gerçeği bozulurdu: kullanıcı o
  içeriği reddetmedi, derlemeye girdiği için tekil yayınlanmadı. İşaretlenen
  taslak ne Telegram'a ayrıca bildirilir ne de otomatik zamanlanır — aynı
  haber iki kez çıkmaz.

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `ROUNDUP_ENABLED` | true | Derleme üretilsin mi |
| `ROUNDUP_ITEM_COUNT` | 6 | Kaç haber (kapakla birlikte 7 slayt) |
| `ROUNDUP_MIN_ITEMS` | 4 | Bu sayının altında derleme üretilmez |
| `ROUNDUP_TIME` | 18:30 | Hazırlanma saati (TR) |

### Günlük kotalar kimi bağlar?

Kota **yalnızca otomatik zamanlamayı** (`auto_schedule_content`) sınırlar.
Telegram'daki **✅ Onayla** butonu ve dashboard'dan manuel onay kotayı
bilinçli olarak atlar: bunlar insan kararıdır ve "ben onay verince paylaş"
açık bir kullanıcı isteğiydi.

Bunun pratik sonucu ölçüldü — 2 Ağustos 2026'da `DAILY_POST_LIMIT=2` ayarlıyken
**18 feed gönderisi** yayınlandı (1 Ağustos: 10). Yayınlanan içeriğin tamamı
manuel onaydan geçmişti; otomatik yol `AUTO_PUBLISH_THRESHOLD` (0.75) yüzünden
neredeyse hiç tetiklenmiyor, çünkü skor ortalaması 0.669.

Onay artık **engellenmiyor ama uyarılıyor**: limitin üzerine çıkıldığında
Telegram yanıtına "⚠️ Günlük limit aşıldı: bugün N gönderi paylaşılacak
(ayarlı limit M)" satırı ekleniyor. Karar sende kalıyor; sadece görünür.

Sert sınırlama isterseniz `src/telegram_bot.py` içindeki `_quota_warning`
çağrısını uyarı yerine erken `return`'e çevirmek yeterli — ama o zaman
onayladığınız içerik sessizce ertesi güne kaymak yerine hiç zamanlanmaz,
yani davranışı bilinçli seçin.


## Reels: otomatik üretim, elle yayın

Reels videoları **otomatik ve sessiz** üretilir, Telegram'a dosya olarak
gönderilir ve **API ile yayınlanmaz** — Instagram uygulamasında müzik eklenip
elle paylaşılır.

Sebep teknik bir kısıt: **Instagram'ın lisanslı müzik kütüphanesi Graph
API'den erişilemiyor.** Meta'nın referansında reels için tek ses parametresi
`audio_name` ve tanımı *"Name of the audio of your Reels media"* — yani
videodaki mevcut sesi yeniden adlandırıyor, müzik eklemiyor. Müzik, videoya
yüklemeden **önce** gömülmek zorunda.

Dolayısıyla "otomatik yayın" ile "Instagram müziği" birbirini dışlıyor. Bu
kurulumda müzik tercih edildi: reels, yeni hesaplarda takipçi olmayanlara
ulaşmanın ana kanalı ve trend ses o kanalın parçası.

### Akış

1. Zamanlayıcı reels senaryosunu üretir (Gemini)
2. `VideoGenerator` sessiz videoyu üretir (MoviePy, 1080×1920)
3. Video Telegram'a gönderilir, açıklama ayrı mesaj olarak gelir
4. **Sen**: videoyu indir → Instagram'da paylaşırken müziği uygulamadan ekle
   → açıklamayı yapıştır

### Neden ses yok

- **Müzik uygulamada ekleniyor**, videoda ses olsaydı çakışırdı.
- **TTS bilinçli olarak kapalı**: Türkçe için ticari kullanıma açık, CPU'da
  çalışan ve mevcut `edge-tts`ten (Azure neural) daha doğal duyan bir açık
  kaynak seçenek 2026 itibarıyla yok. En iyileri (XTTS-v2, MMS-TTS)
  ticari kullanıma kapalı lisanslı; ticari kullanıma açıklar (Glow-TTS,
  TurkicTTS) daha robotik. Ekran yazısı + müzik zaten reels'in baskın formatı.

`REELS_SILENT=false` yaparsanız TTS geri gelir ve reels normal API yayın
akışına döner — ama o zaman Instagram müziği kullanamazsınız.

### Tarihçe

Önceki sürüm videoyu da kullanıcıya ürettiriyordu (Gemini/Veo promptu).
Ölçülen sonuç: **20 reels taslağı üretildi, 0 reels yayınlandı** — her
seferinde manuel iş istediği için hiç yapılmadı. Artık yalnızca son adım
(müzik + paylaş) manuel.

## Telegram teşhis komutları

Sunucuya SSH ile girmeden durum sorabilmek için bot salt-okunur komutlar
kabul eder:

| Komut | Ne gösterir |
|---|---|
| `/durum` | Servisler, disk, son yedeğin yaşı, kuyruk sayıları |
| `/huni` | Son 24 saat: toplanan → işlenen → medya → yayın |
| `/loglar` | Son hata ve uyarı satırları |
| `/kota` | Instagram token ömrü, insights izni, günlük limitler |
| `/yardim` | Komut listesi |

**Hiçbiri sistemde değişiklik yapmaz.** Bu bilinçli bir sınır: Telegram
kanalını ele geçiren biri sunucuda iş yaptıramamalı. Durum değiştiren
işlemler (onay/red) butonlarla ve `content_id` doğrulamasıyla yapılıyor,
serbest metin komutlarıyla değil. Aynı ihtiyacı karşılayan genel amaçlı bir
AI ajanı kurmak (ör. sunucuda komut çalıştırabilen bir asistan) bu garantiyi
ortadan kaldırırdı — 4 Ağustos 2026'da bu değerlendirildi ve bilinçli olarak
tercih edilmedi.

Not: eskiden `/` ile başlayan her mesaj sessizce yok sayılıyordu; `/start`
yazınca hiçbir cevap gelmemesinin sebebi buydu.

## Telegram Onay Botu

`APPROVAL_MODE=telegram` (varsayılan) iken hiçbir içerik otomatik yayınlanmaz —
medyası hazır her post/story/reels için Telegram'a bir onay isteği gönderilir.
`APPROVAL_MODE=auto` yaparsanız eski davranışa (relevance_score eşiğini geçen
otomatik yayınlanır, bkz. yukarıdaki "Otomatik yayın nasıl çalışır?") geri
dönersiniz — tek satırlık `.env` değişikliği.

### Bot Kurulumu

1. Telegram'da **@BotFather**'a `/newbot` yazıp bir bot oluşturun, verdiği
   token'ı `.env`'e `TELEGRAM_BOT_TOKEN` olarak girin.
2. Telegram'da yeni botunuza `/start` yazın.
3. `chat_id`'nizi öğrenin:
   ```bash
   python scripts/get_telegram_chat_id.py
   ```
   Çıkan `chat_id`'yi `.env`'e `TELEGRAM_CHAT_ID` olarak girin.
4. Botu başlatın:
   ```bash
   python main.py --telegram-bot
   ```

Pipeline çalıştığında (`--pipeline`, `--run` veya dashboard'dan manuel tetikleme)
medyası hazır her taslak için Telegram'a bir mesaj gelir: haberin başlığı,
kategorisi, skoru ve **✅ Onayla** / **❌ Reddet** butonları. Onaylarsanız içerik
**hemen** zamanlanır (`immediate_schedule_time`) ve bir sonraki paylaşım
kontrolünde — en geç 5 dakika içinde — yayınlanır; reddederseniz `rejected`
durumuna geçer.

> Manuel onay slot rotasyonu KULLANMAZ ve günlük limit onu **bağlamaz**;
> yalnızca uyarı ekler. Gerekçe için bkz.
> [Günlük kotalar kimi bağlar?](#günlük-kotalar-kimi-bağlar). Bu paragraf
> eskiden slot ve limit vaat ediyordu — ikisi de doğru değildi.


## Görsel Tasarım

Görseller (post/story/reels) 2026 sosyal medya tasarım trendlerine göre
üretilir: düz gradyan yerine **mesh-gradient** (birkaç bulanık ışık kaynağı),
ince **grain/doku**, sert siyah gölge yerine **renkli glow** tipografi, ve
letter-spacing'li editoryal etiketler ([src/image_generator.py](../src/image_generator.py)'daki
`_create_gradient_background`, `_draw_glow_text`, `_draw_letter_spaced`).

### Arka plan görseli öncelik zinciri

Arka plan tek bir kaynaktan gelmez; `image_generator._get_background` sırayla
şunları dener ve ilk başarılı olanı kullanır:

1. **Manuel görsel** — Telegram'dan yanıt olarak gönderilen fotoğraf
2. **Haberin kendi `image_url`'i** — RSS/NewsAPI kaydındaki görsel
3. **`og:image`** — makalenin kendi sayfasından kazınır (haberlerin %14.8'inde
   `image_url` hiç yok, %10.8'inde gelen görsel bozuk/çok küçük)
4. **Steam/RAWG oyun kapağı** — yalnızca oyun kategorisinde
5. **Pexels stok fotoğrafı** — kategoriye uygun, lisanslı
6. **Mesh-gradient** — hiçbiri tutmazsa

Canlı ölçümde içeriğin ~%85'i gerçek (stok olmayan) bir görsele ulaşıyor;
kalan ~%15 gradyanda bitiyor. **Bu kabul edilmiş dürüst bir sonuçtur, hata
değil** — görsel bulmaya zorlamak alakasız eşleşme riski taşıyor (gerçek bir
vaka: "June" kelimesi *June* adlı bir Steam oyunuyla eşleşti).

> **Telif notu:** 2. ve 3. adımlar üçüncü taraf basın görselleri kullanır.
> Altyazıda `📰 Kaynak: X` atfı veriliyor, ancak **atıf lisans değildir** —
> büyük yayın organlarının görselleri çoğunlukla ajans lisanslıdır. Bu,
> bilinçli olarak kabul edilmiş bir risktir; daraltmak isterseniz zincirin
> 2. ve 3. adımlarını kapatıp 4-6 ile devam edebilirsiniz.

**Kurulum**: [pexels.com/api](https://www.pexels.com/api/) üzerinden ücretsiz,
anında bir API anahtarı alıp `.env`'e `PEXELS_API_KEY` olarak girin. Ücretsiz
tier saatte 200 istek / ayda 20.000 istek sağlar, atıf gerektirmez, ticari
kullanıma açıktır — bu proje için fazlasıyla yeterli.

