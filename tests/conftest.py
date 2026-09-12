"""Pytest ortak fixture'ları."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.database import Database


@pytest.fixture
def tmp_db(tmp_path) -> Database:
    """Her testin kendi izole SQLite dosyasını kullandığı Database örneği."""
    db_path = tmp_path / "test_news.db"
    return Database(db_path=str(db_path))


@pytest.fixture(autouse=True)
def _ag_kapali(request, monkeypatch):
    """Testler VARSAYILAN olarak dış dünyaya çıkmasın.

    Ölçüm (8 Ağustos 2026): soket kapatılarak çalıştırıldığında 20 test
    başarısız oluyordu — görsel/hikâye/zamanlayıcı testleri Pexels'ten ve
    haber sitelerinden GERÇEK fotoğraf indiriyordu.

    İki sorun:
      * Testler ağa ve üçüncü taraf servislere bağımlı; o servis yavaşsa
        ya da görseli değiştirirse test sebepsiz kırılır.
      * Sessiz bozulma: bir görsel gelmezse zincir stok/gradient'e
        düşüyor ve test yine GEÇİYOR — yani neyi doğruladığı belirsiz.

    Ağsız çalışan paket ayrıca ~90 saniye daha hızlı (275 sn → 188 sn).

    Zinciri asıl konu edinen testler bu isimleri kendileri
    monkeypatch'liyor; sonradan uygulanan yama bunun üstüne yazıyor.
    Getiricilerin KENDİSİNİ sınayan testler (`test_stock_photos` vb.)
    `src.stock_photos` gibi kaynak modülü doğrudan kullandığı için
    etkilenmiyor.
    """
    import src.image_generator as ig

    for ad in ("fetch_stock_photo", "fetch_article_photo",
               "fetch_article_photo_from_page", "fetch_game_cover_art"):
        if hasattr(ig, ad):
            monkeypatch.setattr(ig, ad, lambda *a, **k: None)

    # NVIDIA embedding (dedup ikinci basamağı + görsel-alaka kontrolü):
    # `database.find_similar_recent` ve `image_generator._gorsel_alakali_mi`
    # bunları ÇAĞRI ANINDA `src.nvidia_embed`'den import ediyor (local
    # import), bu yüzden tek patch noktası kaynak modülün kendisi — hangi
    # dosyadan çağrılırsa çağrılsın etkili olur. Varsayılan None: "API
    # yanıt vermedi" (fail-open) yolunu ölçer, gerçek NVIDIA API'sine
    # gitmez.
    #
    # `test_nvidia_embed` bu fonksiyonların KENDİSİNİ sınıyor (kendi
    # `requests.post` sahtesini kuruyor) — orada yamalamak testi
    # anlamsızlaştırırdı, `test_token_manager` istisnasıyla aynı gerekçe.
    if not request.node.module.__name__.endswith("test_nvidia_embed"):
        import src.nvidia_embed as nvembed

        monkeypatch.setattr(nvembed, "embed_text", lambda *a, **k: None)
        monkeypatch.setattr(nvembed, "embed_image", lambda *a, **k: None)

    # `Scheduler.__init__` kurulum anında Meta'ya GERÇEK istek atıyor
    # (`token_manager.check_and_refresh_if_needed`). Bir Scheduler kuran her
    # test bu yüzden ağa çıkıyordu.
    #
    # `test_token_manager` bu metodun KENDİSİNİ sınıyor ve kendi sahtelerini
    # kuruyor — orada yamalamak testi anlamsızlaştırırdı.
    if request.node.module.__name__.endswith("test_token_manager"):
        return

    import src.token_manager as tm

    monkeypatch.setattr(tm.TokenManager, "check_and_refresh_if_needed",
                        lambda self, *a, **k: None)


@pytest.fixture(autouse=True)
def _kilitler_tmp_dizinde(tmp_path, monkeypatch):
    """Testler GERÇEK kilit dizinine dokunmasın.

    `job_lock` çağıranların çoğu (scheduler.process_content,
    generate_all_media) `lock_dir` vermiyor; varsayılan `data/locks`.
    Paket bir kez çalıştırıldığında depoda `data/locks/medya_uretimi.lock`
    oluşuyordu.

    Asıl tehlike geliştirme makinesindeki çöp değil: aynı kod sunucuda
    `/opt/instagram-otomasyon/data/locks` kullanıyor. Orada test
    çalıştırılsaydı bir test CANLI zamanlayıcının kilidini kapıp medya
    üretimini sessizce durdurabilirdi.

    `lock_dir`'i açıkça veren testler bundan etkilenmiyor.
    """
    import src.joblock as joblock

    kilit_dizini = tmp_path / "locks"
    kilit_dizini.mkdir(exist_ok=True)
    monkeypatch.setattr(joblock, "LOCK_DIR", kilit_dizini)
