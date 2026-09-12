"""
src.mcp_server birim testleri. add_research_finding/list_recent_research
modül seviyesindeki _db global'ini kullanıyor (bağımlılık enjeksiyonu yok) —
bu yüzden her testte src.mcp_server._db, tmp_db (izole SQLite) ile
monkeypatch'leniyor; aksi halde gerçek data/news.db dosyasına yazılırdı.
"""

import src.mcp_server as mcp_server


def test_add_research_finding_writes_to_db(tmp_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_db", tmp_db)

    result = mcp_server.add_research_finding(
        title="Test Bulgusu", summary="Bir özet", category="gaming", topic="ekonomi",
        source_url="https://example.com/kaynak",
    )

    assert result["success"] is True
    saved = tmp_db.get_external_research(limit=10)
    assert len(saved) == 1
    assert saved[0]["title"] == "Test Bulgusu"
    assert saved[0]["topic"] == "ekonomi"
    assert saved[0]["source"] == "claude_research"


def test_add_research_finding_auto_promotes_to_news_items(tmp_db, monkeypatch):
    """
    Gerçek olay: bu tool eskiden bulguyu sadece kaydedip pipeline'a
    aktarmıyordu — aktarma Dashboard'dan elle yapılmayı bekliyordu.
    Zamanlanmış bir ajan (Claude Code Routine) çalıştırıldığında bulgular
    sessizce askıda kalıp hiç işlenmedi. Artık tek çağrıda hem kaydedilip
    hem haber pipeline'ına aktarılmalı.
    """
    monkeypatch.setattr(mcp_server, "_db", tmp_db)

    result = mcp_server.add_research_finding(
        title="Yeni MMORPG Güncellemesi", summary="Detaylı özet", category="gaming",
    )

    assert result["news_id"] is not None
    unprocessed = tmp_db.get_unprocessed_news(category="gaming")
    assert any(n["id"] == result["news_id"] for n in unprocessed)


def test_add_research_finding_invalid_category_falls_back_to_gaming(tmp_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_db", tmp_db)

    result = mcp_server.add_research_finding(
        title="Başlık", summary="Özet", category="not-a-real-category",
    )

    unprocessed = tmp_db.get_unprocessed_news(category="gaming")
    assert any(n["id"] == result["news_id"] for n in unprocessed)


def test_add_research_finding_optional_fields_become_none(tmp_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_db", tmp_db)

    mcp_server.add_research_finding(title="Başlık", summary="Özet")

    saved = tmp_db.get_external_research(limit=10)
    assert saved[0]["topic"] is None
    assert saved[0]["source_url"] is None


def test_list_recent_research_returns_saved_findings(tmp_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_db", tmp_db)

    mcp_server.add_research_finding(title="İlk", summary="Özet 1")
    mcp_server.add_research_finding(title="İkinci", summary="Özet 2")

    results = mcp_server.list_recent_research(limit=10)

    assert len(results) == 2
    assert {r["title"] for r in results} == {"İlk", "İkinci"}


def test_list_recent_research_respects_limit(tmp_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "_db", tmp_db)

    for i in range(5):
        mcp_server.add_research_finding(title=f"Bulgu {i}", summary="x")

    results = mcp_server.list_recent_research(limit=2)
    assert len(results) == 2


def test_run_server_warns_when_shared_secret_missing(monkeypatch, mocker):
    monkeypatch.setattr(mcp_server, "MCP_SHARED_SECRET", "")
    mocker.patch.object(mcp_server, "_build_cors_app")
    mocker.patch("uvicorn.Config")
    mocker.patch("uvicorn.Server")
    warning_mock = mocker.patch.object(mcp_server.logger, "warning")

    mcp_server.run_server()

    assert any("MCP_SHARED_SECRET" in str(call.args[0]) for call in warning_mock.call_args_list)


def test_run_server_does_not_warn_when_shared_secret_set(monkeypatch, mocker):
    monkeypatch.setattr(mcp_server, "MCP_SHARED_SECRET", "some-secret")
    mocker.patch.object(mcp_server, "_build_cors_app")
    mocker.patch("uvicorn.Config")
    mocker.patch("uvicorn.Server")
    warning_mock = mocker.patch.object(mcp_server.logger, "warning")

    mcp_server.run_server()

    assert not any("MCP_SHARED_SECRET" in str(call.args[0]) for call in warning_mock.call_args_list)


def test_build_cors_app_allows_all_origins_and_exposes_session_header():
    """
    Gemini'nin 'Özel bağlı uygulama' ekranı bağlantıyı tarayıcıdan (fetch)
    doğruluyor ve önce bir OPTIONS preflight gönderiyor. CORS başlıkları
    olmadan bu preflight 405 dönüyor ve Gemini bunu "sunucuya ulaşılamadı"
    olarak gösteriyordu (gerçek vaka, README'deki Cloudflare tüneliyle
    doğrulandı). Bu test, düzeltmenin (CORSMiddleware sarmalayıcısı) kalıcı
    olduğunu kilitler.
    """
    from starlette.middleware.cors import CORSMiddleware

    app = mcp_server._build_cors_app()

    assert isinstance(app, CORSMiddleware)
    assert app.allow_origins == ["*"]
    assert app.simple_headers.get("Access-Control-Expose-Headers") == "mcp-session-id"
