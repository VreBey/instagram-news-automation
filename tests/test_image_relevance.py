"""_gorsel_alakali_mi ve _get_background'a entegrasyonu.

DEVRE DIŞI (11 Ağustos 2026). İlk kalibrasyon (10 Ağustos, 5 örnek: 3 doğru
+ 2 kasıtlı yanlış) doğru eşleşmeleri 0.133-0.197, yanlışları 0.063-0.069
vermişti. Production'a alınınca İLK turda 4 GERÇEK, alakalı görsel
yanlışlıkla reddedildi (Wuthering Waves/Onimusha/Aion2 — hepsi kendi
oyununun ekran görüntüsüydü) ve skorları 0.047-0.094 çıktı: "doğru"
örneklerin gerçek dağılımı, ilk kalibrasyondaki "yanlış" aralığıyla
ÇAKIŞIYOR. Sabit bir eşikle bu iki durumu ayırmak güvenilir değil.

Fonksiyon SİLİNMEDİ, sadece `return True` ile devre dışı bırakıldı —
kalibrasyon düzeltilirse geri açılabilir. Zincir mantığı (alakasız
sayılırsa bir sonraki basamağa düş) da bilerek KORUNDU ve test ediliyor;
`_gorsel_alakali_mi`'nin varsayılan uygulaması değişse bile o mantık
sağlam kalmalı.
"""

import src.nvidia_embed as nvembed
from src.image_generator import ImageGenerator


def _gen(tmp_db):
    return ImageGenerator(db=tmp_db)


def test_gorsel_alakali_mi_is_disabled_and_always_true(tmp_db, monkeypatch):
    """
    Yanlış kalibrasyon kanıtlanmış zarara yol açtığı için fonksiyon devre
    dışı — hangi embedding gelirse gelsin (hatta 'alakasız' olması
    ölçülen) True dönmeli.
    """
    gen = _gen(tmp_db)
    monkeypatch.setattr(nvembed, "embed_image", lambda *a, **k: [1.0, 0.0])
    monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: [0.0, 1.0])  # ortogonal, "alakasız"

    assert gen._gorsel_alakali_mi("yol.jpg", "herhangi bir başlık") is True


def test_gorsel_alakali_mi_does_not_call_nvidia_at_all(tmp_db):
    """
    Devre dışı bırakma erken `return True` ile yapıldı — fonksiyon artık
    hiçbir NVIDIA çağrısı yapmamalı (boşuna ağ trafiği/gecikme olmasın).
    """
    gen = _gen(tmp_db)
    cagrildi = []
    import src.nvidia_embed as ne
    ne_embed_image_orig = ne.embed_image
    ne.embed_image = lambda *a, **k: cagrildi.append(1)
    try:
        gen._gorsel_alakali_mi("yol.jpg", "bir başlık")
    finally:
        ne.embed_image = ne_embed_image_orig

    assert cagrildi == [], "devre dışı fonksiyon yine de NVIDIA'ya gitti"


def test_no_relevance_text_means_fail_open(tmp_db):
    """relevance_text verilmese de True dönmeli (fail-open, davranış aynı)."""
    gen = _gen(tmp_db)
    assert gen._gorsel_alakali_mi("herhangi/bir/yol.jpg", None) is True


def test_get_background_skips_rejected_article_image_and_falls_through(
    tmp_db, monkeypatch, tmp_path
):
    """
    ZİNCİR MANTIĞI hâlâ sağlam: `_gorsel_alakali_mi` (monkeypatch'lenerek)
    False dönerse zincir bir sonraki basamağa (og_image) düşmeli. Bu,
    varsayılan uygulama devre dışı olsa bile korunması gereken davranış —
    kalibrasyon düzeltilip fonksiyon geri açıldığında buraya güvenilecek.
    """
    from PIL import Image
    import src.image_generator as ig

    gen = _gen(tmp_db)

    kotu_gorsel = tmp_path / "kotu.jpg"
    Image.new("RGB", (800, 600), (10, 10, 10)).save(kotu_gorsel)
    iyi_gorsel = tmp_path / "iyi.jpg"
    Image.new("RGB", (800, 600), (200, 200, 200)).save(iyi_gorsel)

    monkeypatch.setattr(ig, "fetch_article_photo", lambda url: str(kotu_gorsel))
    monkeypatch.setattr(ig, "fetch_article_photo_from_page", lambda url: str(iyi_gorsel))

    # article_image alakasız, og_image alakalı sayılsın (elle simüle).
    def sahte_alaka(self, photo_path, relevance_text):
        return photo_path == str(iyi_gorsel)

    monkeypatch.setattr(ig.ImageGenerator, "_gorsel_alakali_mi", sahte_alaka)

    gen._get_background(
        (1080, 1350), "gaming", seed=1,
        image_url="https://ornek/gorsel.jpg",
        news_url="https://ornek/haber",
        relevance_text="bir haber başlığı",
    )

    assert gen._last_background_source == "og_image", \
        "alakasız article_image kullanıldı, zincire devam edilmedi"


def test_get_background_uses_article_image_by_default_now(tmp_db, monkeypatch, tmp_path):
    """
    UÇTAN UCA, GERÇEK (mock'lanmamış) `_gorsel_alakali_mi` ile: devre dışı
    olduğu için article_image HER ZAMAN kabul edilmeli, relevance_text
    verilse bile.
    """
    from PIL import Image
    import src.image_generator as ig

    gen = _gen(tmp_db)
    gorsel = tmp_path / "iyi.jpg"
    Image.new("RGB", (800, 600), (200, 200, 200)).save(gorsel)

    monkeypatch.setattr(ig, "fetch_article_photo", lambda url: str(gorsel))

    gen._get_background(
        (1080, 1350), "gaming", seed=1,
        image_url="https://ornek/gorsel.jpg", relevance_text="alakasız görünse bile kabul edilmeli",
    )

    assert gen._last_background_source == "article_image"


def test_game_cover_step_never_called_nvidia(tmp_db, monkeypatch, tmp_path):
    """game_cover zaten hiçbir zaman alaka kontrolünden geçmiyordu — korunuyor."""
    from PIL import Image
    import src.image_generator as ig

    gen = _gen(tmp_db)
    kapak = tmp_path / "kapak.jpg"
    Image.new("RGB", (600, 900), (50, 50, 50)).save(kapak)

    monkeypatch.setattr(ig, "fetch_game_cover_art", lambda title: str(kapak))
    cagrildi = []
    monkeypatch.setattr(nvembed, "embed_image", lambda *a, **k: cagrildi.append(1))

    gen._get_background(
        (1080, 1350), "gaming", seed=1, game_title="Bir Oyun",
        relevance_text="ilgisiz bir başlık",
    )

    assert gen._last_background_source == "game_cover"
    assert cagrildi == []


def test_stock_photo_step_never_called_nvidia(tmp_db, monkeypatch, tmp_path):
    """stock_photo zaten hiçbir zaman alaka kontrolünden geçmiyordu — korunuyor."""
    from PIL import Image
    import src.image_generator as ig

    gen = _gen(tmp_db)
    stok = tmp_path / "stok.jpg"
    Image.new("RGB", (800, 600), (80, 80, 80)).save(stok)

    monkeypatch.setattr(ig, "fetch_stock_photo", lambda category, seed: str(stok))
    cagrildi = []
    monkeypatch.setattr(nvembed, "embed_image", lambda *a, **k: cagrildi.append(1))

    gen._get_background(
        (1080, 1350), "gaming", seed=1, relevance_text="ilgisiz bir başlık",
    )

    assert gen._last_background_source == "stock_photo"
    assert cagrildi == []
