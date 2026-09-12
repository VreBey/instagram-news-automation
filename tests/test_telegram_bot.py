"""
Telegram bot birim testleri. Gerçek Telegram API'ye hiçbir istek atılmaz —
tüm HTTP çağrıları mock'lanır. Onay/red mantığının veritabanını doğru
güncellediği (mevcut auto_schedule_content ile aynı zamanlama deseni)
test edilir.
"""

import json
from pathlib import Path

import pytest

import src.telegram_bot as telegram_bot


def _make_draft_content(db, content_type="post", relevance_score=0.9):
    news_id = db.add_news(
        title="Test Haberi", url=f"https://example.com/{content_type}-{relevance_score}",
        category="ai"
    )
    db.mark_news_processed(news_id, relevance_score=relevance_score)
    content_id = db.add_content(news_id=news_id, content_type=content_type, caption="test")
    db.update_content_media(content_id, media_path=f"/fake/{content_type}.png")
    return content_id


def _callback_query(data, callback_id="cb1", chat_id=123, message_id=456):
    return {
        "id": callback_id,
        "data": data,
        "message": {"chat": {"id": chat_id}, "message_id": message_id},
    }


def test_handle_callback_approve_schedules_content(tmp_db, mocker):
    mocker.patch("src.telegram_bot._answer_callback_query")
    mocker.patch("src.telegram_bot._remove_buttons")
    mocker.patch("src.telegram_bot._send_text")

    content_id = _make_draft_content(tmp_db, content_type="post")

    telegram_bot._handle_callback(_callback_query(f"approve:{content_id}"), tmp_db)

    content = tmp_db.get_content_by_id(content_id)
    assert content["status"] == "approved"

    with tmp_db._get_connection() as conn:
        rows = conn.execute(
            "SELECT source, post_type FROM scheduled_posts WHERE content_id = ?",
            (content_id,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "telegram"
    assert rows[0]["post_type"] == "post"


def test_handle_callback_reject_updates_status_without_scheduling(tmp_db, mocker):
    mocker.patch("src.telegram_bot._answer_callback_query")
    mocker.patch("src.telegram_bot._remove_buttons")
    mocker.patch("src.telegram_bot._send_text")

    content_id = _make_draft_content(tmp_db, content_type="story")

    telegram_bot._handle_callback(_callback_query(f"reject:{content_id}"), tmp_db)

    content = tmp_db.get_content_by_id(content_id)
    assert content["status"] == "rejected"

    with tmp_db._get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE content_id = ?", (content_id,)
        ).fetchall()
    assert len(rows) == 0


def test_handle_callback_invalid_data_does_not_crash(tmp_db, mocker):
    answer_mock = mocker.patch("src.telegram_bot._answer_callback_query")
    mocker.patch("src.telegram_bot._remove_buttons")
    mocker.patch("src.telegram_bot._send_text")

    telegram_bot._handle_callback(_callback_query("bozuk-veri"), tmp_db)

    answer_mock.assert_called_once()
    assert "Geçersiz" in answer_mock.call_args[0][1]


def test_handle_callback_unknown_content_id(tmp_db, mocker):
    answer_mock = mocker.patch("src.telegram_bot._answer_callback_query")
    mocker.patch("src.telegram_bot._remove_buttons")
    mocker.patch("src.telegram_bot._send_text")

    telegram_bot._handle_callback(_callback_query("approve:99999"), tmp_db)

    answer_mock.assert_called_once()
    assert "bulunamadı" in answer_mock.call_args[0][1]


def test_send_approval_request_returns_none_without_credentials(tmp_db, monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "")

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)

    result = telegram_bot.send_approval_request(content)
    assert result is None


def test_send_approval_request_success_without_media(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 777}}
    post_mock = mocker.patch("src.telegram_bot.requests.request", return_value=fake_response)

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = None  # medyasız senaryo -> sendMessage kullanılmalı

    result = telegram_bot.send_approval_request(content)

    assert result == 777
    called_url = post_mock.call_args[0][1]
    assert "sendMessage" in called_url


def _callback_data_values(post_mock) -> list:
    sent_markup = json.loads(post_mock.call_args.kwargs["data"]["reply_markup"])
    return [btn["callback_data"] for row in sent_markup["inline_keyboard"] for btn in row]


