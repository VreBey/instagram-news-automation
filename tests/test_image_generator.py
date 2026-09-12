"""
ImageGenerator için duman (smoke) testleri.
Piksel-mükemmel doğrulama yapılmaz — sadece dosyanın oluştuğu, açılabildiği
ve beklenen boyutta olduğu kontrol edilir. generate_post_image() gerçek
output/posts/ dizinine yazar (mevcut --test komutuyla aynı davranış).
"""

import pytest
from PIL import Image

from config import POST_SIZE_PORTRAIT, POST_LAYOUT_VARIANTS
from src.image_generator import ImageGenerator
from src.image_layouts import LAYOUTS


@pytest.fixture
def img_gen(tmp_db):
    return ImageGenerator(db=tmp_db)


def test_select_layout_variant_is_deterministic(img_gen):
    first = img_gen._select_layout_variant(5)
    second = img_gen._select_layout_variant(5)
    assert first == second
    assert first in POST_LAYOUT_VARIANTS


def test_select_layout_variant_covers_all_options(img_gen):
    seen = {img_gen._select_layout_variant(i) for i in range(len(POST_LAYOUT_VARIANTS))}
    assert seen == set(POST_LAYOUT_VARIANTS)


def test_every_post_layout_variant_has_a_registered_render_function():
    """
    config.POST_LAYOUT_VARIANTS'a yeni bir isim eklenip src/image_layouts/
    altında karşılık gelen modül unutulursa generate_post_image() sessizce
    KeyError ile patlar — bu tutarlılığı burada erkenden yakalıyoruz.
    """
    assert set(POST_LAYOUT_VARIANTS) == set(LAYOUTS.keys())


@pytest.mark.parametrize("layout_name", list(LAYOUTS.keys()))
def test_layout_variants_produce_valid_image(img_gen, layout_name):
    render = LAYOUTS[layout_name]
    img = render(img_gen, POST_SIZE_PORTRAIT, "ai", "Test Başlığı Burada Uzunca Bir Metin", "Test Kaynağı")
    assert isinstance(img, Image.Image)
    assert img.size == POST_SIZE_PORTRAIT


def test_generate_post_image_creates_valid_file(img_gen):
    news_data = {
        "category": "gaming",
        "summary_text": "GTA 6 Çıkış Tarihi Açıklandı",
        "source_name": "IGN",
    }
    path = img_gen.generate_post_image(news_data=news_data)
    assert path is not None

    with Image.open(path) as img:
        assert img.size == POST_SIZE_PORTRAIT


def test_generate_post_image_uses_carousel_when_list_items_present(img_gen, tmp_db):
    """
    Kullanıcı geri bildirimi: "daha fazla detay vermek gerekirse feed
    içerisinde kaydırmalı şekilde içerikler yapılabilmeli". list_items
    tespit edilmiş bir içerik için tekil görsel yerine çok slaytlı carousel
    üretilmeli ve carousel_paths veritabanına yazılmalı.
    """
    news_id = tmp_db.add_news(title="Ağustos Oyunları", url="https://example.com/carousel-list", category="gaming")
    tmp_db.mark_news_processed(news_id, relevance_score=0.9)
    content_id = tmp_db.add_content(
        news_id=news_id, content_type="post", caption="c",
        list_items={
            "cover": "Ağustos'un en büyük oyunları!",
            "items": [
                {"name": "Beast of Reincarnation", "detail": "5 Ağustos'ta çıkıyor"},
                {"name": "Star Wars: Zero Company", "detail": "6 Ağustos'ta çıkıyor"},
            ],
        },
    )

    path = img_gen.generate_post_image(content_id=content_id)

    assert path is not None
    with Image.open(path) as img:
        assert img.size == POST_SIZE_PORTRAIT

    updated = tmp_db.get_content_by_id(content_id)
    assert updated["media_path"] == path
    assert len(updated["carousel_paths"]) == 3  # kapak + 2 öge
    for slide_path in updated["carousel_paths"]:
        with Image.open(slide_path) as img:
            assert img.size == POST_SIZE_PORTRAIT


def test_generate_post_image_ignores_list_items_for_news_data_path(img_gen):
    """content_id olmadan (news_data ile) çağrıldığında carousel dalı hiç
    tetiklenmemeli — carousel kaydı content_id gerektirir."""
    news_data = {
        "category": "ai",
        "summary_text": "Tekil haber",
        "list_items": {"cover": "x", "items": [{"name": "a", "detail": "b"}, {"name": "c", "detail": "d"}]},
    }
    path = img_gen.generate_post_image(news_data=news_data)
    assert path is not None
    with Image.open(path) as img:
        assert img.size == POST_SIZE_PORTRAIT


def test_get_background_falls_back_to_mesh_without_stock_photo(img_gen, mocker):
    mocker.patch("src.image_generator.fetch_stock_photo", return_value=None)
    img = img_gen._get_background(POST_SIZE_PORTRAIT, "ai", seed=1)
    assert isinstance(img, Image.Image)
    assert img.size == POST_SIZE_PORTRAIT


