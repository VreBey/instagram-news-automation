# Tasarım kararları ve ölçümler

[← README](../README.md)

Bu dosya "neden böyle yapıldı" sorusunun cevabı. Buradaki sayıların çoğu
tahmin değil ölçüm — hangi tarihte neyin ölçüldüğü metinde yazıyor.
Kod değiştirmeden önce okunması önerilir.

## Günlük işleme bütçesi

`process_all_news` günde en fazla
`max(DAILY_POST_LIMIT, DAILY_STORY_LIMIT) × MEDIA_GENERATION_MULTIPLIER`
haber işler — **tur başına değil, gün başına**.

Eskiden bu sayı her turda yeniden uygulanıyordu. Boru hattı günde birkaç kez
çalıştığı için (zamanlanmış tur + yeniden başlatma telafisi + elle
çalıştırmalar) gerçek üretim günlük yayın kapasitesinin **8–11 katına**
çıkıyordu: 3–5 Ağustos 2026'da 251 içerik üretildi, 58'ine karar verildi,
onay kuyruğunda 193 birikme oluştu ve en eski bekleyen içerik 4 günlüktü.

Fazla üretim bedava değil — Gemini kotası, görsel üretimi, disk ve
kullanıcının karar verme dikkati. Bütçe dolduğunda tur erken döner ve
sebebini loglar.

## Üretim freni: kuyruk dolduğunda üretim durur

Günlük bütçe tek başına yetmedi, çünkü **bütçe her gün sıfırlanıyor ama
kuyruk sıfırlanmıyor.** 7 Ağustos 2026 ölçümü: 466 onay bekleyen taslak, en
eskisi 1 Ağustos'tan, kuyruk günde ~22 büyüyor. Üretim günde ~30 içerik;
Telegram'a bildirilen 8; yayınlanan 8.

En çarpıcı rakam: 466 taslağın **yalnızca 109'unun medyası üretilmiş, 104'ü
Telegram'a gitmiş.** Yani 357 taslak üretildi, Gemini kotası harcadı ve
kimse görmeden kuyrukta bekledi.

Artık iki mekanizma birlikte çalışıyor:

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `DRAFT_BACKLOG_DAYS` | 2 | Kuyrukta bu kadar **günlük yayın kapasitesi** birikince yeni içerik üretimi duraklar (8/gün → tavan 16) |
| `DRAFT_EXPIRY_DAYS` | 3 | Bu kadar günden eski taslak, gecelik bakımda kuyruktan düşer |

Fren kalıcı değil: onayladıkça ya da taslaklar bayatladıkça kuyruk erir ve
üretim kendiliğinden açılır. Raf ömrü olmasaydı fren bir kez kapandığında bir
daha açılmaz, sistem kalıcı olarak susardı.

`/durum` freni açıkken bunu söyler — duraklama sessiz bir durumdur ve sebebi
görünmezse "sistem çöktü" diye okunur.

> **Bayat taslak `rejected` olur ama ayrı bir `expired_at` damgası alır.**
> `status` CHECK kısıtlı olduğu için yeni bir değer eklenemiyor, ama bu ikisi
> karıştırılmamalı: kullanıcının reddi bir **içerik kalitesi** sinyali,
> emeklilik yalnızca bir **kapasite** sinyalidir.
> `scripts/measure_relevance.py` tam olarak bu yer gerçeğini okuyor — tek bir
> gecelik bakım 375 gerçek reddin yanına 257 emeklilik ekleyecekti.

## Hangi haber işlenir? (öncelik hattı)

Günde ~75 haber toplanıyor, ~30'u işlenebiliyor. Yani her gün haberlerin
%60'ı elenmek zorunda — asıl soru **hangileri**.

Eskiden seçim `collected_at DESC` ile yapılıyordu: en son gelen kazanırdı,
haberin değeri seçime hiç girmezdi. Puan yalnızca haber *işlenirken*
hesaplandığı için seçimden sonra devreye giriyordu. Ölçülen sonuç (3 Ağustos
2026, 839 gerçek haber): emekliye ayrılanların ortalama puanı **0.728**,
yayınlananların **0.671** — elimizdekinden iyisini çöpe atıyorduk. 162 yüksek
değerli haberin **%34'ü** hiç işlenmedi.

Artık puan `add_news` anında hesaplanıyor ([src/relevance.py](../src/relevance.py))
ve seçim ona göre yapılıyor. Simülasyon, aynı kapasiteyle:

