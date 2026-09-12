# Katkı / Contributing

Bu öncelikle kişisel bir projedir. Büyük bir değişiklik planlıyorsanız önce
issue açın — yön uyuşmazsa ikimizin de emeği boşa gitmesin.

> This is primarily a personal project. Open an issue before a large PR.

## Geliştirme ortamı

```bash
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/ -v
```

Testler gerçek ağ çağrısı yapmaz ve `.env` gerektirmez. Anahtar olmadan da
tamamı koşar.

## Kurallar

**Python 3.10 ve 3.12'de geçmeli.** 3.10 alt sınırdır
çünkü üretim orada çalışıyor — yalnızca yeni sürümde çalışan sözdizimi
kullanmayın.

**Kod, yorum ve commit mesajları Türkçe.** Depo baştan böyle yazıldı; karışık
dil okumayı zorlaştırır. İngilizce belgeler `README.en.md` ile sınırlı.

**Yorum "ne" değil "neden" anlatır.** Bu depodaki yorumların çoğu bir kararın
gerekçesini ya da geçmişte yaşanmış bir arızayı kaydeder. Örnek:

```python
# sin^0.72 → tepesi düzleşmiş kemer; sin^1 yarım daire verip şişiriyor
```

Bir sayıyı ya da eşiği değiştiriyorsanız neden değiştirdiğinizi yazın.
Ölçümle bulunmuş değerler için bkz. [docs/tasarim-kararlari.md](docs/tasarim-kararlari.md).

**Commit mesajı: ne yapıldığı + neden.** Tek satır özet, sonra gerekiyorsa
gövdede gerekçe. Emir kipi tercih edilir.

**Sır eklemeyin.** `.env` gitignore'da ve öyle kalmalı. Yeni bir yapılandırma
anahtarı eklerseniz `.env.example`'a **adını ve açıklamasını** ekleyin —
değerini değil. Eksik anahtarların sessizce özellik kapatması bu projede daha
önce yaşandı; boş bırakıldığında ne olacağını da yazın.

**Log'a sır yazdırmayın.** Redaksiyon filtresi ve sınırları için
[SECURITY.md](SECURITY.md).

## Neyi kabul etmem muhtemel

- Hata düzeltmesi, test eklemesi, belge düzeltmesi — memnuniyetle.
- Çoklu hesap desteği, Docker dağıtımı — kapsam dışı, bilinçli bir karar
  (bkz. [README > Bilinen sınırlamalar](README.md#bilinen-sınırlamalar)).
