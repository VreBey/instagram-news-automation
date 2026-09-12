"""Yeniden başlatmada kaçırılan günlük işlerin telafisi.

Neden var (4 Ağustos 2026): `schedule` kütüphanesi işleri süreç belleğinde
tutuyor. Servis yeniden başlayınca zamanlama sıfırdan kuruluyor ve saati
ÇOKTAN GEÇMİŞ günlük işler ertesi güne atılıyor — o gün hiç çalışmıyorlar.

Ölçülen etki: sekiz deploy sonrası "Medya Üretimi" ve "Günlük Bakım" o gün
hiç çalışmadı, "Telegram Onay Bildirimi" üç yerine iki kez çalıştı. Kullanıcı
bunu "Telegram'dan sadece 1 haber bildirimi geldi" olarak fark etti. Sistem
kararlıydı (sıfır çökme) ama eksik teslim ediyordu.
"""

from datetime import datetime, time as dtime, timedelta

import pytest
import schedule as schedule_lib


@pytest.fixture
def scheduler(tmp_db):
    from src.scheduler import Scheduler
    s = Scheduler.__new__(Scheduler)
    s.db = tmp_db
    return s


@pytest.fixture(autouse=True)
def _temiz_zamanlama():
    schedule_lib.clear()
    yield
    schedule_lib.clear()


def _kaydet(scheduler, saat: str, fn, ad: str):
    schedule_lib.every().day.at(saat).do(scheduler._safe_run, fn, ad)
    scheduler._tag_daily_jobs_with_slot()


# Saatler "%H:%M" olarak kaydediliyor, yani GÜN bilgisi yok. Basitçe
# now±90dk almak gece yarısı çevresinde testi ters çeviriyordu: 22:40'ta
# "gelecek" saat 00:10 olarak yazılıyor ve zamanlayıcı onu bugün ÇOKTAN
# GEÇMİŞ sayıyordu (test 22:30'dan sonra kendiliğinden kırmızıya dönüyordu,
# kodda hiçbir şey değişmeden). Bu yüzden saatler gün sınırına kırpılıyor.
def _gecmis_saat(dakika=90):
    hedef = datetime.now() - timedelta(minutes=dakika)
    if hedef.date() != datetime.now().date():
        hedef = datetime.now().replace(hour=0, minute=0)
    return hedef.strftime("%H:%M")


