"""
MCP Sunucusu — Gemini Spark / Harici Araştırma Köprüsü
Gemini'nin "Bağlı Uygulamalar" (Custom Connected Apps) özelliğine bağlanabilecek
bir Streamable HTTP MCP sunucusu. Gemini Spark, günlük araştırma bulgularını
buradaki `add_research_finding` tool'unu çağırarak bu projenin veritabanına yazar.

Çalıştırma: python main.py --mcp-server
Dışa açma: Cloudflare Tunnel (bkz. README.md) — bu sunucu kendi başına public
değildir, sadece localhost'ta dinler.
"""

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import MCP_SERVER_HOST, MCP_SERVER_PORT, MCP_PATH, MCP_SHARED_SECRET
from src.database import Database

logger = logging.getLogger(__name__)

# FastMCP, host="127.0.0.1" olduğunda otomatik olarak yalnızca
# localhost/127.0.0.1 Host header'ına izin veren bir DNS-rebinding koruması
# açıyor. Cloudflare Tunnel isteği olduğu gibi (public tünel adresiyle)
# ilettiğinden bu koruma her isteği 421 ile reddeder. Gerçek erişim kontrolümüz
# zaten URL'ye gömülü MCP_SHARED_SECRET olduğundan bu ekstra katmanı kapatıyoruz.
mcp = FastMCP(
    name="instagram-otomasyon-research-bridge",
    instructions=(
        "Instagram AI & Gaming News Otomasyon projesine harici araştırma "
        "bulguları eklemek için kullanılır. add_research_finding ile yeni bir "
        "bulgu kaydedin, list_recent_research ile daha önce kaydedilenleri görün."
    ),
    host=MCP_SERVER_HOST,
    port=MCP_SERVER_PORT,
    streamable_http_path=MCP_PATH,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

_db = Database()


@mcp.tool()
def add_research_finding(title: str, summary: str, category: str = "gaming",
                          topic: str = "", source_url: str = "") -> dict:
    """
    Bir araştırma bulgusunu kaydet ve otomatik olarak Instagram içerik
    pipeline'ına aktar (haber olarak işlenip görsel/caption üretimine girer).

    ÖNEMLİ: Bu tool eskiden bulguyu sadece kaydedip pipeline'a aktarmıyordu
    (aktarma, Dashboard'dan elle "Haberlere Aktar" denmesini bekliyordu) —
    gerçek bir zamanlanmış ajan çalıştırıldığında bulgular sessizce askıda
    kalıp hiç işlenmedi. Artık ekleme ile aktarma tek adımda yapılıyor.

    Args:
        title: Bulgunun kısa, spesifik başlığı (clickbait olmayan).
        summary: Somut isim/tarih/rakam içeren, detaylı (3-5 cümle) İngilizce özet.
        category: 'gaming' veya 'ai' olmalı (varsayılan: 'gaming').
        topic: Serbest metin konu/etiket (opsiyonel).
        source_url: Bulgunun birincil kaynağının URL'si (opsiyonel).
    """
    if category not in ("gaming", "ai"):
        category = "gaming"
    research_id = _db.add_external_research(
        source="claude_research",
        title=title,
        summary=summary,
        topic=topic or None,
        source_url=source_url or None,
    )
    news_id = _db.promote_research_to_news(research_id, category=category)
    return {"success": True, "research_id": research_id, "news_id": news_id}


@mcp.tool()
def list_recent_research(limit: int = 10) -> list[dict]:
    """Son kaydedilen araştırma bulgularını listele (en yeniden en eskiye)."""
    return _db.get_external_research(limit=limit)


def _build_cors_app():
    """
    mcp.run() yerine Starlette app'i elle alıp CORSMiddleware ile sarmalıyoruz.
    Sebep: Gemini'nin "Özel bağlı uygulama" ekranı bağlantıyı tarayıcıdan
    (fetch ile) doğruluyor; tarayıcı önce bir OPTIONS preflight isteği
    gönderiyor. FastMCP'nin varsayılan Starlette app'inde hiç CORS başlığı
    yok — OPTIONS isteği 405 ile reddediliyor, tarayıcı bunu "sunucuya
    ulaşılamadı" olarak yorumluyor (curl gibi CORS uygulamayan bir istemciyle
    test edildiğinde sorun hiç görünmüyor, çünkü CORS tamamen tarayıcı
    tarafında uygulanan bir kısıtlama). Gerçek erişim kontrolü zaten URL'ye
    gömülü MCP_SHARED_SECRET'ta olduğundan origin'i kısıtlamanın ek bir
    güvenlik faydası yok — bu yüzden tüm origin'lere izin veriyoruz.
    """
    from starlette.middleware.cors import CORSMiddleware

    app = mcp.streamable_http_app()
    return CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )


def run_server():
    """MCP sunucusunu Streamable HTTP transport ile (CORS destekli) başlat."""
    if not MCP_SHARED_SECRET:
        logger.warning(
            "⚠️ MCP_SHARED_SECRET ayarlanmamış! Sunucu URL'sini bilen HERKES "
            "add_research_finding'i çağırabilir. .env dosyasına uzun rastgele "
            "bir MCP_SHARED_SECRET eklemeniz şiddetle önerilir."
        )

    # DİKKAT: MCP_PATH paylaşılan sırrı URL YOLUNDA taşır. Tam URL'yi loglamak
    # sırrı diske yazar — 2026-08-03 denetiminde app.log'da bulundu. Bu yüzden
    # yalnızca sırsız kısmı logluyoruz; tam adres zaten .env'de duruyor.
    logger.info(
        f"🔌 MCP sunucusu başlıyor: http://{MCP_SERVER_HOST}:{MCP_SERVER_PORT}/mcp/***"
    )
    logger.info("   Tam adres (sır dahil) .env > MCP_SHARED_SECRET ile oluşur;")
    logger.info("   bilerek loglanmıyor. Cloudflare Tunnel ile dışa açıp tünel")
    logger.info("   URL'sini Gemini'nin 'Özel bağlı uygulama' dialoguna girin.")

    import uvicorn

    # `log_config=None`: uvicorn KENDİ handler'larını kurmasın.
    #
    # Varsayılan yapılandırması `uvicorn.access` logger'ını `propagate=False`
    # ile kendi handler'ına bağlıyor ve satır biçimi tam istek yolunu
    # içeriyor: `"POST /mcp/<SIR> HTTP/1.1" 200`. propagate kapalı olduğu
    # için root handler'lardaki RedactingFilter bu kaydı HİÇ görmüyordu —
    # `/mcp/` desenini tanıyor olması (log_redaction.py) bir işe yaramıyordu.
    # Sonuç: MCP'nin TEK kimlik doğrulaması olan sır, her istekte journald'a
    # düz metin yazılıyordu.
    #
    # None verince uvicorn'un kayıtları root'a propagate olur ve maskeli
    # handler'lardan geçer; erişim logu korunur ama sır maskelenir.
    config = uvicorn.Config(
        _build_cors_app(),
        host=MCP_SERVER_HOST,
        port=MCP_SERVER_PORT,
        log_config=None,
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    from src.log_redaction import install as install_log_redaction
    install_log_redaction()
    run_server()
