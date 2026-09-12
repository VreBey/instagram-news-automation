"""systemd birim dosyalarının yapısal denetimi.

Neden test: birim dosyaları sessizce yanlış olabiliyor. systemd bilinmeyen
bir anahtarı hata vermeden atlıyor — yalnızca journald'a "Unknown key name
... ignoring" yazıyor ve kimse bakmıyor. Yani yazdığını sandığın kural
hiç uygulanmıyor.

Bu tam olarak bu projede avlanan hata sınıfı ("yazılan ama okunmayan
yapılandırma") ve bir kez daha yaşandı: `StartLimitIntervalSec` /
`StartLimitBurst` ilk denemede `[Service]` bölümüne konuldu, systemd 249
üzerinde denenince "Unknown key name ... ignoring" çıktı. Anahtarlar
`[Unit]` bölümüne ait.
"""

import configparser
from pathlib import Path

import pytest

BIRIM_DIZINI = Path(__file__).resolve().parent.parent / "deploy" / "systemd"

# Uzun ömürlü uygulama servisleri (şablon ve tünel hariç).
UYGULAMA_BIRIMLERI = (
    "instagram-scheduler.service",
    "instagram-telegram.service",
    "instagram-dashboard.service",
    "instagram-mcp.service",
)

# Anahtar -> ait olduğu bölüm.
_UNIT_ANAHTARLARI = {"startlimitintervalsec", "startlimitburst", "onfailure",
                     "requires", "wants", "after", "before", "description"}
_SERVICE_ANAHTARLARI = {"memorymax", "memoryaccounting", "restart", "restartsec",
                        "execstart", "user", "type", "environment",
                        "environmentfile", "workingdirectory", "cpuquota"}


def _oku(ad: str) -> configparser.ConfigParser:
    # `interpolation=None`: systemd `%n`, `%i` gibi belirteçler kullanıyor ve
    # configparser'ın varsayılan `%` yorumlaması bunlarda patlıyor.
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # anahtar adlarını olduğu gibi koru
    cp.read(BIRIM_DIZINI / ad, encoding="utf-8")
    return cp


def test_all_units_parse():
    dosyalar = list(BIRIM_DIZINI.glob("*.service"))
    assert dosyalar, "birim dosyası bulunamadı"
    for yol in dosyalar:
        _oku(yol.name)  # ayrıştırma hatası testi düşürür


@pytest.mark.parametrize("ad", UYGULAMA_BIRIMLERI)
def test_keys_are_in_the_right_section(ad):
    """
    systemd yanlış bölümdeki anahtarı SESSİZCE yok sayar. Bu testin tek işi
    o sessizliği bozmak.
    """
    cp = _oku(ad)
    yanlis = []
    for bolum in cp.sections():
        for anahtar in cp[bolum]:
            k = anahtar.lower()
            if k in _UNIT_ANAHTARLARI and bolum != "Unit":
                yanlis.append(f"{ad}: [{bolum}] içindeki {anahtar} → [Unit] olmalı")
            if k in _SERVICE_ANAHTARLARI and bolum != "Service":
                yanlis.append(f"{ad}: [{bolum}] içindeki {anahtar} → [Service] olmalı")
    assert not yanlis, "\n".join(yanlis)


@pytest.mark.parametrize("ad", UYGULAMA_BIRIMLERI)
def test_restart_loop_becomes_visible(ad):
    """
    `Restart=always` tek başına sessiz bir çökme döngüsü üretir: systemd'nin
    varsayılan hız sınırı (10 sn'de 5) `RestartSec=10` ile asla tetiklenmez,
    yani birim hiçbir zaman `failed` durumuna geçmez ve kimse fark etmez.
    """
    cp = _oku(ad)
    assert cp["Service"].get("Restart") == "always"
    assert cp["Unit"].get("StartLimitBurst"), f"{ad}: StartLimitBurst yok"
    assert cp["Unit"].get("StartLimitIntervalSec"), f"{ad}: StartLimitIntervalSec yok"
    assert "instagram-alert@" in (cp["Unit"].get("OnFailure") or ""), \
        f"{ad}: arıza bildirimi bağlı değil"


@pytest.mark.parametrize("ad", UYGULAMA_BIRIMLERI)
def test_memory_is_bounded(ad):
    """OOM katili SIGKILL gönderir; _safe_run onu yakalayamaz."""
    cp = _oku(ad)
    assert cp["Service"].get("MemoryMax"), f"{ad}: MemoryMax yok"
    assert cp["Service"].get("MemoryAccounting") == "yes"


@pytest.mark.parametrize("ad", UYGULAMA_BIRIMLERI)
def test_all_services_share_one_timezone(ad):
    """
    Dört süreç aynı saat diliminde koşmalı. instagram-mcp'de bu satır
    unutulmuştu; bkz. src/database.py başındaki zaman kuralı.
    """
    assert _oku(ad)["Service"].get("Environment") == "TZ=Europe/Istanbul"


def test_tunnel_does_not_hard_require_other_services():
    """
    `Requires=` ile bağlıyken tamamen ilgisiz bir MCP hatası tüneli de
    düşürüyor ve panelin dış URL'sini kapatıyordu.
    """
    cp = _oku("instagram-cloudflare-tunnel.service")
    assert not cp["Unit"].get("Requires"), "tünel hâlâ sert bağımlı"
    assert cp["Unit"].get("Wants"), "tünel bağımlılığı tamamen kaldırılmış"


def test_alert_template_exists_and_is_oneshot():
    cp = _oku("instagram-alert@.service")
    assert cp["Service"].get("Type") == "oneshot"
    assert "--alert" in cp["Service"].get("ExecStart", "")


def test_every_app_unit_is_restarted_by_the_deploy_hook():
    """
    Hook'un SERVICES listesi eskirse yeni servis deploy almaz — bu daha önce
    yaşandı (instagram-mcp listeye hiç eklenmemişti, iki deploy'u da kaçırdı).
    """
    hook = (BIRIM_DIZINI.parent / "post-receive").read_text(encoding="utf-8")
    satir = next(s for s in hook.splitlines() if s.startswith("SERVICES="))
    for ad in UYGULAMA_BIRIMLERI:
        assert ad.replace(".service", "") in satir, f"{ad} hook listesinde yok"