def _iki_gecmis_saat() -> tuple[str, str]:
    """Bugün içinde kalan, BİRBİRİNDEN FARKLI iki geçmiş saat.

    `_gecmis_saat(180)` ve `_gecmis_saat(60)` doğrudan kullanılamıyor: gece
    00:00–03:00 arasında ikisi de gün başına kırpılıp AYNI "00:00" değerini
    veriyor. O zaman iki ayrı slot tek slota düşüyor ve "ayrı ayrı telafi
    edilmeli" testi, kodda hiçbir şey değişmeden kırmızıya dönüyordu — yani
    gerçek bir hatayı değil, testin çalıştığı saati ölçüyordu.

    Burada gün başından itibaren mevcut olan aralık ikiye bölünüyor, böylece
    iki saat her koşulda farklı kalıyor.
    """
    simdi = datetime.now()
    gecen_dakika = simdi.hour * 60 + simdi.minute
    if gecen_dakika < 2:
        pytest.skip("gün başı: iki farklı geçmiş saat üretilemiyor")

    erken = simdi - timedelta(minutes=min(180, gecen_dakika))
    gec = simdi - timedelta(minutes=min(60, gecen_dakika // 2))
    return erken.strftime("%H:%M"), gec.strftime("%H:%M")


def _gelecek_saat(dakika=90):
    hedef = datetime.now() + timedelta(minutes=dakika)
    if hedef.date() != datetime.now().date():
        hedef = datetime.now().replace(hour=23, minute=59)
    return hedef.strftime("%H:%M")


@pytest.fixture(autouse=True)
def _gun_sinirinda_atla():
    """Günün ilk ve son dakikalarında "geçmiş/gelecek saat" tanımsız kalıyor."""
    simdi = datetime.now()
    if simdi.hour == 0 and simdi.minute == 0:
        pytest.skip("gün başı: geçmiş saat üretilemiyor")
    if simdi.hour == 23 and simdi.minute >= 59:
        pytest.skip("gün sonu: gelecek saat üretilemiyor")


# =============================================
# Telafi davranışı
# =============================================

def test_missed_job_is_caught_up(scheduler):
    calisti = []
    _kaydet(scheduler, _gecmis_saat(), lambda: calisti.append(1), "Medya Üretimi")

    telafi = scheduler.catch_up_missed_jobs()

    assert calisti == [1]
    assert len(telafi) == 1


def test_future_job_is_not_run_early(scheduler):
    """Saati gelmemiş işi normal zamanlayıcı çalıştıracak."""
    calisti = []
    _kaydet(scheduler, _gelecek_saat(), lambda: calisti.append(1), "Gece Gönderisi")

    scheduler.catch_up_missed_jobs()

    assert calisti == []


def test_already_run_job_is_not_repeated(scheduler):
    """Aynı gün ikinci kez açılışta iş tekrar çalışmamalı."""
    calisti = []
    _kaydet(scheduler, _gecmis_saat(), lambda: calisti.append(1), "Günlük Bakım")

    scheduler.catch_up_missed_jobs()
    scheduler.catch_up_missed_jobs()

    assert calisti == [1]


def test_yesterdays_run_does_not_block_today(scheduler, tmp_db):
    calisti = []
    _kaydet(scheduler, _gecmis_saat(), lambda: calisti.append(1), "Haber Toplama")
    dun = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    tmp_db.set_setting(scheduler._job_key("Haber Toplama", _gecmis_saat()), dun)

    scheduler.catch_up_missed_jobs()

    assert calisti == [1]


def test_failed_job_is_retried_next_restart(scheduler):
    """
    Kayıt yalnızca BAŞARILI çalışmada yazılıyor; patlayan bir iş bir sonraki
    açılışta yeniden denenmeli.
    """
    denemeler = []

    def patla():
        denemeler.append(1)
        raise RuntimeError("bum")

    _kaydet(scheduler, _gecmis_saat(), patla, "Kırılgan İş")

    scheduler.catch_up_missed_jobs()
    scheduler.catch_up_missed_jobs()

    assert len(denemeler) == 2


# =============================================
# Slot ayrımı — asıl incelik
# =============================================

def test_slots_are_tracked_separately(scheduler):
    """
    'Telegram Onay Bildirimi' günde üç kez, farklı saatlerde kayıtlı. Yalnızca
    ADA göre kayıt tutulsaydı sabahki çalışma öğlen ve akşamki turları da
    'çalıştı' saydırırdı — kullanıcının yaşadığı eksik bildirim tam da buydu.
    """
    calisti = []
    fn = lambda: calisti.append(1)
    erken, gec = _iki_gecmis_saat()
    assert erken != gec, "test kurulumu bozuk: iki slot ayni saatte"
    schedule_lib.every().day.at(erken).do(scheduler._safe_run, fn, "Bildirim")
    schedule_lib.every().day.at(gec).do(scheduler._safe_run, fn, "Bildirim")
    scheduler._tag_daily_jobs_with_slot()

    scheduler.catch_up_missed_jobs()

    assert len(calisti) == 2, "ayni adli iki slot ayri ayri telafi edilmeli"


def test_slot_is_derived_from_job_not_hardcoded(scheduler, tmp_db):
    """
    Saat işin kendisinden türetilmeli; elle yazılsaydı `.at()` değiştiğinde
    etiket eskide kalır ve iş her açılışta tekrar çalışırdı.
    """
    _kaydet(scheduler, "07:15", lambda: None, "Sabah İşi")
    job = schedule_lib.get_jobs()[0]
    assert job.job_func.args[2] == "07:15"


# =============================================
# Sık aralıklı işler etkilenmemeli
# =============================================

def test_interval_jobs_are_ignored(scheduler):
    """5 dakikada bir çalışan iş zaten kaçırılmıyor; telafiye girmemeli."""
    calisti = []
    schedule_lib.every(5).minutes.do(scheduler._safe_run, lambda: calisti.append(1),
                                     "Paylaşım Kontrolü")
    scheduler._tag_daily_jobs_with_slot()

    scheduler.catch_up_missed_jobs()

    assert calisti == []


def test_no_jobs_registered(scheduler):
    assert scheduler.catch_up_missed_jobs() == []


def test_missed_jobs_are_detected_without_running(scheduler):
    """
    Tespit ve çalıştırma ayrı olmalı: `run_scheduler` listeyi ana döngüye
    serpiştirerek çalıştırıyor, hepsini peş peşe değil.
    """
    calisti = []
    _kaydet(scheduler, _gecmis_saat(), lambda: calisti.append(1), "Ağır İş")

    eksik = scheduler.find_missed_jobs()

    assert len(eksik) == 1
    assert eksik[0][1] == "Ağır İş"
    assert calisti == [], "find_missed_jobs sadece TESPIT etmeli, calistirmamali"


def test_detection_returns_callable_and_slot(scheduler):
    _kaydet(scheduler, "06:15", lambda: "sonuc", "Sabah")
    func, ad, slot = scheduler.find_missed_jobs()[0]
    assert ad == "Sabah" and slot == "06:15"
    assert func() == "sonuc"


def test_record_failure_does_not_break_job(scheduler, monkeypatch):
    """Kayıt yazılamazsa iş yine de başarılı sayılmalı."""
    monkeypatch.setattr(scheduler.db, "set_setting",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    calisti = []
    _kaydet(scheduler, _gecmis_saat(), lambda: calisti.append(1), "İş")

    scheduler.catch_up_missed_jobs()

    assert calisti == [1]


# =============================================
# Sağlık kontrolü yeniden başlatmaya dayanmalı
#
# `every(6).hours` ilk çalışmasını KURULUMDAN 6 saat sonrasına yazıyor
# (schedule kütüphanesinin davranışı) ve `find_missed_jobs` saatsiz işleri
# kapsamıyor. Yani her deploy sayacı sıfırlıyordu; deploy'lar 6 saatten sık
# olduğu sürece sistemin TEK alarm mekanizması hiç çalışmıyordu — tam da en
# çok gerektiği günlerde.
# =============================================

def test_healthcheck_is_a_daily_slotted_job(scheduler):
    """Sabit saatli olmalı ki telafi mekanizması onu görebilsin."""
    from config import HEALTHCHECK_TIMES

    assert HEALTHCHECK_TIMES, "sağlık kontrolü saati tanımsız"
    for saat in HEALTHCHECK_TIMES:
        _kaydet(scheduler, saat, lambda: None, "Sağlık Kontrolü")

    gunluk = [j for j in schedule_lib.jobs
              if getattr(j, "at_time", None) is not None and j.unit == "days"]
    assert len(gunluk) == len(HEALTHCHECK_TIMES)


def test_missed_healthcheck_is_caught_up(scheduler):
    """Saati geçmiş sağlık kontrolü, yeniden başlatmadan sonra telafi edilmeli."""
    calisti = []
    _kaydet(scheduler, _gecmis_saat(), lambda: calisti.append(1), "Sağlık Kontrolü")

    scheduler.catch_up_missed_jobs()

    assert calisti == [1]
