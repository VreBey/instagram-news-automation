"""
Zamanlama Yardımcı Fonksiyonları
Hem Scheduler hem de telegram_bot.py tarafından kullanılır — dairesel import'tan
kaçınmak için ayrı bir modülde tutulur (telegram_bot.py, scheduler.py'ı import
edemez çünkü scheduler.py da telegram_bot.py'ı import eder).
"""

from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import SCHEDULE, SCHEDULE_SLOTS_BY_TYPE


def next_slot_datetime(time_str: str, extra_days: int = 0) -> str:
    """
    Verilen 'HH:MM' zaman dilimi için bir sonraki uygun tarih-saat değerini hesapla.
    Saat bugün için geçtiyse yarına kayar; extra_days aynı slotun birden fazla kez
    kullanılması gerektiğinde (round-robin) ek gün ekler.
    """
    hours, minutes = map(int, time_str.split(":"))
    now = datetime.now()
    candidate = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    candidate += timedelta(days=extra_days)
    return candidate.strftime("%Y-%m-%d %H:%M:%S")


def immediate_schedule_time(buffer_minutes: int = 1) -> str:
    """
    Kullanıcı Telegram/dashboard'dan manuel ONAY verdiğinde kullanılır —
    kullanıcı geri bildirimi: "ben onay verince otomatik paylaş, aksi halde
    günde 2-3 içerik anca paylaşılır". Eskiden manuel onay da
    compute_next_schedule_time'ın slot-rotasyonunu kullanıyordu; "post" gibi
    türlerin günde sadece 2 sabit slotu (10:00/21:00) olduğundan, art arda
    birkaç onay bile hemen ertesi güne/günlere taşıyordu. Artık kullanıcı
    onayladığı an paylaşım kuyruğuna (scheduled_posts) hemen giriyor —
    publish_scheduled() birkaç dakikada bir çalıştığından pratikte "onayla
    → yayınla" gibi hissettiriyor. Slot-rotasyonu sadece otomatik onay modu
    (auto_schedule_content) için hâlâ geçerli.
    """
    return (datetime.now() + timedelta(minutes=buffer_minutes)).strftime("%Y-%m-%d %H:%M:%S")


def compute_next_schedule_time(db, content_type: str) -> str:
    """
    Bir içerik türü için bir sonraki uygun zamanlama slotunu hesaplar
    (SCHEDULE_SLOTS_BY_TYPE'daki slotlar arasında round-robin döner, günlük
    slot sayısı dolunca ek günlere taşar).

    ÇAĞIRAN: yalnızca `scheduler.auto_schedule_content` (otomatik onay modu).
    Docstring eskiden "hem Telegram hem dashboard bunu kullanıyor" diyordu;
    o artık YANLIŞ — manuel onayın ikisi de `immediate_schedule_time`
    kullanıyor (gerekçesi orada yazılı). Bir ara bu fonksiyonun HİÇ çağıranı
    kalmamıştı: aşağıdaki düzeltme yalnızca testlerde yaşıyor, gerçek
    zamanlama ise hâlâ eski hatalı sayaç mantığını kullanıyordu.

    ÖNEMLİ: Eskiden "bugün için kaç tane zamanlanmış" SAYISI slot dizisine
    indeks olarak kullanılıyordu (0. sayı → 0. slot). Ama günün ilk slotu
    (ör. sabah postu 10:00) saati geçtiği için atlanıp o gün için ikinci
    slota (gece 21:00) yazılırsa, "1 tane var" sayısı bir sonraki çağrıda
    yanlışlıkla "1. slotu kullan" derdi — ki bu AYNI (zaten dolu) slottu.
    Gerçek olay: art arda iki onay, ikisi de aynı yarınki slota çakıştı ve
    bugünün hâlâ müsait olan ikinci slotu (gece postu) hiç kullanılmadı.
    Artık sayı yerine, o gün için HANGİ tam saatlerin gerçekten dolu olduğu
    kontrol edilip slot_keys sırasıyla gezilerek ilk boş VE gelecekteki
    slot seçiliyor.
    """
    slot_keys = SCHEDULE_SLOTS_BY_TYPE.get(content_type, [])
    if not slot_keys:
        return next_slot_datetime("09:00")

    now = datetime.now()
    for day_offset in range(0, 14):  # güvenlik sınırı — 2 hafta yeterince geniş
        target_date = (now + timedelta(days=day_offset)).date()
        date_str = target_date.strftime("%Y-%m-%d")
        used_times = db.get_scheduled_times_for_date(content_type, date_str)
        for slot_key in slot_keys:
            hours, minutes = map(int, SCHEDULE[slot_key].split(":"))
            candidate = datetime(target_date.year, target_date.month, target_date.day, hours, minutes)
            candidate_str = candidate.strftime("%Y-%m-%d %H:%M:%S")
            if candidate > now and candidate_str not in used_times:
                return candidate_str
        # Bu günün tüm slotları ya dolu ya da geçmiş, bir sonraki güne geç.
    return next_slot_datetime("09:00")
