"""
schedule_utils.compute_next_schedule_time için birim testleri. Gerçek olay:
günün ilk slotu (ör. sabah postu 10:00) çoktan geçtiğinde, art arda iki onay
"bugün 0 tane zamanlanmış" görüp AYNI yarınki slota çakışıyordu — hem de
bugünün hâlâ müsait olan ikinci slotu (gece postu 21:00) hiç kullanılmadan
atlanıyordu. datetime.now() dondurularak (freeze) bu senaryolar test edilir.
"""

import datetime as real_datetime
import uuid

import src.schedule_utils as schedule_utils_module
from src.schedule_utils import compute_next_schedule_time, immediate_schedule_time


class _FrozenDateTime(real_datetime.datetime):
    """datetime.now() sabit bir değer döndürür, geri kalan her şey gerçek
    datetime davranışıyla aynı kalır (constructor, karşılaştırma, aritmetik)."""

    _frozen = real_datetime.datetime(2026, 8, 1, 14, 30, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen


def _freeze(monkeypatch, dt: real_datetime.datetime):
    _FrozenDateTime._frozen = dt
    monkeypatch.setattr(schedule_utils_module, "datetime", _FrozenDateTime)


def _make_content(db, content_type="post"):
    news_id = db.add_news(title="Test Haberi", url=f"https://example.com/{content_type}-{uuid.uuid4()}", category="ai")
    db.mark_news_processed(news_id, relevance_score=0.9)
    return db.add_content(news_id=news_id, content_type=content_type, caption="test")


def test_picks_first_slot_when_still_in_future(tmp_db, monkeypatch):
    # Saat 08:00 — sabah postu (10:00) henüz gelmedi, ilk slot seçilmeli.
    _freeze(monkeypatch, real_datetime.datetime(2026, 8, 1, 8, 0, 0))

    result = compute_next_schedule_time(tmp_db, "post")

    assert result == "2026-08-01 10:00:00"


def test_picks_todays_second_slot_when_first_has_passed(tmp_db, monkeypatch):
    # Gerçek olay senaryosu: saat 14:30 — sabah postu (10:00) geçmiş ama
    # gece postu (21:00) hâlâ bugün için müsait. Bu slot atlanmamalı.
    _freeze(monkeypatch, real_datetime.datetime(2026, 8, 1, 14, 30, 0))

    result = compute_next_schedule_time(tmp_db, "post")

    assert result == "2026-08-01 21:00:00"


def test_two_sequential_approvals_do_not_collide(tmp_db, monkeypatch):
    # Gerçek olayın kendisi: art arda iki onay aynı ana çakışmamalı.
    _freeze(monkeypatch, real_datetime.datetime(2026, 8, 1, 14, 30, 0))

    first = compute_next_schedule_time(tmp_db, "post")
    tmp_db.schedule_post(content_id=_make_content(tmp_db), scheduled_time=first, post_type="post")
    second = compute_next_schedule_time(tmp_db, "post")

    assert first == "2026-08-01 21:00:00"
    assert second == "2026-08-02 10:00:00"
    assert first != second


def test_rolls_to_next_day_when_all_todays_slots_passed(tmp_db, monkeypatch):
    # Saat 22:00 — hem sabah (10:00) hem gece postu (21:00) bugün için geçmiş.
    _freeze(monkeypatch, real_datetime.datetime(2026, 8, 1, 22, 0, 0))

    result = compute_next_schedule_time(tmp_db, "post")

    assert result == "2026-08-02 10:00:00"


def test_rolls_to_next_day_when_todays_slots_already_full(tmp_db, monkeypatch):
    _freeze(monkeypatch, real_datetime.datetime(2026, 8, 1, 8, 0, 0))
    tmp_db.schedule_post(content_id=_make_content(tmp_db), scheduled_time="2026-08-01 10:00:00", post_type="post")
    tmp_db.schedule_post(content_id=_make_content(tmp_db), scheduled_time="2026-08-01 21:00:00", post_type="post")

    result = compute_next_schedule_time(tmp_db, "post")

    assert result == "2026-08-02 10:00:00"


def test_immediate_schedule_time_is_near_future(monkeypatch):
    # Kullanıcı geri bildirimi: manuel onay slot-rotasyonu yerine "hemen"
    # zamanlanmalı — aksi halde günde 2-3 içerik anca paylaşılıyordu.
    _freeze(monkeypatch, real_datetime.datetime(2026, 8, 1, 14, 30, 0))

    result = immediate_schedule_time(buffer_minutes=1)

    assert result == "2026-08-01 14:31:00"