def test_get_background_with_seed_none_skips_stock_photo(img_gen, mocker):
    fetch_mock = mocker.patch("src.image_generator.fetch_stock_photo")
    img = img_gen._get_background(POST_SIZE_PORTRAIT, "ai", seed=None)
    assert isinstance(img, Image.Image)
    fetch_mock.assert_not_called()


def test_get_background_uses_photo_when_available(img_gen, mocker, tmp_path):
    # Basit tek renkli bir "stok fotoğraf" oluştur
    fake_photo_path = tmp_path / "fake_stock.jpg"
    Image.new("RGB", (800, 1200), (120, 80, 200)).save(fake_photo_path, "JPEG")

    mocker.patch("src.image_generator.fetch_stock_photo", return_value=str(fake_photo_path))
    img = img_gen._get_background(POST_SIZE_PORTRAIT, "ai", seed=1)

    assert isinstance(img, Image.Image)
    assert img.size == POST_SIZE_PORTRAIT


def test_draw_glow_text_returns_updated_image_and_draw(img_gen):
    from PIL import ImageDraw

    img = Image.new("RGB", POST_SIZE_PORTRAIT, (10, 10, 10))
    font = img_gen._get_font("accent", 40)
    new_img, new_draw = img_gen._draw_glow_text(
        img, (50, 50), ["Test Başlık"], font,
        fill=(255, 255, 255), glow_color=(108, 92, 231), line_height=50
    )
    assert isinstance(new_img, Image.Image)
    assert isinstance(new_draw, ImageDraw.ImageDraw)
    assert new_img.size == img.size


def test_draw_letter_spaced_advances_x_position(img_gen):
    from PIL import ImageDraw

    img = Image.new("RGB", (400, 100), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = img_gen._get_font("subtitle", 24)
    end_x = img_gen._draw_letter_spaced(draw, (10, 10), "AI", font, (255, 255, 255), spacing=5)
    assert end_x > 10


def test_get_background_prefers_manual_image_over_everything(img_gen, mocker, tmp_path):
    manual_path = tmp_path / "manual.jpg"
    Image.new("RGB", (800, 1200), (10, 200, 10)).save(manual_path, "JPEG")

    article_mock = mocker.patch("src.image_generator.fetch_article_photo")
    pexels_mock = mocker.patch("src.image_generator.fetch_stock_photo")

    img = img_gen._get_background(
        POST_SIZE_PORTRAIT, "ai", seed=1,
        image_url="https://example.com/a.jpg", manual_image_path=str(manual_path)
    )

    assert isinstance(img, Image.Image)
    assert img.size == POST_SIZE_PORTRAIT
    article_mock.assert_not_called()
    pexels_mock.assert_not_called()


def test_get_background_prefers_article_photo_over_pexels(img_gen, mocker, tmp_path):
    article_path = tmp_path / "article.jpg"
    Image.new("RGB", (800, 1200), (200, 10, 10)).save(article_path, "JPEG")

    mocker.patch("src.image_generator.fetch_article_photo", return_value=str(article_path))
    pexels_mock = mocker.patch("src.image_generator.fetch_stock_photo")

    img = img_gen._get_background(
        POST_SIZE_PORTRAIT, "ai", seed=1, image_url="https://example.com/a.jpg"
    )

    assert isinstance(img, Image.Image)
    pexels_mock.assert_not_called()


def test_get_background_falls_back_to_pexels_when_article_photo_missing(img_gen, mocker, tmp_path):
    pexels_path = tmp_path / "pexels.jpg"
    Image.new("RGB", (800, 1200), (10, 10, 200)).save(pexels_path, "JPEG")

    mocker.patch("src.image_generator.fetch_article_photo", return_value=None)
    pexels_mock = mocker.patch("src.image_generator.fetch_stock_photo", return_value=str(pexels_path))

    img = img_gen._get_background(
        POST_SIZE_PORTRAIT, "ai", seed=1, image_url="https://example.com/a.jpg"
    )

    assert isinstance(img, Image.Image)
    pexels_mock.assert_called_once()


def _relative_luminance(rgb):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def test_photo_background_darkens_headline_band_enough_for_white_text(img_gen, tmp_path):
    """
    Baslik bandi beyaz metin icin yeterince koyu olmali.

    Gercek kusur: scrim yalnizca ust %35 ve alt %55'i karartiyordu, baslik
    ise tam aradaki AYDINLIK banda dusuyordu — parlak bir kapak gorseli
    (or. Cyberpunk 2077'nin sari sanati) uzerinde beyaz metin okunamaz hale
    geliyordu. WCAG AA buyuk metin esigi 3.0; burada guvenli tarafta kalmak
    icin 4.5 araniyor.
    """
    bright = tmp_path / "bright.jpg"
    Image.new("RGB", (1200, 1500), (250, 240, 60)).save(bright, "JPEG")  # parlak sari

    img = img_gen._compose_photo_background(POST_SIZE_PORTRAIT, str(bright), "gaming")

    width, height = img.size
    band = img.crop((0, int(height * 0.42), width, int(height * 0.58)))
    pixels = list(band.get_flattened_data())
    avg = tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))

    lum = _relative_luminance(avg)
    contrast = (1.0 + 0.05) / (lum + 0.05)  # beyaz metne karsi
    assert contrast >= 4.5, f"baslik bandi kontrasti yetersiz: {contrast:.2f} (arkaplan {avg})"


