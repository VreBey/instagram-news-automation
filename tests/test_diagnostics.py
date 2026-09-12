"""Salt-okunur teşhis komutları.

Neden var: sistemin sessiz arızaları ancak loglara elle bakılınca fark
ediliyordu — Telegram 2 saat koptu (1-2 Ağustos), insights aylarca 0/58
topladı (4 Ağustos), 290 haber işlenmeden birikti. Sağlık kontrolü bu sınıfı
kendiliğinden bildiriyor; buradaki eksik "sorulduğunda cevap verme"ydi.

En kritik test: bu komutların hiçbiri durum DEĞİŞTİRMEMELİ.
"""

import pytest

from src import diagnostics
from src.telegram_bot import _handle_command


def _seed(db, n=3):
    for i in range(n):
        news_id = db.add_news(title=f"Haber {i}", url=f"https://example.com/{i}",
                              category="ai", source_name=f"K{i}")
        db.mark_news_processed(news_id, relevance_score=0.8)
        db.add_content(news_id=news_id, content_type="post", caption="c",
                       summary_text="özet")


@pytest.fixture(autouse=True)
def _no_systemctl(monkeypatch):
    """Testler gerçek servis durumu sorgulamasın."""
    monkeypatch.setattr(
        diagnostics, "_service_states",
        lambda: [("instagram-scheduler", "active"), ("instagram-telegram", "active")],
    )


# =============================================
# Raporlar
# =============================================

def test_status_lists_services_and_queues(tmp_db):
    _seed(tmp_db)
    out = diagnostics.system_status(tmp_db)
    assert "scheduler" in out
    assert "Onay bekleyen" in out
    assert "Disk" in out


def test_status_flags_down_service(tmp_db, monkeypatch):
    monkeypatch.setattr(
        diagnostics, "_service_states",
        lambda: [("instagram-telegram", "failed"), ("instagram-mcp", "active")],
    )
    out = diagnostics.system_status(tmp_db)
    assert "❌" in out
    assert "1 servis çalışmıyor" in out


def test_status_reports_missing_backup(tmp_db, monkeypatch):
    monkeypatch.setattr("src.maintenance.get_latest_backup_age_hours", lambda *a, **k: None)
    assert "hiç yok" in diagnostics.system_status(tmp_db)


def test_funnel_counts_pipeline_stages(tmp_db):
    _seed(tmp_db, 4)
    out = diagnostics.funnel_report(tmp_db)
    assert "Toplanan haber: 4" in out
    assert "Üretilen içerik: 4" in out


def test_funnel_handles_empty_system(tmp_db):
    out = diagnostics.funnel_report(tmp_db)
    assert "Toplanan haber: 0" in out
    assert "bugün henüz yok" in out


def test_funnel_flags_quota_overrun(tmp_db, monkeypatch):
    """2 Ağustos'ta limit 2 iken 18 gönderi yayınlanmıştı — görünür olmalı."""
    monkeypatch.setattr(diagnostics, "DAILY_POST_LIMIT", 2)
    _seed(tmp_db, 1)
    for i in range(5):
        tmp_db.add_publish_record(content_id=1, post_type="post", status="success",
                                  instagram_media_id=f"m{i}")
    assert "limit aşıldı" in diagnostics.funnel_report(tmp_db)


def test_errors_report_when_log_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "LOG_FILE", tmp_path / "yok.log")
    assert "okunamadı" in diagnostics.recent_errors()


