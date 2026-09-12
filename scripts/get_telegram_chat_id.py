"""
Tek seferlik yardımcı script: Telegram bot'unuza gönderdiğiniz /start mesajından
chat_id'nizi bulur.

Kullanım:
  1. Telegram'da @BotFather ile bot oluşturup token'ı .env'e TELEGRAM_BOT_TOKEN
     olarak girin (chat_id henüz gerekmiyor).
  2. Telegram'da yeni botunuza /start yazın.
  3. Bu scripti çalıştırın: python scripts/get_telegram_chat_id.py
  4. Çıkan chat_id'yi .env'deki TELEGRAM_CHAT_ID'ye yapıştırın.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from config import TELEGRAM_BOT_TOKEN

# Windows konsolu varsayılan olarak UTF-8 kullanmayabilir (cp1254 vb.),
# bu da emoji/Türkçe karakter basarken UnicodeEncodeError'a yol açar.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN .env dosyasında ayarlanmamış.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    updates = data.get("result", [])
    if not updates:
        print("⚠️ Henüz bir mesaj bulunamadı.")
        print("   Telegram'da botunuza /start yazıp bu scripti tekrar çalıştırın.")
        return

    seen = set()
    for update in updates:
        message = update.get("message") or update.get("callback_query", {}).get("message")
        if not message:
            continue
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id and chat_id not in seen:
            seen.add(chat_id)
            name = chat.get("username") or chat.get("first_name") or "?"
            print(f"✅ chat_id={chat_id}  (kullanıcı: {name})")

    if seen:
        print("\n.env dosyanıza şunu ekleyin:")
        print(f"TELEGRAM_CHAT_ID={next(iter(seen))}")


if __name__ == "__main__":
    main()
