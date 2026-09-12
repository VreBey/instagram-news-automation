# Instagram AI & Gaming News Otomasyon

**Türkçe** · [English](README.en.md)

Günlük yapay zeka ve oyun dünyası haberlerini toplayıp, AI (Gemini) ile
özetleyip, marka şablonlu görsel/hikaye/Reels içeriği üretip Instagram'a
zamanlı olarak paylaşan tek-hesaplı bir otomasyon sistemi.

> **Hiçbir şey siz onaylamadan yayınlanmaz.** Varsayılan
> `APPROVAL_MODE=telegram`: her içerik taslak olarak durur ve Telegram'dan
> onay ister. Tam otomatik yayın isteğe bağlıdır (`APPROVAL_MODE=auto`).

Ne ürettiğini görmek için kurulumdan sonra `python main.py --test` çalıştırın;
örnek gönderi, hikaye ve Reels `output/` altına yazılır. API anahtarı
gerekmez — kendi logonuz ve ayarlarınızla, kendi makinenizde üretilir.

---

## Hızlı başlangıç

**Gereken:** Python 3.10+ (üretimde 3.10.12), Git. Reels üretilecekse `ffmpeg`.

```bash
git clone https://github.com/VreBey/instagram-news-automation.git
cd instagram-news-automation
python -m venv venv
venv\Scripts\activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env         # Linux/macOS: cp .env.example .env
python main.py --test          # API'ye dokunmadan örnek görsel üretir
```

Son komut hiçbir anahtar gerektirmez — kurulumun çalıştığını doğrulamanın en
hızlı yolu budur.

### Hangi anahtarlar gerekli?