def test_send_approval_request_includes_image_button_for_post(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 777}}
    post_mock = mocker.patch("src.telegram_bot.requests.request", return_value=fake_response)

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = None

    telegram_bot.send_approval_request(content)

    assert f"image:{content_id}" in _callback_data_values(post_mock)


def test_send_approval_request_omits_image_button_for_reels(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 778}}
    post_mock = mocker.patch("src.telegram_bot.requests.request", return_value=fake_response)

    content_id = _make_draft_content(tmp_db, content_type="reels")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = None

    telegram_bot.send_approval_request(content)

    assert f"image:{content_id}" not in _callback_data_values(post_mock)


def _sent_caption(post_mock) -> str:
    data = post_mock.call_args.kwargs["data"]
    return data.get("caption") or data.get("text") or ""


def test_send_approval_request_is_reminder_prefixes_caption(tmp_db, monkeypatch, mocker):
    """Hatırlatma olarak gönderilen bir mesaj, kullanıcının bunu yeni bir
    öğeyle karıştırmaması için açıkça işaretlenmeli."""
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    fake_response = mocker.Mock(status_code=200)
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 900}}
    post_mock = mocker.patch("src.telegram_bot.requests.request", return_value=fake_response)

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = None

    telegram_bot.send_approval_request(content, is_reminder=True)

    assert "Hatırlatma" in _sent_caption(post_mock)


def test_send_approval_request_without_reminder_flag_has_no_reminder_text(tmp_db, monkeypatch, mocker):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    fake_response = mocker.Mock(status_code=200)
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 901}}
    post_mock = mocker.patch("src.telegram_bot.requests.request", return_value=fake_response)

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = None

    telegram_bot.send_approval_request(content)

    assert "Hatırlatma" not in _sent_caption(post_mock)


def test_send_reminder_resends_and_clears_old_buttons(tmp_db, monkeypatch, mocker):
    """send_reminder hem yeni bir mesaj gönderip yeni message_id döndürmeli
    hem de ESKİ mesajın butonlarını kaldırmalı — aksi halde aynı içerik
    için iki canlı Onayla/Reddet çifti dolaşır."""
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")
    remove_buttons_mock = mocker.patch("src.telegram_bot._remove_buttons")

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = None
    content["telegram_message_id"] = 500  # daha önce gönderilmiş

    mocker.patch("src.telegram_bot.send_approval_request", return_value=999)

    result = telegram_bot.send_reminder(content)

    assert result == 999
    remove_buttons_mock.assert_called_once_with("12345", 500)


def test_send_reminder_keeps_old_buttons_when_resend_fails(tmp_db, monkeypatch, mocker):
    """Yeniden gönderim başarısız olursa (None döner) eski mesajın
    butonlarına DOKUNULMAMALI — aksi halde kullanıcının elinde ne yeni ne
    de eski çalışan bir onay isteği kalır."""
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")
    remove_buttons_mock = mocker.patch("src.telegram_bot._remove_buttons")

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = None
    content["telegram_message_id"] = 500

    mocker.patch("src.telegram_bot.send_approval_request", return_value=None)

    result = telegram_bot.send_reminder(content)

    assert result is None
    remove_buttons_mock.assert_not_called()


def test_handle_callback_image_sends_prompt_and_keeps_buttons(tmp_db, mocker):
    mocker.patch("src.telegram_bot._answer_callback_query")
    remove_buttons_mock = mocker.patch("src.telegram_bot._remove_buttons")
    send_text_mock = mocker.patch("src.telegram_bot._send_text", return_value=555)

    content_id = _make_draft_content(tmp_db, content_type="post")

    telegram_bot._handle_callback(_callback_query(f"image:{content_id}"), tmp_db)

    content = tmp_db.get_content_by_id(content_id)
    assert content["manual_image_prompt_message_id"] == 555
    assert content["status"] == "draft"  # onay/red durumu değişmedi
    remove_buttons_mock.assert_not_called()  # kullanıcı beklerken mevcut butonlar kalmalı
    send_text_mock.assert_called_once()


