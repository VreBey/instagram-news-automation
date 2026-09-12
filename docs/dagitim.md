# Dağıtım, bakım ve kurtarma

[← README](../README.md)

VPS kurulumu, Cloudflare tüneli, günlük bakım ve yedekten geri dönüş.

## 7/24 Sunucuda Çalıştırma (VPS)

Bilgisayarınız kapalıyken de sistemin çalışmaya devam etmesi için basit bir
VPS'e (ör. [Hetzner Cloud](https://www.hetzner.com/cloud/) ya da yerli bir
sağlayıcı, en ucuz plan, Ubuntu 22.04/24.04) taşınabilir.

**Dağıtım dosyaları** [deploy/](../deploy/) dizininde hazır:
- `deploy/systemd/*.service` — beş servis: scheduler (`--run`), MCP sunucusu,
  Telegram bot, dashboard (yalnızca localhost'ta dinler —
  `DASHBOARD_HOST=127.0.0.1` bilinçli olarak sabit, güvenlik için; dışarı
  açılması Cloudflare Tunnel ile sağlanır), ve tek bir Cloudflare Named
  Tunnel servisi (`instagram-cloudflare-tunnel`) — hem MCP'yi hem dashboard'u
  aynı anda dışarı açar.
- `deploy/setup_vps.sh` — proje dosyaları `/opt/instagram-otomasyon`'a
  kopyalandıktan sonra VPS'te `root` olarak çalıştırılacak script: sistem
  paketlerini + `cloudflared`'ı kurar, bir `appuser` oluşturur, Python sanal
  ortamını kurar, dört uygulama servisini kurup başlatır. `.env`'de
  `DASHBOARD_PASSWORD` boşsa uyarı verip 10 saniye bekler (yanlışlıkla
  şifresiz internete açmayı zorlaştırmak için). **Tünel servisini başlatmaz**
  — bu, aşağıdaki tek seferlik manuel adımı gerektirir.

Kısaca akış: VPS oluştur → SSH anahtarınızı ekleyin → proje dosyalarını
(`.env` dahil — `DASHBOARD_PASSWORD` ve `DASHBOARD_SECRET_KEY` mutlaka dolu
olsun) `scp`/`rsync` ile `/opt/instagram-otomasyon`'a kopyalayın →
`bash deploy/setup_vps.sh` çalıştırın.

### Cloudflare Named Tunnel kurulumu (domain sahibi olduktan sonra, tek seferlik)

> **Neden şart:** İlk sürümde kullanılan "hızlı tünel" (`cloudflared tunnel
> --url`, `*.trycloudflare.com`) hem her yeniden başlatmada adres değiştiriyor
> hem de gerçek kullanımda **Gemini'nin bağlı-uygulama doğrulaması bu
> paylaşımlı/ücretsiz domain'i güvenlik sebebiyle reddetti** (tarayıcı
> `trycloudflare.com`'a hiç istek bile atmadı). Kendi sahip olunan bir
> domain'le kalıcı, güvenilir bir adrese geçmek bunu çözüyor.

1. Bir domain satın alıp [Cloudflare](https://dash.cloudflare.com/sign-up)'a
   ekleyin (ücretsiz plan yeterli), kayıt firmanızdaki nameserver'ları
   Cloudflare'in verdikleriyle değiştirin, "Active" olmasını bekleyin.
2. VPS'te: `cloudflared tunnel login` — çıkan URL'yi Cloudflare hesabınıza
   giriş yapmış bir tarayıcıda açıp domain'inizi seçip yetkilendirin.
3. `cloudflared tunnel create <isim>` — bir tünel ID'si ve credentials
   dosyası (`~/.cloudflared/<id>.json`) üretir.
4. `~/.cloudflared/config.yml` yazın:
   ```yaml
   tunnel: <tunnel-id>
   credentials-file: /root/.cloudflared/<tunnel-id>.json
   ingress:
     - hostname: mcp.sizin-domaininiz.com
       service: http://localhost:8765
     - hostname: panel.sizin-domaininiz.com
       service: http://localhost:5000
     - service: http_status:404
   ```
5. DNS kayıtlarını tünele bağlayın:
   ```bash
   cloudflared tunnel route dns <isim> mcp.sizin-domaininiz.com
   cloudflared tunnel route dns <isim> panel.sizin-domaininiz.com
   ```
6. `deploy/systemd/instagram-cloudflare-tunnel.service`'i etkinleştirin
   (`setup_vps.sh` diğer servislerle birlikte zaten `/etc/systemd/system/`'e
   kopyaladı):
   ```bash
   systemctl enable --now instagram-cloudflare-tunnel
   ```

Bundan sonra `https://mcp.sizin-domaininiz.com/mcp/<MCP_SHARED_SECRET>` ve
`https://panel.sizin-domaininiz.com` adresleri kalıcıdır — VPS ayakta kaldığı
sürece değişmez, `cloudflared` servisi çökse bile aynı adrese geri döner.


## Kurtarma: yedekten geri dönüş

```bash
python scripts/restore.py --list              # yedekleri listele + doğrula
python scripts/restore.py <dosya>             # KURU çalışır, plan yazar
python scripts/restore.py --yes <dosya>       # gerçekten geri yükler
```

`--yes` verilmeden betik hiçbir şeyi değiştirmez; yıkıcı bir işlemin
varsayılanı güvenli olmalı.

Betiğin zorunlu kıldığı **kritik adım**, elle yapıldığında en kolay atlanan
adımdır: yedeği `news.db` üzerine kopyalayıp eski `news.db-wal` dosyasını
yerinde bırakırsanız SQLite o WAL'ı yeni dosyaya uygular ve **veritabanını
bozar ya da eski veriyi diriltir.** Baskı altında tam olarak bu unutulur.

Sıra: yedeği doğrula → servisleri durdur → mevcut veritabanını kenara al
(geri yükleme de geri alınabilsin) → `-wal`/`-shm` sil → kopyala → başlat →
doğrula. Bozuk çıkan bir yedek hiçbir şeye dokunmadan reddedilir.

> `.env` geri yüklenmez — onu `data/backups/env_*.bak` dosyasından elle
> kopyalayın (`chmod 600`). Bilinçli: anahtar dosyasını bir betiğin
> otomatik üzerine yazması istenmez.

## Bakım, yedekleme ve disk

Zamanlayıcı her gece **02:00**'de `Günlük Bakım` işini çalıştırır
([src/maintenance.py](../src/maintenance.py)). Sıra bilinçlidir — veri silen her
adımdan önce yedek alınır:

1. `data/backups/news_<tarih>.db` — SQLite `backup()` API'si ile tutarlı
   kopya (düz dosya kopyası WAL'daki işlenmemiş yazmaları kaçırır), ardından
   `PRAGMA integrity_check` ile doğrulanır. Bozuk çıkarsa yedek **saklanmaz**.
2. Saklama süresini aşan yedekler silinir (en yenisi her zaman korunur).
3. 2 günden eski, **hiç işlenmemiş** haberler kuyruktan düşürülür (aşağıya bkz.).
4. `DRAFT_EXPIRY_DAYS`'ten eski, **onay bekleyen taslaklar** kuyruktan düşürülür
   ([Üretim freni](#üretim-freni-kuyruk-dolduğunda-üretim-durur)).
5. 30 günden eski kullanılmış haber satırları temizlenir.
6. `VACUUM` — silinen satırların yeri diske geri verilir.
7. `output/` ve görsel önbelleklerindeki eski dosyalar silinir.

