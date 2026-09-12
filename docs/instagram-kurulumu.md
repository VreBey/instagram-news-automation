# Instagram hesap kurulumu

[← README](../README.md)

Token alma ve yenileme adımları. Bunlar tek seferlik işlemler olduğu için
ana README'den ayrıldı.

### Instagram token'ını yenileme (izin doğrulamalı)

```bash
python scripts/setup_instagram_token.py
```

Kısa ömürlü token'ı gizli olarak sorar, uzun ömürlüye (60 gün) çevirir ve
`.env`'i günceller. **Gerekli izinler eksikse `.env`'i değiştirmez** —
`instagram_manage_insights` olmadan performans verisi sessizce toplanmaz,
ki 4 Ağustos 2026'ya kadar tam olarak bu yaşandı.

Kısa ömürlü token'ı [Graph API Explorer](https://developers.facebook.com/tools/explorer)
üzerinden alın: uygulamanızı seçin → **Permissions** listesine
`instagram_manage_insights` ekleyin → *Generate Access Token*.

### Instagram kimlik bilgileri alma

1. [Meta for Developers](https://developers.facebook.com/) üzerinde bir uygulama oluşturun, Instagram Graph API ürününü ekleyin.
2. Instagram hesabınızı bir Facebook Sayfası'na bağlayın (Graph API bunu gerektirir).
3. Graph API Explorer veya OAuth akışıyla bir kısa ömürlü token alıp
   uzun ömürlü token'a dönüştürün — bu ilk dönüştürme elle yapılmalıdır.
4. `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, `INSTAGRAM_APP_ID`,
   `INSTAGRAM_APP_SECRET` değerlerini `.env`'e girin.
5. Bundan sonrasını `src/token_manager.py` üstlenir: token süresi
   `TOKEN_REFRESH_WARNING_DAYS` (varsayılan 10 gün) içine girince otomatik
   yenilenir ve veritabanında (`app_settings` tablosu) saklanır — `.env`
   dosyası tekrar düzenlenmez.

