#!/bin/bash
# Instagram Otomasyon - VPS Kurulum Script'i
# Ubuntu 22.04/24.04 icin. Proje dosyalari /opt/instagram-otomasyon'a
# kopyalandiktan SONRA, VPS'te root olarak calistirin:
#   bash /opt/instagram-otomasyon/deploy/setup_vps.sh
set -e

PROJECT_DIR="/opt/instagram-otomasyon"
APP_USER="appuser"

echo "==> Sistem paketleri kuruluyor..."
apt-get update
apt-get install -y python3 python3-venv python3-pip ffmpeg curl

echo "==> cloudflared kuruluyor..."
if ! command -v cloudflared >/dev/null 2>&1; then
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
    dpkg -i /tmp/cloudflared.deb
    rm -f /tmp/cloudflared.deb
fi

echo "==> Uygulama kullanicisi olusturuluyor..."
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi
chown -R "$APP_USER:$APP_USER" "$PROJECT_DIR"

echo "==> Python sanal ortami kuruluyor..."
sudo -u "$APP_USER" python3 -m venv "$PROJECT_DIR/venv"
sudo -u "$APP_USER" "$PROJECT_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "!! UYARI: $PROJECT_DIR/.env bulunamadi. Devam etmeden once .env dosyasini olusturun."
elif ! grep -q "^DASHBOARD_PASSWORD=.\+" "$PROJECT_DIR/.env"; then
    echo "!! UYARI: DASHBOARD_PASSWORD .env'de bos! Panel internete aciliyor,"
    echo "   sifresiz birakmayin. Devam etmeden once doldurun (Ctrl+C ile durdurup duzeltin)."
    sleep 10
fi

echo "==> systemd servisleri kuruluyor..."
cp "$PROJECT_DIR/deploy/systemd/"*.service /etc/systemd/system/
systemctl daemon-reload

# Deploy hook'u: ciplak depo zaten kuruluysa guncelle. Bu dosya 3 Agustos
# 2026'ya kadar yalnizca sunucuda yasiyordu ve repoda olmadigi icin icindeki
# servis listesi sessizce eskidi -- instagram-mcp listede olmadigindan MCP
# sunucusu ard arda iki deploy'u da almadi. Artik tek dogru kaynak repo.
# Ciplak deponun yolu kuruluma ozeldir; mevcut bir kurulumda farkli olabilir.
# Ornek:  BARE_REPO=/opt/eski-adi.git bash deploy/setup_vps.sh
BARE_REPO="${BARE_REPO:-/opt/instagram-news.git}"
if [ -d "$BARE_REPO" ]; then
    echo "==> deploy hook'u guncelleniyor ($BARE_REPO)..."
    cp "$PROJECT_DIR/deploy/post-receive" "$BARE_REPO/hooks/post-receive"
    chmod +x "$BARE_REPO/hooks/post-receive"
else
    echo "!! NOT: $BARE_REPO yok. 'git push vds master' ile deploy icin once"
    echo "   ciplak depoyu kurun, sonra hook'u yerlestirin:"
    echo "     git init --bare $BARE_REPO"
    echo "     cp $PROJECT_DIR/deploy/post-receive $BARE_REPO/hooks/post-receive"
    echo "     chmod +x $BARE_REPO/hooks/post-receive"
fi

# Not: instagram-cloudflare-tunnel.service BURADA baslatilmiyor -- adlandirilmis
# tunel (cloudflared tunnel login/create/route dns) tarayici ile tek seferlik bir
# yetkilendirme gerektirdiginden otomatiklestirilemez. Once asagidaki 4 uygulama
# servisini ayaga kaldirip, sonra README.md > "Kalici URL" bolumundeki adimlarla
# tuneli manuel kurun.
for svc in instagram-scheduler instagram-mcp instagram-telegram instagram-dashboard; do
    systemctl enable "$svc"
    systemctl restart "$svc"
done

echo "==> Tamamlandi. Durum kontrolu:"
systemctl status instagram-scheduler instagram-mcp instagram-telegram instagram-dashboard --no-pager
echo ""
echo "==> Sirada: Cloudflare adlandirilmis tunelini kurun (bkz. README.md):"
echo "    cloudflared tunnel login && cloudflared tunnel create <isim>"
echo "    ~/.cloudflared/config.yml yazip 'cloudflared tunnel route dns <isim> <subdomain>'"
echo "    sonra instagram-cloudflare-tunnel.service'i etkinlestirin:"
echo "    systemctl enable --now instagram-cloudflare-tunnel"