def test_handle_callback_manual_done_marks_published(tmp_db, mocker):
    """Elle paylaşım paketindeki '✅ Elle Paylaştım' butonu — Meta feed post
    yayınını API'den engellediğinde kullanıcı Instagram'dan elle paylaştığını
    bu butonla bildirir; içerik 'published' olarak kapatılmalı."""
    mocker.patch("src.telegram_bot._answer_callback_query")
    mocker.patch("src.telegram_bot._remove_buttons")
    mocker.patch("src.telegram_bot._send_text")

    content_id = _make_draft_content(tmp_db, content_type="post")

    telegram_bot._handle_callback(_callback_query(f"manual_done:{content_id}"), tmp_db)

    content = tmp_db.get_content_by_id(content_id)
    assert content["status"] == "published"
    history = tmp_db.get_publish_history()
    assert any(h["content_id"] == content_id and h["status"] == "success" for h in history)


def _reply_message(reply_message_id, chat_id=123, photo_file_id="big-photo"):
    return {
        "photo": [{"file_id": "thumb"}, {"file_id": photo_file_id}],
        "reply_to_message": {"message_id": reply_message_id},
        "chat": {"id": chat_id},
    }


def test_handle_photo_reply_regenerates_post_and_resends(tmp_db, mocker):
    mocker.patch("src.telegram_bot._remove_buttons")
    mocker.patch("src.telegram_bot._download_telegram_file_bytes", return_value=b"fake-image-bytes")
    send_approval_mock = mocker.patch("src.telegram_bot.send_approval_request", return_value=999)

    content_id = _make_draft_content(tmp_db, content_type="post")
    tmp_db.set_manual_image_prompt(content_id, 555)

    img_gen = mocker.Mock()
    img_gen.generate_post_image.return_value = "/fake/post.png"
    story_gen = mocker.Mock()

    telegram_bot._handle_photo_reply(_reply_message(555), tmp_db, img_gen, story_gen)

    content = tmp_db.get_content_by_id(content_id)
    assert content["manual_image_path"] is not None
    assert content["telegram_message_id"] == 999
    img_gen.generate_post_image.assert_called_once_with(content_id=content_id)
    story_gen.generate_story_image.assert_not_called()
    send_approval_mock.assert_called_once()


def test_handle_photo_reply_routes_story_to_story_generator(tmp_db, mocker):
    mocker.patch("src.telegram_bot._remove_buttons")
    mocker.patch("src.telegram_bot._download_telegram_file_bytes", return_value=b"fake-image-bytes")
    mocker.patch("src.telegram_bot.send_approval_request", return_value=999)

    content_id = _make_draft_content(tmp_db, content_type="story")
    tmp_db.set_manual_image_prompt(content_id, 555)

    img_gen = mocker.Mock()
    story_gen = mocker.Mock()
    story_gen.generate_story_image.return_value = "/fake/story.png"

    telegram_bot._handle_photo_reply(_reply_message(555), tmp_db, img_gen, story_gen)

    story_gen.generate_story_image.assert_called_once_with(content_id=content_id)
    img_gen.generate_post_image.assert_not_called()


def test_handle_photo_reply_ignores_unmatched_reply(tmp_db, mocker):
    img_gen = mocker.Mock()
    story_gen = mocker.Mock()

    telegram_bot._handle_photo_reply(_reply_message(99999), tmp_db, img_gen, story_gen)

    img_gen.generate_post_image.assert_not_called()
    story_gen.generate_story_image.assert_not_called()




def _video_reply_message(reply_message_id, chat_id=123, video_file_id="video-1"):
    return {
        "video": {"file_id": video_file_id},
        "reply_to_message": {"message_id": reply_message_id},
        "chat": {"id": chat_id},
    }


def test_handle_video_reply_saves_media_and_sends_approval(tmp_db, mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "REELS_OUTPUT_DIR", tmp_path)
    mocker.patch("src.telegram_bot._download_telegram_file_bytes", return_value=b"fake-video-bytes")
    send_approval_mock = mocker.patch("src.telegram_bot.send_approval_request", return_value=999)

    content_id = _make_draft_content(tmp_db, content_type="reels")
    tmp_db.set_manual_video_prompt(content_id, 555)

    video_gen = mocker.Mock()
    video_gen.add_logo_watermark.return_value = False  # watermarking başarısız → ham video kullanılır

    telegram_bot._handle_video_reply(_video_reply_message(555), tmp_db, video_gen)

    content = tmp_db.get_content_by_id(content_id)
    assert content["media_path"] is not None
    assert Path(content["media_path"]).read_bytes() == b"fake-video-bytes"
    assert content["telegram_message_id"] == 999
    send_approval_mock.assert_called_once()
    video_gen.add_logo_watermark.assert_called_once()