def test_photo_background_preserves_source_image_identity(img_gen, tmp_path):
    """
    Marka tonu gercek gorseli EZMEMELI.

    Onceden 0.22 alpha ile kategori rengi kariştiriliyordu ve her oyun
    gorseli ayni camurlu yesile donusuyordu ("oyunlarda hep ayni gorsel"
    geri bildirimi). Ust bolge (scrim'in en az mudahale ettigi yer) hala
    kaynak gorselin baskin rengini tasimali.
    """
    red = tmp_path / "red.jpg"
    Image.new("RGB", (1200, 1500), (220, 40, 40)).save(red, "JPEG")

    img = img_gen._compose_photo_background(POST_SIZE_PORTRAIT, str(red), "gaming")

    width, height = img.size
    top = img.crop((0, int(height * 0.16), width, int(height * 0.22)))
    pixels = list(top.get_flattened_data())
    avg = tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))

    assert avg[0] > avg[1] + 40 and avg[0] > avg[2] + 40, (
        f"kaynak gorselin kirmizi kimligi kaybolmus: {avg}"
    )


def test_get_background_tries_game_cover_before_pexels_for_gaming(img_gen, mocker, tmp_path):
    """
    Kullanıcı geri bildirimi: gaming içerikler hep aynı jenerik Pexels stok
    fotoğrafını kullanıyordu. Haberin kendi görseli yoksa, Pexels'ten önce
    başlıktan tahmin edilen oyunun GERÇEK Steam kapak görseli denenmeli.
    """
    cover_path = tmp_path / "cover.jpg"
    Image.new("RGB", (600, 900), (5, 5, 5)).save(cover_path, "JPEG")

    mocker.patch("src.image_generator.fetch_article_photo", return_value=None)
    cover_mock = mocker.patch("src.image_generator.fetch_game_cover_art", return_value=str(cover_path))
    pexels_mock = mocker.patch("src.image_generator.fetch_stock_photo")

    img = img_gen._get_background(
        POST_SIZE_PORTRAIT, "gaming", seed=1,
        image_url="https://example.com/a.jpg", game_title="Grand Theft Auto 6 delayed"
    )

    assert isinstance(img, Image.Image)
    cover_mock.assert_called_once_with("Grand Theft Auto 6 delayed")
    pexels_mock.assert_not_called()


def test_get_background_falls_back_to_pexels_when_no_game_cover_found(img_gen, mocker, tmp_path):
    pexels_path = tmp_path / "pexels.jpg"
    Image.new("RGB", (800, 1200), (10, 10, 200)).save(pexels_path, "JPEG")

    mocker.patch("src.image_generator.fetch_article_photo", return_value=None)
    mocker.patch("src.image_generator.fetch_game_cover_art", return_value=None)
    pexels_mock = mocker.patch("src.image_generator.fetch_stock_photo", return_value=str(pexels_path))

    img = img_gen._get_background(
        POST_SIZE_PORTRAIT, "gaming", seed=1, game_title="some obscure headline"
    )

    assert isinstance(img, Image.Image)
    pexels_mock.assert_called_once()


def test_get_background_skips_game_cover_for_non_gaming_category(img_gen, mocker):
    cover_mock = mocker.patch("src.image_generator.fetch_game_cover_art")
    mocker.patch("src.image_generator.fetch_stock_photo", return_value=None)

    img_gen._get_background(POST_SIZE_PORTRAIT, "ai", seed=1, game_title="GPT-5 announced")

    cover_mock.assert_not_called()


def test_get_background_skips_game_cover_without_title(img_gen, mocker):
    cover_mock = mocker.patch("src.image_generator.fetch_game_cover_art")
    mocker.patch("src.image_generator.fetch_stock_photo", return_value=None)

    img_gen._get_background(POST_SIZE_PORTRAIT, "gaming", seed=1)

    cover_mock.assert_not_called()


def test_draw_headline_block_highlights_last_line(img_gen):
    img = Image.new("RGB", POST_SIZE_PORTRAIT, (10, 10, 10))
    font = img_gen._get_font("accent", 40)
    new_img, new_draw = img_gen._draw_headline_block(
        img, (50, 50), ["İlk Satır", "Son Satır"], font,
        fill=(255, 255, 255), glow_color=(108, 92, 231), line_height=50,
        category="ai", subtitle="Kısa bir alt açıklama"
    )
    assert isinstance(new_img, Image.Image)
    assert new_img.size == img.size
