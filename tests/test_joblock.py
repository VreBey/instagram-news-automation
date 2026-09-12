"""Aynı iş iki kez birden çalışmasın.

1 Ağustos 2026: dokuz haber 2-27 dakika arayla İKİŞER kez işlendi, her biri
için mükerrer post+story çifti üretildi. 7 Ağustos denetiminde mekanizma
bulundu — `process_all_news` içindeki SELECT → Gemini → `is_processed=1`
penceresi haber başına dakikalarca açık ve aynı işi başlatabilen üç kapı var
(zamanlayıcı servisi, panelin /api/run/* uçları, elle CLI).

Aralığın 2'den 27 dakikaya BÜYÜMESİ eşzamanlılığın imzasıydı: iki çalışma
aynı Gemini kotasını tüketip birbirini geri çekilmeye zorluyor.
"""

import multiprocessing
import sys
from pathlib import Path

import pytest

from src.joblock import job_lock


def test_default_lock_dir_is_never_the_real_one(tmp_path):
    """
    Koruma: paket çalıştığında depodaki (ve sunucudaki) gerçek kilit
    dizinine dosya YAZILMAMALI. `job_lock` çağıranların çoğu `lock_dir`
    vermiyor; conftest'teki autouse fixture varsayılanı tmp'ye çeviriyor.

    Bir testin canlı zamanlayıcının kilidini kapması, medya üretimini
    sessizce durdururdu.
    """
    import src.joblock as joblock

    gercek = Path(joblock.__file__).resolve().parent.parent / "data" / "locks"
    assert Path(joblock.LOCK_DIR).resolve() != gercek.resolve(), \
        "testler gerçek kilit dizinini kullanıyor"

    # `lock_dir` VERMEDEN çağır — üretim kodunun yaptığı gibi.
    with job_lock("medya_uretimi") as alindi:
        assert alindi is True

    assert (Path(joblock.LOCK_DIR) / "medya_uretimi.lock").exists(), \
        "kilit dosyası tmp dizininde oluşmadı"


def test_second_caller_is_refused(tmp_path):
    with job_lock("is", lock_dir=tmp_path) as birinci:
        assert birinci is True
        with job_lock("is", lock_dir=tmp_path) as ikinci:
            assert ikinci is False, "ikinci çağrı kilidi almamalıydı"


def test_lock_is_released_after_the_block(tmp_path):
    with job_lock("is", lock_dir=tmp_path) as alindi:
        assert alindi is True

    with job_lock("is", lock_dir=tmp_path) as tekrar:
        assert tekrar is True, "kilit blok bitince bırakılmalı"


def test_lock_is_released_even_on_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with job_lock("is", lock_dir=tmp_path) as alindi:
            assert alindi is True
            raise RuntimeError("bum")

    with job_lock("is", lock_dir=tmp_path) as tekrar:
        assert tekrar is True, "istisna sonrası kilit takılı kalmamalı"


def test_different_jobs_do_not_block_each_other(tmp_path):
    with job_lock("icerik_isleme", lock_dir=tmp_path) as a:
        with job_lock("medya_uretimi", lock_dir=tmp_path) as b:
            assert a is True and b is True


def _cocuk(dizin, kuyruk):
    from src.joblock import job_lock as kilit
    with kilit("is", lock_dir=dizin) as alindi:
        kuyruk.put(alindi)


@pytest.mark.skipif(sys.platform == "win32",
                    reason="fork tabanlı test; Windows'ta spawn ile import sorunlu")
def test_lock_works_across_processes(tmp_path):
    """
    ASIL MESELE bu: kilit süreçler arası çalışmazsa hiçbir işe yaramaz.
    Panel ve zamanlayıcı AYRI systemd servisleri — aynı süreçte değiller.
    """
    kuyruk = multiprocessing.Queue()
    with job_lock("is", lock_dir=tmp_path) as birinci:
        assert birinci is True
        p = multiprocessing.Process(target=_cocuk, args=(str(tmp_path), kuyruk))
        p.start()
        p.join(timeout=20)
        assert kuyruk.get(timeout=5) is False, "başka süreç kilidi alabildi"


# =============================================
# Zamanlayıcı entegrasyonu
# =============================================

def test_process_content_skips_when_locked(tmp_db, monkeypatch, tmp_path):
    from src.scheduler import Scheduler
    import src.joblock as joblock

    monkeypatch.setattr(joblock, "LOCK_DIR", tmp_path)
    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    cagrildi = []
    s.processor = type("P", (), {
        "process_all_news": lambda self: cagrildi.append(1) or {"processed": 1},
        "create_reels_content": lambda self, category=None: None,
    })()

    with job_lock("icerik_isleme", lock_dir=tmp_path):
        stats = s.process_content()

    assert stats.get("skipped_already_running") is True
    assert not cagrildi, "kilit tutuluyorken iş yine de çalıştı"


def test_generate_all_media_skips_when_locked(tmp_db, monkeypatch, tmp_path):
    from src.scheduler import Scheduler
    import src.joblock as joblock

    monkeypatch.setattr(joblock, "LOCK_DIR", tmp_path)
    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    cagrildi = []
    monkeypatch.setattr(Scheduler, "_generate_all_media",
                        lambda self: cagrildi.append(1) or {})

    with job_lock("medya_uretimi", lock_dir=tmp_path):
        stats = s.generate_all_media()

    assert stats.get("skipped_already_running") is True
    assert not cagrildi


def test_lock_is_free_in_the_normal_case(tmp_db, monkeypatch, tmp_path):
    """Kilit yokken iş normal çalışmalı — fren kalıcı olmamalı."""
    from src.scheduler import Scheduler
    import src.joblock as joblock

    monkeypatch.setattr(joblock, "LOCK_DIR", tmp_path)
    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    monkeypatch.setattr(Scheduler, "_generate_all_media", lambda self: {"posts": 3})

    assert s.generate_all_media() == {"posts": 3}
    assert s.generate_all_media() == {"posts": 3}, "kilit ikinci turda takılı kaldı"