def test_handle_video_reply_uses_watermarked_output_on_success(tmp_db, mocker, tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "REELS_OUTPUT_DIR", tmp_path)
    mocker.patch("src.telegram_bot._download_telegram_file_bytes", return_value=b"fake-video-bytes")
    mocker.patch("src.telegram_bot.send_approval_request", return_value=999)

    content_id = _make_draft_content(tmp_db, content_type="reels")
    tmp_db.set_manual_video_prompt(content_id, 555)

    def _fake_watermark(input_path, output_path):
        Path(output_path).write_bytes(b"watermarked-video-bytes")
        return True

    video_gen = mocker.Mock()
    video_gen.add_logo_watermark.side_effect = _fake_watermark

    telegram_bot._handle_video_reply(_video_reply_message(555), tmp_db, video_gen)

    content = tmp_db.get_content_by_id(content_id)
    assert Path(content["media_path"]).read_bytes() == b"watermarked-video-bytes"
    assert not content["media_path"].endswith("_raw.mp4")


def test_handle_video_reply_ignores_unmatched_reply(tmp_db, mocker):
    download_mock = mocker.patch("src.telegram_bot._download_telegram_file_bytes")
    video_gen = mocker.Mock()

    telegram_bot._handle_video_reply(_video_reply_message(99999), tmp_db, video_gen)

    download_mock.assert_not_called()
    video_gen.add_logo_watermark.assert_not_called()