| Anahtar grubu | Zorunlu mu? | Not |
|---|---|---|
| `CLOUDINARY_*` | **Evet** | Instagram API medya için public URL ister. Bu üçü olmadan **hiçbir paylaşım tamamlanamaz** |
| `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` | Yayın için evet | Bkz. [Instagram kurulumu](docs/instagram-kurulumu.md) |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | Önerilir | Olmadan token otomatik yenilenmez, ~60 günde elle yenilemek gerekir |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Varsayılan modda evet | Onay istekleri buraya gider |
| `GEMINI_API_KEY` | Opsiyonel | Yoksa şablon tabanlı (AI'sız) caption üretimine düşülür |
| `NEWS_API_KEY`, `CURRENTS_API_KEY` | Opsiyonel | Yoksa sadece RSS kullanılır, yine de yeterli hacim sağlar |
| `NVIDIA_API_KEY` | Opsiyonel | Sadece embedding: paraphrase tekilleştirme + görsel-metin alaka. **Yoksa çökme olmaz**, bu iki kontrol sessizce kapanır |

Tam liste ve açıklamalar: [.env.example](.env.example)

### Bu depoyu devraldıysanız

Hiçbir marka adı, alan adı, sunucu adresi veya token koda gömülü **değildir**.
Kendinize göre kurmak için sadece bunları doldurun:

| Ayar | Nerede | Boş bırakılırsa |
|---|---|---|
| `BRAND_NAME` | `.env` | Panel başlığı `Instagram News Automation` olur |
| `INSTAGRAM_USERNAME` | `.env` | Panel önizlemesinde `hesabiniz` yer tutucusu görünür |
| `BRAND_HANDLE` | `.env` | Üretilen görsele hesap etiketi **hiç basılmaz** (yanlış ad basmaktansa boş) |
| `CATEGORIES` | `config.py` | Kategori adı, emoji ve yedek hashtag'ler — **kendi nişinize uyarlarken ilk değiştireceğiniz yer** |
| `REELS_CTA_TEXT`, `STORY_BADGE_TEXT` | `.env` | Reels çıkış çağrısı ve hikaye rozeti Türkçe varsayılanla kalır |
| `BOT_NAME`, `BOT_CONTACT_URL` | `.env` | RSS kaynaklarına bu deponun adresi kimlik olarak gider |
| `BARE_REPO` | `deploy/setup_vps.sh` ortam değişkeni | `/opt/instagram-news.git` varsayılır |
| `TUNNEL_NAME` | `/etc/default/instagram-tunnel` | `instagram-news` adlı tünel aranır |

`BOT_CONTACT_URL`'i **kendi** adresinizle doldurun — başkasının alan adıyla
tarama yapmak hem yanıltıcıdır hem o adresin itibarını riske atar.

**Logolar depoda yoktur**, kişiye özeldir. Kendi dosyalarınızı
`assets/logos/` altına koyun: `logo_horizontal.png` (üst/alt bar) ve
`logo_mark.png` (Reels/story rozeti). Koymazsanız görseller logosuz üretilir;
kod bunu sessizce ve sorunsuz karşılar.

---

## Mimari

```text
Haber Toplama → İçerik İşleme → Medya Üretimi → Otomatik Zamanlama → Yayın
  (RSS/News        (Gemini AI /      (Pillow /         (relevance_score      (Instagram
   API/Currents)    fallback)         MoviePy)           eşiği)                Graph API)
```

| Modül | Sorumluluk |
|---|---|
| [src/news_collector.py](src/news_collector.py) | RSS, NewsAPI ve Currents'tan haber toplar; aynı olayın farklı kaynaklarca tekrarını `src/dedup.py` ile yakalar |
| [src/content_processor.py](src/content_processor.py) | Haberleri puanlar (`relevance_score`), Gemini ile (veya şablonla) caption/hikaye/Reels senaryosu üretir |
| [src/image_generator.py](src/image_generator.py) | Feed görselleri (3 şablon arasında deterministik rotasyon) |
| [src/story_generator.py](src/story_generator.py) | Instagram Story görselleri |
| [src/video_generator.py](src/video_generator.py) | Pillow + Edge-TTS + MoviePy ile Reels videoları |
| [src/instagram_client.py](src/instagram_client.py) | Graph API istemcisi, Cloudinary yükleme, insights okuma |
| [src/token_manager.py](src/token_manager.py) | Uzun ömürlü token'ın otomatik yenilenmesi |
| [src/scheduler.py](src/scheduler.py) | Tüm boru hattının orkestrasyonu ve zamanlama |
| [src/dashboard.py](src/dashboard.py) + [templates/index.html](templates/index.html) | Flask web paneli — inceleme kuyruğu, istatistikler, insights |
| [src/database.py](src/database.py) | SQLite (WAL) veri katmanı |

## Boru hattı nasıl çalışır?

Her `--pipeline` çalıştığında (veya `--run` ile zamanlanmış olarak):

1. Haberler toplanır ve tekilleştirilir.
2. Her haber `relevance_score` (0.0–1.0) ile puanlanır. `MIN_PROCESSING_SCORE`
   altında kalanlar için AI üretimi tamamen atlanır (Gemini kotası tasarrufu).
   Üretim ayrıca günlük bütçe ve kuyruk freniyle sınırlıdır —
   bkz. [Tasarım kararları](docs/tasarim-kararlari.md).
3. En yüksek skorlu taslaklar için medya üretilir.
4. *(yalnızca `APPROVAL_MODE=auto`)* `AUTO_PUBLISH_THRESHOLD` üzerindeki ve
   medyası hazır içerikler, günlük limitler dahilinde yayınlanır.
5. Varsayılan modda içerik `draft` kalır ve Telegram'dan onay istenir; panelin
   **İçerikler** sayfası da aynı kuyruğu gösterir.

---

## Komutlar

```bash
python main.py --collect      # Haberleri topla
python main.py --process      # İçerikleri işle (AI özetleme + puanlama)
python main.py --generate     # Medya oluştur (görsel/video)
python main.py --publish      # Zamanlanmış gönderileri paylaş
python main.py --pipeline     # collect + process + generate + zamanlama, tek seferde
python main.py --run          # Zamanlayıcıyı sürekli çalıştır (günlük tam döngü)
python main.py --dashboard    # Web panelini başlat (http://127.0.0.1:5000)
python main.py --stats        # İstatistikleri göster
python main.py --test         # Test görselleri/video üret (API'ye dokunmaz)
python main.py --maintenance  # Bakımı elle çalıştır (yedek + temizlik + VACUUM)
python main.py --roundup      # Günlük derleme gönderisi üret
```

`--maintenance` normalde gerekmez, zamanlayıcı her gece 02:00'de yapar.
Deploy'dan hemen sonra ilk yedeği almak için kullanışlıdır.

## Panel

`python main.py --dashboard` → `http://127.0.0.1:5000`

- **Dashboard** — istatistikler, günlük limitler, token süresi uyarısı
- **Haberler** — toplanan tüm haberler, skorlarıyla
- **İçerikler** — taslak kuyruğu; eşiğin üzerindekiler işaretli, altındakiler
  elle onaylanabilir
- **Paylaşım Geçmişi** — yayınlanan her şey
- **İçgörüler** — performans (impressions, reach, beğeni, etkileşim), her gün
  04:00'te senkronize edilir

Panel yalnızca `127.0.0.1`'e bağlanır. Dışa açmak Cloudflare Tunnel ile
yapılır — bkz. [Dağıtım](docs/dagitim.md).

## Yapılandırma

Tüm ayarlar [config.py](config.py) içinde, çoğu `.env` ile değiştirilebilir.
Öne çıkanlar:

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `APPROVAL_MODE` | `telegram` | `telegram` = onaysız hiçbir şey yayınlanmaz; `auto` = eşiğe göre yayın |
| `AUTO_PUBLISH_THRESHOLD` | 0.75 | Bu skorun üzeri otomatik yayınlanır (auto modda) |
| `MIN_PROCESSING_SCORE` | 0.35 | Bu skorun altı için AI üretimi atlanır |
| `MEDIA_GENERATION_MULTIPLIER` | 3 | Günlük limitin kaç katı taslak işlensin. **Gemini harcamasını da belirler** |
| `DEDUP_TITLE_SIMILARITY_THRESHOLD` | 0.82 | Bu benzerliğin üstü aynı haber sayılır |
| `DEDUP_WINDOW_HOURS` | 72 | Tekilleştirmenin baktığı zaman penceresi |
| `DAILY_POST_LIMIT` / `_STORY_LIMIT` / `_REELS_LIMIT` | 2 / 5 / 1 | Günlük kotalar (**yalnızca otomatik yolu bağlar**) |
| `TOKEN_REFRESH_WARNING_DAYS` | 10 | Token bu kadar gün kala yenileme denenir |

## Test

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Testler gerçek Instagram/Cloudinary/Gemini/RSS çağrısı yapmaz; her test kendi
izole geçici SQLite dosyasını kullanır (`tests/conftest.py`).

---

## Belgeler

| Belge | İçerik |
|---|---|
| [Instagram kurulumu](docs/instagram-kurulumu.md) | Token alma ve yenileme |
| [İçerik üretimi](docs/icerik-uretimi.md) | Günlük derleme, Reels, görsel tasarım, Telegram botu |
| [Dağıtım ve bakım](docs/dagitim.md) | VPS kurulumu, Cloudflare tüneli, yedekten geri dönüş |
| [MCP köprüsü](docs/mcp-koprusu.md) | Harici AI ajanı entegrasyonu (opsiyonel) |
| [Tasarım kararları](docs/tasarim-kararlari.md) | Neden böyle yapıldı — ölçümler ve gerekçeler |
| [Katkı](CONTRIBUTING.md) | Geliştirme ortamı, kurallar, kapsam dışı olanlar |
| [Güvenlik](SECURITY.md) | Sırlar nerede durur, log redaksiyonunun sınırı |

Kod değiştirmeden önce **Tasarım kararları**'nı okuyun. Oradaki sayıların çoğu
tahmin değil ölçüm, ve birkaçı acı deneyimle bulunmuş.

---

## Bilinen sınırlamalar

- **Tek hesap, tek makine.** Çoklu hesap veya Docker dağıtımı için
  tasarlanmadı — bilinçli bir kapsam kararı.
- **Tek SQLite dosyası, dört süreç.** scheduler, dashboard, telegram ve mcp
  aynı `news.db`'yi paylaşıyor. WAL + 30sn `busy_timeout` açık, ama yük altında
  yine de `database is locked` görülebiliyor (31 Temmuz 2026'da 11 paylaşım bu
  yüzden başarısız oldu). Yazmayı tek sürece toplamak doğru çözüm, yapılmadı.
- **Üçüncü taraf basın görselleri kullanılıyor.** Arka plan zinciri haberin
  kendi görselini ve `og:image`'ı tercih ediyor. Atıf veriliyor ama lisans
  alınmıyor — kabul edilmiş bir telif riski.
- **Yerel geliştirme üretimin Telegram botunu çalar.** Aynı token ile iki
  `getUpdates` döngüsü çalışamaz (409 Conflict); yerelde ayrı bot kullanın.
- **Performans verisi `instagram_manage_insights` iznine bağlı.** İzin yoksa
  Meta `(#10) Application does not have permission` döndürür ve tablo boş
  kalır. Meta uygulama ayarlarından çözülür, kodda düzeltilebilecek bir şey
  değil.
- **Hikaye insights'ı yalnızca 24 saat.** Hikaye kaybolunca medya nesnesi
  API'de çözülmüyor.
- **Yedekler yerel.** Günlük yedek alınıyor ama aynı diskte duruyor. Sunucu
  tamamen kaybedilirse yedek de gider. **`.env`'in bir kopyasını sunucu
  dışında tutun** — veritabanını geri yükleseniz bile o dosya olmadan sistem
  çalışmaz; gerçek kurtarma süresini belirleyen şey odur.

### Güncel durum: Instagram yayını engelli

23 Ağustos 2026'da Meta hesaba API kilidi koydu (hata kodu 200). Gönderi ve
story API üzerinden yayınlanamıyor. Sistem içerik üretmeye ve elle paylaşım
paketi hazırlamaya devam ediyor. **Bu bir kurulum hatası değil** — sıfırdan
kurulum da aynı davranır; çözüm Meta tarafında.

---

## Katkıda bulunanlar

- [VreBey](https://github.com/VreBey) — ürün, tasarım kararları, işletme
- Claude (Anthropic) — [Claude Code](https://claude.com/claude-code) üzerinden
  kod, test ve belge üretimi

## Lisans

[MIT](LICENSE) — yalnızca kaynak kodu kapsar.

Fontlar, çalışma zamanında indirilen görseller ve marka adları kapsam
dışındadır; ayrıntı için [NOTICE.md](NOTICE.md).