| Seçim kuralı | Yüksek değerli haberin yakalanma oranı |
|---|---|
| Tazelik (eski) | %16 |
| Puan + tazelik sönümü (yeni) | **%56** |

Tazelik atılmadı, sönüme dönüştü: eşit puanda yeni olan öne geçer ve haber
gün başına `RELEVANCE_RECENCY_DECAY_PER_DAY` (0.05) kadar öncelik kaybeder —
böylece bayat ama yüksek puanlı bir haber kuyruğu sonsuza kadar tıkamaz.

### Tek kaynak partiyi ele geçiremez

Seçimi puana çevirdikten **sonra** ölçüldü: bir turluk ilk 30 haberin **16'sı
tek kaynaktan** geliyordu (kuyrukta 33 farklı kaynak varken ilk 30'da yalnızca
9'u temsil ediliyordu). Yani puan sıralaması açlık problemini çözmemiş, başka
bir kaynağa taşımıştı.

Sınırın asıl gerekçesi, puanın kaynak düzeyinde onayı **öngörmemesi**:

| Kaynak | Ort. puan | Gerçek onay oranı |
|---|---|---|
| Heavy.com | 0.767 | **%0** |
| GlobeNewswire | 0.683 | **%0** |
| Geeky Gadgets | 0.675 | **%0** |
| Bleeding Cool | 0.762 | %50 |
| ComicBook.com | 0.812 | %38 |

Yüksek puan alıp hiç onaylanmayan kaynaklar var; sınırsız bırakılsalar
slotları doldurup gerçek haberi dışarıda bırakırlardı. `MAX_NEWS_PER_SOURCE_RATIO`
(0.25) bir kota değil **tavan** — kaynak gerçekten baskınsa yine partinin
dörtte birini alır, ve kuyrukta başka kaynak yoksa parti eksik bırakılmaz.

### Puanlama katsayıları tahminle değil ölçümle seçildi

Puan artık neyin yayınlanacağını belirlediği için katsayıları "mantıklı
görünüyor" diye değiştirmek yeterli değil. Yer gerçeği elimizde: kullanıcının
onayladığı (59) ve reddettiği (343) içerik. İyi bir puan bu ikisini ayırmalı.

```bash
python scripts/measure_relevance.py    # eski vs yeni ayırt etme gücü
```

Bileşen bazında ölçülen ayırt etme gücü (Cohen's d):

| Sinyal | d | Karar |
|---|---|---|
| Yüksek-ilgi kelime **sayısı** | +1.388 | En güçlü sinyal, tam ağırlıkta korundu |
| Açıklama uzunluğu | +0.870 | İkiliden **kademeliye** çevrildi |
| `mention_count` | +0.685 | Gerçek ama kelimelerden zayıf, ölçülü ağırlık |
| `image_url` var mı | **−0.725** | **Bonus kaldırıldı** — ters korelasyon |
| Düşük-ilgi kelimeler | −0.065 | Neredeyse etkisiz, küçük ağırlıkta bırakıldı |

İki bulgu özellikle dikkat çekici:

- **`image_url` ters çalışıyordu.** Görseli olan haberler daha çok
  reddediliyor (muhtemelen görseli beslemeyle gelen yüksek hacimli/düşük
  kaliteli kaynaklar baskın), ama puanda **+0.05 bonus** alıyordu. Bonus
  kaldırıldı; ceza da verilmedi, çünkü bu korelasyon ve 59 örneğe aşırı
  uyum riski var.
- **İlk denemem daha kötüydü.** `mention_count`'u güçlendirip anahtar
  kelimeleri kısan sürüm ayırt etme gücünü düşürdü (d 1.334 → 1.080) ve
  geri alındı. Ölçüm olmasa bu değişiklik "iyileştirme" diye yayınlanacaktı.

Sonuç: **d 1.334 → 1.502.** Bu dosyadaki katsayıları değiştirirken ölçümü
çalıştırın.

**7 Ağustos 2026 yeniden ölçümü (74 onay / 375 red): d = +1.365.** Rakam
Ağustos 3'teki 1.502'den düşük ama karşılaştırılabilir değil — o gün 59/343
örnek vardı ve yer gerçeği tanımı o zamandan beri daraltıldı. Önemli olan
**hâlâ 0.8'in (büyük etki) çok üstünde** olması: puanlama kullanıcının
onayladığıyla reddettiğini gerçekten ayırıyor. Yani darboğaz **seçim değil,
yayın kapasitesi**. Daha iyi bir sıralama fonksiyonu aramayın.

> **Yer gerçeğini kirletmeyin.** Ölçüm yalnızca KULLANICININ verdiği
> kararları sayar. İki tür `rejected` dışarıda: bayatladığı için düşen
> taslaklar (`expired_at` dolu) ve derlemeye girdiği için tekil
> yayınlanmayanlar (`used_in_roundup`). İkisi de içerik hakkında bir yargı
> değil. Sorgu `tests/test_relevance.py`'de kilitli.

### Neden bayat haber emekliye ayrılıyor?

`process_all_news` **günde** ~15 haber işliyor (medya kotasından türetilir,
bkz. [Günlük işleme bütçesi](#günlük-işleme-bütçesi)) ama günde ~150 haber
toplanıyor. Aradaki fark kuyrukta birikiyor.

> Bu bölüm eskiden "tur başına ~15" ve "`collected_at DESC` ile en yeniden
> başlıyor" diyordu; ikisi de artık yanlış — bütçe günlük hale geldi, seçim
> de puana göre yapılıyor (yukarıdaki tabloya bakın). Aynı bayat metin
> `Database.expire_stale_unprocessed_news` docstring'ine de kopyalanmıştı;
> iki kaynak birbirini "doğruladığı" için yanlışlık uzun süre fark edilmedi.

Sorunun ilk ölçümü: 3 Ağustos 2026'da 839 haberin
290'ı (%35) hiç işlenmemişti ve birikme **günde ~45** büyüyordu (75 toplanan
vs 30 işlenen); birikmenin 171'i hâlâ 31 Temmuz'dandı.

Kapasiteyi artırmak yanlış cevap olurdu: 15/tur limiti medya kotasıyla
bilinçli olarak hizalı. Bunun yerine 2 günden eski işlenmemiş haber açıkça
düşürülüyor — iki günlük haberin yayın değeri zaten yok.

Bu haberler **silinmiyor**, `is_processed = 1` + `expired_at` ile
işaretleniyor. Silmek tekilleştirmeyi bozardı (`mention_count` ve dedup
penceresi aynı tabloya bakıyor). `expired_at` ayrı bir kolon çünkü emekli
haberler de `relevance_score = 0` ile duruyor; iz bırakılmasa gerçekten düşük
puan almış haberlerle karışırlardı.

Sağlık kontrolü ayrıca **disk doluluğunu** ve **en son yedeğin yaşını**
izler; disk eşiği aşarsa veya yedek 48 saatten eskiyse Telegram'dan uyarır.

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `BACKUP_RETENTION_DAYS` | 14 | Veritabanı yedeklerinin saklanma süresi |
| `MEDIA_RETENTION_DAYS` | 14 | `output/` altındaki üretilmiş medyanın ömrü |
| `CACHE_RETENTION_DAYS` | 30 | İndirilen görsel önbelleklerinin ömrü |
| `DISK_USAGE_WARN_PERCENT` | 80 | Bu doluluğun üstünde sağlık uyarısı üretilir |

> **Neden var:** 3 Ağustos 2026 denetiminde `output/` 8 günde 4.3 GB'a
> ulaşmıştı (~540 MB/gün) ve hiçbir şey silinmiyordu; veritabanının ise hiç
> yedeği yoktu. Disk dolduğunda beş systemd servisi birden durur.

## Gemini kotası tükenirse

Kota dolduğunda sistem **şablon içerik üretmez ve yayınlamaz**. İşlenmemiş
haberler `is_processed` olarak işaretlenmeden bırakılır, kota yenilendiğinde
otomatik yeniden denenir, ve Telegram'dan tek bir uyarı gönderilir.

Bu bilinçli bir tercihtir: eskiden kota dolunca "basit özetleme"ye düşülüp
yayına devam ediliyordu — yani AI kalitesinde olmayan içerik, AI içeriğiymiş
gibi akışa giriyordu. *Bugün 1 gönderi eksik*, *kalitesiz gönderi yayınlandı*'dan
iyidir.

Gemini `.env`'de **hiç yapılandırılmamışsa** şablon yolu korunur — o geçici
bir arıza değil, bilinçli bir kurulum tercihidir.