def test_is_authorized_chat_matches_configured_chat_id(monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")
    assert telegram_bot._is_authorized_chat(12345) is True
    assert telegram_bot._is_authorized_chat("12345") is True


def test_is_authorized_chat_rejects_other_chat_ids(monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")
    assert telegram_bot._is_authorized_chat(99999) is False
    assert telegram_bot._is_authorized_chat(None) is False


def test_is_authorized_chat_rejects_everything_when_unconfigured(monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "")
    assert telegram_bot._is_authorized_chat(12345) is False


def test_handle_photo_reply_ignores_message_without_photo(tmp_db, mocker):
    img_gen = mocker.Mock()
    story_gen = mocker.Mock()
    message = {"reply_to_message": {"message_id": 555}, "chat": {"id": 123}}

    telegram_bot._handle_photo_reply(message, tmp_db, img_gen, story_gen)

    img_gen.generate_post_image.assert_not_called()


def test_handle_manual_research_text_multiline_splits_title_and_summary(tmp_db, mocker):
    send_text_mock = mocker.patch("src.telegram_bot._send_text")
    message = {
        "chat": {"id": 123},
        "text": "OpenAI Yeni Model Duyurdu\nModel insan seviyesinde akıl yürütüyor.\nİkinci satır daha.",
    }

    telegram_bot._handle_manual_research_text(message, tmp_db)

    saved = tmp_db.get_external_research(limit=1)[0]
    assert saved["title"] == "OpenAI Yeni Model Duyurdu"
    assert saved["summary"] == "Model insan seviyesinde akıl yürütüyor.\nİkinci satır daha."
    assert saved["source"] == "telegram_manual"
    send_text_mock.assert_called_once()
    assert "kaydedildi" in send_text_mock.call_args[0][1]


def test_handle_manual_research_text_single_line_uses_same_text_for_both(tmp_db, mocker):
    mocker.patch("src.telegram_bot._send_text")
    message = {"chat": {"id": 123}, "text": "Tek satırlık kısa bir bulgu."}

    telegram_bot._handle_manual_research_text(message, tmp_db)

    saved = tmp_db.get_external_research(limit=1)[0]
    assert saved["title"] == "Tek satırlık kısa bir bulgu."
    assert saved["summary"] == "Tek satırlık kısa bir bulgu."


def test_handle_manual_research_text_ignores_empty_text(tmp_db, mocker):
    mocker.patch("src.telegram_bot._send_text")
    message = {"chat": {"id": 123}, "text": "   "}

    telegram_bot._handle_manual_research_text(message, tmp_db)

    assert tmp_db.get_external_research(limit=10) == []


def test_handle_manual_research_text_truncates_long_title(tmp_db, mocker):
    mocker.patch("src.telegram_bot._send_text")
    long_line = "x" * 250
    message = {"chat": {"id": 123}, "text": f"{long_line}\nözet"}

    telegram_bot._handle_manual_research_text(message, tmp_db)

    saved = tmp_db.get_external_research(limit=1)[0]
    assert len(saved["title"]) == 200


# =============================================
# _request_with_retry
# =============================================

def test_request_with_retry_returns_immediately_on_success(mocker):
    fake_response = mocker.Mock(status_code=200)
    request_mock = mocker.patch("src.telegram_bot.requests.request", return_value=fake_response)
    sleep_mock = mocker.patch("src.telegram_bot.time.sleep")

    result = telegram_bot._request_with_retry("POST", "https://example.com/x")

    assert result is fake_response
    request_mock.assert_called_once()
    sleep_mock.assert_not_called()


def test_request_with_retry_retries_on_network_exception_then_succeeds(mocker):
    import requests as requests_module
    fake_response = mocker.Mock(status_code=200)
    request_mock = mocker.patch(
        "src.telegram_bot.requests.request",
        side_effect=[requests_module.ConnectionError("boom"), fake_response],
    )
    mocker.patch("src.telegram_bot.time.sleep")

    result = telegram_bot._request_with_retry("POST", "https://example.com/x", max_retries=2)

    assert result is fake_response
    assert request_mock.call_count == 2


def test_request_with_retry_retries_on_429_using_retry_after(mocker):
    """
    Telegram 429 (hız sınırı) döndüğünde, yanıt gövdesindeki
    parameters.retry_after değeri kullanılmalı — sabit bir bekleme yerine
    Telegram'ın kendi önerdiği süre kadar beklenmeli.
    """
    rate_limited = mocker.Mock(status_code=429)
    rate_limited.json.return_value = {"parameters": {"retry_after": 7}}
    success = mocker.Mock(status_code=200)
    request_mock = mocker.patch("src.telegram_bot.requests.request", side_effect=[rate_limited, success])
    sleep_mock = mocker.patch("src.telegram_bot.time.sleep")

    result = telegram_bot._request_with_retry("POST", "https://example.com/x", max_retries=2)

    assert result is success
    assert request_mock.call_count == 2
    sleep_mock.assert_called_once_with(7)


def test_request_with_retry_retries_on_5xx(mocker):
    server_error = mocker.Mock(status_code=502)
    success = mocker.Mock(status_code=200)
    request_mock = mocker.patch("src.telegram_bot.requests.request", side_effect=[server_error, success])
    mocker.patch("src.telegram_bot.time.sleep")

    result = telegram_bot._request_with_retry("POST", "https://example.com/x", max_retries=2)

    assert result is success
    assert request_mock.call_count == 2


def test_request_with_retry_does_not_retry_400_by_default(mocker):
    bad_request = mocker.Mock(status_code=400)
    request_mock = mocker.patch("src.telegram_bot.requests.request", return_value=bad_request)
    mocker.patch("src.telegram_bot.time.sleep")

    result = telegram_bot._request_with_retry("POST", "https://example.com/x", max_retries=2)

    assert result is bad_request
    request_mock.assert_called_once()


def test_request_with_retry_retries_400_when_opted_in(mocker):
    """
    Gerçek olay: toplu Telegram bildirimi gönderiminde 20 sendPhoto
    isteğinden 9'u geçici "400 Client Error" ile başarısız oldu, aynı
    isteğin elle tekrarı her seferinde başarılı oldu. send_approval_request
    bu davranışı retry_on_400=True ile devreye alır.
    """
    bad_request = mocker.Mock(status_code=400)
    success = mocker.Mock(status_code=200)
    request_mock = mocker.patch("src.telegram_bot.requests.request", side_effect=[bad_request, success])
    mocker.patch("src.telegram_bot.time.sleep")

    result = telegram_bot._request_with_retry(
        "POST", "https://example.com/x", max_retries=2, retry_on_400=True
    )

    assert result is success
    assert request_mock.call_count == 2


def test_request_with_retry_raises_after_exhausting_network_retries(mocker):
    import requests as requests_module
    request_mock = mocker.patch(
        "src.telegram_bot.requests.request",
        side_effect=requests_module.ConnectionError("hala calismiyor"),
    )
    mocker.patch("src.telegram_bot.time.sleep")

    with pytest.raises(requests_module.ConnectionError):
        telegram_bot._request_with_retry("POST", "https://example.com/x", max_retries=2)

    assert request_mock.call_count == 3  # ilk deneme + 2 retry


def test_send_approval_request_with_photo_retries_and_reuses_full_file_bytes(tmp_db, monkeypatch, mocker, tmp_path):
    """
    Gerçek olay: dosya açık bir tanıtıcı (file handle) olarak gönderilseydi,
    ilk (başarısız) denemeden sonra imleç dosya sonunda kalıp retry'da BOŞ
    bir dosya gönderilirdi. Artık dosya içeriği bir kez belleğe okunup her
    denemede aynı tam bayt dizisi kullanılıyor.
    """
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    photo_path = tmp_path / "post.png"
    photo_path.write_bytes(b"gercek-gorsel-verisi")

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = str(photo_path)

    bad_request = mocker.Mock(status_code=400)
    success = mocker.Mock(status_code=200)
    success.raise_for_status.return_value = None
    success.json.return_value = {"ok": True, "result": {"message_id": 999}}
    request_mock = mocker.patch(
        "src.telegram_bot.requests.request", side_effect=[bad_request, success]
    )
    mocker.patch("src.telegram_bot.time.sleep")

    result = telegram_bot.send_approval_request(content)

    assert result == 999
    assert request_mock.call_count == 2
    for call in request_mock.call_args_list:
        sent_bytes = call.kwargs["files"]["photo"][1]
        assert sent_bytes == b"gercek-gorsel-verisi"




def test_request_with_retry_applies_default_timeout(mocker):
    """
    Çağıran timeout vermezse istek sonsuza kadar asılabilir ve bot sessizce
    ölür (uzun-yoklama döngüsü bir daha dönmez). Güvenli bir taban değer
    uygulanmalı.
    """
    resp = mocker.Mock()
    resp.status_code = 200
    request = mocker.patch("src.telegram_bot.requests.request", return_value=resp)

    telegram_bot._request_with_retry("GET", "https://api.telegram.org/x")

    assert request.call_args.kwargs["timeout"] == 30


def test_request_with_retry_respects_explicit_timeout(mocker):
    resp = mocker.Mock()
    resp.status_code = 200
    request = mocker.patch("src.telegram_bot.requests.request", return_value=resp)

    telegram_bot._request_with_retry("GET", "https://api.telegram.org/x", timeout=5)

    assert request.call_args.kwargs["timeout"] == 5


def test_send_manual_publish_fallback_returns_none_without_credentials(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "")

    photo_path = tmp_path / "post.png"
    photo_path.write_bytes(b"gorsel")
    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = str(photo_path)

    assert telegram_bot.send_manual_publish_fallback(content) is None


def test_send_manual_publish_fallback_returns_none_when_media_missing(tmp_db, monkeypatch):
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = "/nope/does-not-exist.png"

    assert telegram_bot.send_manual_publish_fallback(content) is None


def test_send_manual_publish_fallback_sends_photo_with_button_and_separate_caption(tmp_db, monkeypatch, mocker, tmp_path):
    """Meta feed post yayınını engellediğinde gönderilen elle paylaşım
    paketi: fotoğraf + '✅ Elle Paylaştım' butonu, caption AYRI bir metin
    mesajı olarak (fotoğraf açıklamasını kopyalamak zahmetli olduğu için)."""
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(telegram_bot, "TELEGRAM_CHAT_ID", "12345")

    photo_path = tmp_path / "post.png"
    photo_path.write_bytes(b"gercek-gorsel-verisi")

    content_id = _make_draft_content(tmp_db, content_type="post")
    content = tmp_db.get_content_by_id(content_id)
    content["media_path"] = str(photo_path)
    content["caption"] = "Test başlığı"
    content["hashtags"] = ["#ai", "#oyun"]

    fake_response = mocker.Mock(status_code=200)
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 888}}
    post_mock = mocker.patch("src.telegram_bot.requests.request", return_value=fake_response)
    send_text_mock = mocker.patch("src.telegram_bot._send_text")

    result = telegram_bot.send_manual_publish_fallback(content)

    assert result == 888
    called_url = post_mock.call_args[0][1]
    assert "sendPhoto" in called_url
    reply_markup = json.loads(post_mock.call_args.kwargs["data"]["reply_markup"])
    callback_datas = [btn["callback_data"] for row in reply_markup["inline_keyboard"] for btn in row]
    assert f"manual_done:{content_id}" in callback_datas

    send_text_mock.assert_called_once()
    caption_text = send_text_mock.call_args[0][1]
    assert "Test başlığı" in caption_text
    assert "#ai" in caption_text
