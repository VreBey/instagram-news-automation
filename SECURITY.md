# Güvenlik

## Açık bildirimi

Bir güvenlik açığı bulursanız **herkese açık issue açmayın**. GitHub'ın
[Security Advisories](https://github.com/VreBey/instagram-news-automation/security/advisories/new)
formunu kullanın.

## Bu projede sırlar nerede durur

Tümü `.env` dosyasındadır ve `.env` `.gitignore` içindedir. Depoda hiçbir
gerçek anahtar yoktur; `.env.example` yalnızca anahtar adlarını ve
açıklamalarını taşır.

`.env.bak*` desenleri de yoksayılır. Bunlar token yenileme akışının ürettiği
yedeklerdir ve **eski token'ı taşırlar** — eski token yenisi üretilince
otomatik geçersiz olmaz.

## Log redaksiyonu

Sırlar loglara iki yoldan sızabilir ve ikisi de gerçekten yaşandı:

1. `requests` istisna metni tam URL'yi gömer. Telegram token'ı URL yolunda
   taşındığı için istisna mesajı token'ı da yazar.
2. MCP sunucusu başlangıç adresini loglarsa, paylaşılan sır URL yolunda olduğu
   için o da loga düşer.

Çözüm `src/log_redaction.py`: kök logger'a takılan bir filtre.
`bot<id>:<token>`, `/mcp/<sır>` ve `?access_token=` / `?api_key=` gibi sorgu
parametrelerini maskeler.

**Filtre otomatik kurulmaz — giriş noktası kurar.** `main.py`,
`src/mcp_server.py` ve `src/telegram_bot.py` başlangıçta `install()` çağırır.
Uygulamayı bunları atlayarak (örneğin `create_app`'i doğrudan import ederek)
çalıştırırsanız **redaksiyon devrede olmaz**. Kendi betiğinizi yazıyorsanız
filtreyi kendiniz kurun:

```python
from src.log_redaction import install as install_log_redaction
install_log_redaction()
```

## Panel

- Yalnızca `127.0.0.1`'e bağlanır; `DASHBOARD_HOST` bilinçli olarak sabittir.
- İnternete açmak için Cloudflare Tunnel kullanılır —
  bkz. [docs/dagitim.md](docs/dagitim.md).
- **Açmadan önce `DASHBOARD_PASSWORD` ve `DASHBOARD_SECRET_KEY` doldurun.**
  Şifre boşken giriş ekranı devre dışı kalır; bu yerelde sorun değildir ama
  dışa açılmış bir panelde link'i bilen herkes paylaşımları yönetebilir.
  `deploy/setup_vps.sh` şifre boşsa uyarır ve 10 saniye bekler.

## Token kapsamı

`INSTAGRAM_ACCESS_TOKEN` hesabın yayın ve okuma yetkisini taşır. Sızarsa
hesap adına paylaşım yapılabilir. Şüphe halinde Meta uygulama ayarlarından
geçersiz kılıp `scripts/setup_instagram_token.py` ile yenisini üretin.
