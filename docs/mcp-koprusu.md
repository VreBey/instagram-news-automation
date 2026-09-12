# Gemini Spark köprüsü (MCP)

[← README](../README.md)

Harici bir AI ajanının sisteme araştırma bulgusu göndermesi için kurulan
MCP sunucusu. Opsiyoneldir; kurulmazsa sistemin geri kalanı etkilenmez.

## Nasıl kurulur

Bu proje, ayrı bir Gemini Spark ajanının günlük araştırma bulgularını otomatik
olarak alabilmesi için bir MCP (Model Context Protocol) sunucusu içerir
([src/mcp_server.py](../src/mcp_server.py)). Gemini'nin "Bağlı Uygulamalar" özelliği
sadece **public HTTPS + Streamable HTTP transport** olan MCP sunucularını kabul
ettiğinden, bu sunucuyu Cloudflare Tunnel ile dışa açmanız gerekir.

### 1. Sunucuyu başlatın

```bash
python main.py --mcp-server
```

Varsayılan olarak `http://127.0.0.1:8765/mcp/<MCP_SHARED_SECRET>` adresinde dinler.
`.env`'e `MCP_SHARED_SECRET` girmeyi unutmayın — bu, URL'yi bilmeyen kimsenin
`add_research_finding` tool'unu çağıramamasını sağlayan tek koruma katmanıdır:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2. Cloudflare Tunnel ile dışa açın

```bash
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8765
```

Bu komut hızlı/geçici bir `https://xxxx.trycloudflare.com` URL'si verir (her
çalıştırmada değişir — ilk testler için yeterlidir). Kalıcı bir adres isterseniz,
kendi Cloudflare hesabınıza ekli bir domain ile adlandırılmış bir tünel
(`cloudflared tunnel create ...` + DNS CNAME kaydı) kurmanız gerekir — bu adım
kendi Cloudflare hesap/domain sahipliğinizi gerektirdiğinden burada otomatikleştirilmedi.

### 3. Gemini'ye bağlayın

Gemini'de **Ayarlar → Bağlı Uygulamalar → Özel bağlı uygulama oluştur**'a gidip
tam URL'yi girin:

```
https://<tünel-adresiniz>/mcp/<MCP_SHARED_SECRET>
```

OAuth alanlarını boş bırakın — bu ilk sürüm OAuth kullanmıyor (aşağıdaki güvenlik
notuna bakın).

### 4. Sohbette gerçekten çağırma (önemli — deneme yanılmayla bulundu)

Aracı sadece düz metinde adıyla anmak ("add_research_finding aracını kullanarak...")
**çalışmıyor** — Gemini "böyle bir araç tanımlı değil" diyor. Doğru yöntem:

1. Mesaj kutusuna **`@`** yazıp ardından uygulama adını yazın: `@Instagram Otomasyon Research Bridge`
2. Gemini bir **izin kartı** gösterir: "Let Gemini use '...' from Instagram Otomasyon Research Bridge" + hangi tool'un kullanılacağını gösteren bir seçici.
3. **Allow**'a basın.
4. Gemini aracı çağırır ve sonucu (kayıt ID'si dahil) sohbette gösterir.

Bu, hem düz Gemini sohbetinde hem de Spark görevlerinde denendi — yalnızca
`@` ile açıkça etiketleyip "Allow" ile onaylandığında çalıştı; @ menüsünde
özel uygulamalar listelenmiyor ama adını yazmaya devam edince kart çıkıyor.

### 5. Sonucu görün

Başarıyla gönderilen bulgular dashboard'daki **🔬 Spark Araştırmaları** sayfasında
görünür. AI veya Gaming ile ilgili bir bulguyu "Haberlere Aktar" ile
`news_items`'a taşıyıp normal Instagram içerik pipeline'ına sokabilirsiniz.

### Güvenlik notu

- Bu ilk sürümde OAuth **yok** — koruma tamamen URL'ye gömülü `MCP_SHARED_SECRET`'a
  ve tünel adresinin gizliliğine dayanıyor. Bu tam URL'yi kimseyle paylaşmayın.
- MCP kütüphanesinin varsayılan DNS-rebinding koruması (Host header doğrulaması)
  Cloudflare Tunnel ile uyumsuz olduğundan (`421 Misdirected Request` hatası verir)
  [src/mcp_server.py](../src/mcp_server.py) içinde bilinçli olarak kapatıldı — gerçek
  erişim kontrolü zaten yukarıdaki paylaşılan sır ile sağlanıyor.
- Sızma durumunda en kötü ihtimal: `external_research` tablosuna sahte satırlar
  eklenir. Bunlar **Instagram'a otomatik yayınlanmaz** — yalnızca manuel
  "Haberlere Aktar" adımıyla pipeline'a girebilir.
- Gemini bağlantı sırasında OAuth alanlarını zorunlu kılarsa (konsol/uygulama
  hata verirse), bu bir sonraki iterasyon için sinyaldir — `FastMCP`'nin
  `auth`/`token_verifier` desteğiyle eklenebilir.