def test_errors_extracts_only_problem_lines(monkeypatch, tmp_path):
    log = tmp_path / "app.log"
    log.write_text(
        "2026-08-04 10:00:00 | INFO     | src.x | normal satır\n"
        "2026-08-04 10:00:01 | ERROR    | src.y | bir hata oldu\n"
        "2026-08-04 10:00:02 | WARNING  | src.z | bir uyarı\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics, "LOG_FILE", log)
    out = diagnostics.recent_errors()
    assert "bir hata oldu" in out and "bir uyarı" in out
    assert "normal satır" not in out


def test_errors_clean_log(monkeypatch, tmp_path):
    log = tmp_path / "app.log"
    log.write_text("2026-08-04 10:00:00 | INFO | src.x | her şey yolunda\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "LOG_FILE", log)
    assert "hata/uyarı yok" in diagnostics.recent_errors()


def test_quota_reports_missing_insights_permission(tmp_db):
    tmp_db.set_setting("insights_permission_status", "missing")
    assert "Insights izni yok" in diagnostics.quota_report(tmp_db)


def test_quota_reports_healthy_insights(tmp_db):
    tmp_db.set_setting("insights_permission_status", "ok")
    assert "Insights verisi toplanıyor" in diagnostics.quota_report(tmp_db)


def test_help_lists_all_commands():
    h = diagnostics.help_text()
    for c in ("/durum", "/huni", "/loglar", "/kota"):
        assert c in h
    assert "salt-okunur" in h


# =============================================
# Komut yönlendirme
# =============================================

def _komut(db, metin, mocker):
    gonderilen = []
    mocker.patch("src.telegram_bot._send_text",
                 side_effect=lambda cid, t, **k: gonderilen.append(t))
    _handle_command({"chat": {"id": 1}, "text": metin}, db)
    return "\n".join(gonderilen)


@pytest.mark.parametrize("metin,beklenen", [
    ("/durum", "Sistem Durumu"),
    ("/huni", "Son 24 Saat"),
    ("/kota", "Kota ve Token"),
    ("/yardim", "Teşhis Komutları"),
    ("/start", "Teşhis Komutları"),
])
def test_commands_are_routed(tmp_db, mocker, metin, beklenen):
    assert beklenen in _komut(tmp_db, metin, mocker)


def test_group_suffix_is_stripped(tmp_db, mocker):
    """Gruplarda Telegram '/durum@botadi' gönderir."""
    assert "Sistem Durumu" in _komut(tmp_db, "/durum@ornek_bot", mocker)


def test_unknown_command_shows_help(tmp_db, mocker):
    out = _komut(tmp_db, "/bilinmeyen", mocker)
    assert "Bilinmeyen komut" in out and "/durum" in out


def test_command_failure_does_not_crash_bot(tmp_db, mocker):
    """Teşhis komutu patlarsa bot düşmemeli, sadece söylemeli."""
    mocker.patch.object(diagnostics, "system_status", side_effect=RuntimeError("bum"))
    assert "Rapor üretilemedi" in _komut(tmp_db, "/durum", mocker)


# =============================================
# EN KRİTİK: hiçbir komut durum değiştirmemeli
# =============================================

def _veritabani_parmak_izi(db) -> str:
    """Veritabanının TÜM içeriğinin özeti — satır sayısı değil.

    Eski sürüm yalnızca beş tablonun satır SAYISINI karşılaştırıyordu.
    Bir UPDATE sayıyı değiştirmez: içerik durumunu 'approved' yapmak,
    `is_used` bayrağını çevirmek, kayıtlı Instagram token'ını silmek ya da
    bir caption'ı baştan yazmak o testten SORUNSUZ geçerdi. Oysa testin
    korumak istediği şey tam olarak bu — Telegram kanalını ele geçiren
    birinin sunucuda iş yaptıramaması.

    Parmak izi tüm tabloları ve tüm sütunları kapsıyor; yeni bir tablo
    eklendiğinde de kendiliğinden kapsama giriyor.
    """
    import hashlib

    ozet = hashlib.sha256()
    with db._get_connection() as conn:
        tablolar = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tablolar:
            ozet.update(t.encode())
            for satir in conn.execute(f"SELECT * FROM {t} ORDER BY rowid"):
                ozet.update(repr(tuple(satir)).encode())
    return ozet.hexdigest()


@pytest.mark.parametrize("metin", ["/durum", "/huni", "/loglar", "/kota", "/yardim"])
def test_commands_never_mutate_state(tmp_db, mocker, metin):
    """
    Bilinçli sınır: Telegram kanalını ele geçiren biri sunucuda iş
    yaptıramamalı. Aynı ihtiyacı karşılayan genel amaçlı bir AI ajanı
    kurmak bu garantiyi ortadan kaldırırdı.

    Ölçü SATIR SAYISI DEĞİL, tüm satırların içeriği — bkz.
    `_veritabani_parmak_izi`.
    """
    _seed(tmp_db, 3)

    once = _veritabani_parmak_izi(tmp_db)
    _komut(tmp_db, metin, mocker)

    assert _veritabani_parmak_izi(tmp_db) == once


def test_fingerprint_catches_an_update(tmp_db):
    """
    Ölçünün KENDİSİNİ doğrula: parmak izi satır sayısını değiştirmeyen bir
    değişikliği görmeli. Görmezse yukarıdaki test hiçbir şey garanti etmez.
    """
    _seed(tmp_db, 3)
    once = _veritabani_parmak_izi(tmp_db)

    with tmp_db._get_connection() as conn:
        conn.execute("UPDATE news_items SET is_used = 1")

    assert _veritabani_parmak_izi(tmp_db) != once, \
        "parmak izi satır sayısını değiştirmeyen değişikliği kaçırdı"


def test_fingerprint_is_stable_without_changes(tmp_db):
    """Parmak izi kararlı olmalı; yoksa test rastgele kırmızıya döner."""
    _seed(tmp_db, 3)
    assert _veritabani_parmak_izi(tmp_db) == _veritabani_parmak_izi(tmp_db)
