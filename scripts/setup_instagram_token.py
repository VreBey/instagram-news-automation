"""Instagram token'ını yenile: kısa ömürlü → uzun ömürlü, izin doğrulamalı.

Neden var: 4 Ağustos 2026'da 58 yayının performans verisinin hiç
toplanmadığı ortaya çıktı. Sebep metrik adı değil, token'da
`instagram_manage_insights` izninin HİÇ olmamasıydı — ve sistem bunu
aylarca sessizce yaşadı.

Bu script iki şeyi garanti eder:

1. Token sohbete/loga hiç yazılmaz. Giriş gizli okunur (getpass), çıktıda
   yalnızca ilk birkaç karakteri gösterilir.
2. Gerekli izinler DOĞRULANIR. İzin eksikse .env'e yazılmaz — aksi halde
   aynı sessiz arıza tekrarlardı.

Kullanım:
    python scripts/setup_instagram_token.py

Öncesinde Graph API Explorer'dan `instagram_manage_insights` izniyle kısa
ömürlü bir kullanıcı token'ı üretin (README > Instagram kimlik bilgileri).
"""

import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from config import INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET, BASE_DIR

GEREKLI_IZINLER = (
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_insights",
)

ENV_PATH = BASE_DIR / ".env"


def _kisalt(token: str) -> str:
    return f"{token[:8]}…{token[-4:]} ({len(token)} karakter)"


def _izinleri_getir(token: str) -> tuple[bool, list[str], str]:
    """Token'ın izinlerini debug_token ile sorgula."""
    r = requests.get(
        "https://graph.facebook.com/debug_token",
        params={
            "input_token": token,
            "access_token": f"{INSTAGRAM_APP_ID}|{INSTAGRAM_APP_SECRET}",
        },
        timeout=20,
    )
    data = r.json().get("data", {})
    if not data.get("is_valid"):
        return False, [], data.get("error", {}).get("message", "token geçersiz")
    return True, sorted(data.get("scopes") or []), ""


def _env_guncelle(token: str) -> bool:
    """.env içindeki INSTAGRAM_ACCESS_TOKEN satırını değiştir."""
    if not ENV_PATH.exists():
        print(f"!! {ENV_PATH} bulunamadı.")
        return False

    icerik = ENV_PATH.read_text(encoding="utf-8")
    yeni, adet = re.subn(
        r"^INSTAGRAM_ACCESS_TOKEN=.*$",
        f"INSTAGRAM_ACCESS_TOKEN={token}",
        icerik,
        flags=re.M,
    )
    if adet != 1:
        print(f"!! .env içinde tam olarak 1 INSTAGRAM_ACCESS_TOKEN satırı beklenirdi, {adet} bulundu.")
        return False

    yedek = ENV_PATH.with_suffix(f".env.bak")
    yedek.write_text(icerik, encoding="utf-8")
    ENV_PATH.write_text(yeni, encoding="utf-8")
    print(f"   .env güncellendi (yedek: {yedek.name})")
    return True


def main() -> int:
    if not INSTAGRAM_APP_ID or not INSTAGRAM_APP_SECRET:
        print("!! INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET .env'de tanımlı değil.")
        return 1

    print("Graph API Explorer'dan aldığınız KISA ÖMÜRLÜ token'ı yapıştırın.")
    print("(Girdi ekranda görünmez.)\n")
    kisa = getpass.getpass("Kısa ömürlü token: ").strip()
    if not kisa:
        print("!! Boş girdi.")
        return 1

    print(f"\n1/3 Girilen token: {_kisalt(kisa)}")

    gecerli, izinler, hata = _izinleri_getir(kisa)
    if not gecerli:
        print(f"!! Token geçersiz: {hata}")
        return 1

    eksik = [i for i in GEREKLI_IZINLER if i not in izinler]
    print(f"    İzinler ({len(izinler)}): {', '.join(izinler)}")
    if eksik:
        print(f"\n!! EKSİK İZİN: {', '.join(eksik)}")
        print("   Graph API Explorer'da bu izinleri ekleyip token'ı yeniden üretin.")
        print("   .env DEĞİŞTİRİLMEDİ — eksik izinli bir token yazmak, düzeltmeye")
        print("   çalıştığımız sessiz arızanın aynısını üretirdi.")
        return 2

    print("\n2/3 Uzun ömürlü token'a çevriliyor...")
    from src.token_manager import TokenManager

    sonuc = TokenManager().refresh_long_lived_token(kisa)
    if not sonuc or not sonuc.get("access_token"):
        print("!! Dönüşüm başarısız. APP_ID/APP_SECRET doğru mu?")
        return 1

    uzun = sonuc["access_token"]
    gun = int(sonuc.get("expires_in", 0)) // 86400
    print(f"    Uzun ömürlü token alındı: {_kisalt(uzun)}")
    print(f"    Geçerlilik: ~{gun} gün")

    gecerli, izinler, _ = _izinleri_getir(uzun)
    eksik = [i for i in GEREKLI_IZINLER if i not in izinler]
    if eksik:
        print(f"!! Dönüşüm sonrası izin kaybı: {', '.join(eksik)}. .env DEĞİŞTİRİLMEDİ.")
        return 2

    print("\n3/3 .env güncelleniyor...")
    if not _env_guncelle(uzun):
        return 1

    print("\n✅ Tamamlandı. Sırada:")
    print("   1) Aynı token'ı VDS'teki /opt/instagram-otomasyon/.env dosyasına yazın")
    print("   2) systemctl restart instagram-scheduler instagram-telegram "
          "instagram-dashboard instagram-mcp")
    print("   3) python main.py --stats  ile doğrulayın")
    return 0


if __name__ == "__main__":
    sys.exit(main())
