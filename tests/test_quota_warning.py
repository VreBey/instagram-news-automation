"""Manuel onayda günlük limit uyarısı.

Denetim bulgusu (2026-08-03): `DAILY_POST_LIMIT=2` ayarlıyken 2 Ağustos'ta
18 feed gönderisi yayınlandı. Kota kontrolü yalnızca `auto_schedule_content`
içindeydi ve o yol AUTO_PUBLISH_THRESHOLD yüzünden neredeyse hiç
tetiklenmiyordu — yayınlanan içeriğin TAMAMI manuel onaydan geçiyordu.

Bu testler uyarının davranışını kilitler. Uyarı BLOKLAMAZ: manuel onay
bilinçli bir insan kararı, "onaylayınca paylaş" açık bir kullanıcı isteğiydi.
"""

from datetime import datetime
from itertools import count

import pytest

from src.telegram_bot import _quota_warning


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr("src.telegram_bot.DAILY_POST_LIMIT", 2)
    monkeypatch.setattr("src.telegram_bot.DAILY_STORY_LIMIT", 5)
    monkeypatch.setattr("src.telegram_bot.DAILY_REELS_LIMIT", 1)


_counter = count()


def _schedule_n(db, content_type, n):
    """Bugün için n adet içerik zamanla.

    Başlık/URL global bir sayaçtan üretiliyor: aynı test içinde ikinci kez
    çağrıldığında aynı URL tekrar edilirse tekilleştirme devreye girip
    add_news None döner ve testin kendisi bozulur.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for _ in range(n):
        i = next(_counter)
        news_id = db.add_news(
            title=f"H{content_type}{i}", url=f"https://example.com/{content_type}{i}",
            category="ai",
        )
        content_id = db.add_content(
            news_id=news_id, content_type=content_type, caption="c"
        )
        db.schedule_post(content_id, now, content_type, source="telegram")


def test_no_warning_below_limit(tmp_db):
    _schedule_n(tmp_db, "post", 1)
    assert _quota_warning(tmp_db, "post") is None


def test_no_warning_exactly_at_limit(tmp_db):
    """Limitin tam üstünde değil, AŞILDIĞINDA uyarılmalı."""
    _schedule_n(tmp_db, "post", 2)
    assert _quota_warning(tmp_db, "post") is None


def test_warns_when_limit_exceeded(tmp_db):
    _schedule_n(tmp_db, "post", 3)
    w = _quota_warning(tmp_db, "post")
    assert w is not None
    assert "3" in w and "2" in w
    # Onayın iptal edilmediği açıkça yazmalı — kullanıcı paniklememelil.
    assert "iptal edilmedi" in w


def test_warning_reflects_real_incident_scale(tmp_db):
    """2 Ağustos'taki gerçek durum: limit 2 iken 18 gönderi."""
    _schedule_n(tmp_db, "post", 18)
    w = _quota_warning(tmp_db, "post")
    assert w is not None and "18" in w


def test_limits_are_per_content_type(tmp_db):
    """Gönderi limitini aşmak hikaye uyarısı üretmemeli."""
    _schedule_n(tmp_db, "post", 5)
    assert _quota_warning(tmp_db, "post") is not None
    assert _quota_warning(tmp_db, "story") is None


def test_story_uses_its_own_higher_limit(tmp_db):
    _schedule_n(tmp_db, "story", 5)
    assert _quota_warning(tmp_db, "story") is None
    _schedule_n(tmp_db, "story", 1)
    assert _quota_warning(tmp_db, "story") is not None


def test_unknown_content_type_is_silent(tmp_db):
    assert _quota_warning(tmp_db, "carousel") is None


def test_db_failure_does_not_break_approval(tmp_db, monkeypatch):
    """
    Uyarı hesaplanamasa bile onay akışı çalışmaya devam etmeli — bu bir
    bilgilendirme, kritik yol değil.
    """
    monkeypatch.setattr(
        tmp_db, "get_scheduled_count_today",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    assert _quota_warning(tmp_db, "post") is None
