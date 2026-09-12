"""NVIDIA embedding istemcisi — ağ çağrıları sahte, gerçek API'ye gidilmez.

TASARIM İLKESİ SINANIYOR: her fonksiyon hatada None döner (fail open),
hiçbir zaman patlamaz. Bu modül opsiyonel bir kalite sinyali üretiyor;
pipeline'ın çekirdek işlevi ona bağımlı olamaz.
"""

import requests

import src.nvidia_embed as nvembed


def test_cosine_similarity_identical_vectors():
    v = [1.0, 2.0, 3.0]
    assert abs(nvembed.cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_similarity_orthogonal_vectors():
    assert abs(nvembed.cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_similarity_zero_vector_does_not_crash():
    assert nvembed.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_embed_text_empty_string_returns_none_without_a_call(monkeypatch):
    cagrildi = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: cagrildi.append(1))
    assert nvembed.embed_text("") is None
    assert nvembed.embed_text("   ") is None
    assert cagrildi == [], "boş metin için ağa çıkılmamalı"


def test_embed_text_no_api_key_returns_none(monkeypatch):
    monkeypatch.setattr(nvembed, "NVIDIA_API_KEY", "")
    assert nvembed.embed_text("bir haber başlığı") is None


def test_embed_text_success(monkeypatch):
    class SahteYanit:
        status_code = 200
        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr(nvembed, "NVIDIA_API_KEY", "sahte-anahtar")
    monkeypatch.setattr(requests, "post", lambda *a, **k: SahteYanit())

    sonuc = nvembed.embed_text("test")
    assert sonuc == [0.1, 0.2, 0.3]


def test_embed_text_http_error_returns_none(monkeypatch):
    class SahteHata:
        status_code = 500
        text = "sunucu hatası"

    monkeypatch.setattr(nvembed, "NVIDIA_API_KEY", "sahte-anahtar")
    monkeypatch.setattr(requests, "post", lambda *a, **k: SahteHata())

    assert nvembed.embed_text("test") is None


def test_embed_text_network_exception_returns_none(monkeypatch):
    def patlar(*a, **k):
        raise requests.exceptions.Timeout("zaman aşımı")

    monkeypatch.setattr(nvembed, "NVIDIA_API_KEY", "sahte-anahtar")
    monkeypatch.setattr(requests, "post", patlar)

    assert nvembed.embed_text("test") is None


def test_embed_text_malformed_response_returns_none(monkeypatch):
    class BozukYanit:
        status_code = 200
        def json(self):
            return {"beklenmeyen": "biçim"}

    monkeypatch.setattr(nvembed, "NVIDIA_API_KEY", "sahte-anahtar")
    monkeypatch.setattr(requests, "post", lambda *a, **k: BozukYanit())

    assert nvembed.embed_text("test") is None


def test_embed_image_missing_file_returns_none(monkeypatch):
    monkeypatch.setattr(nvembed, "NVIDIA_API_KEY", "sahte-anahtar")
    assert nvembed.embed_image("olmayan/bir/dosya.jpg") is None


def test_embed_image_success(monkeypatch, tmp_path):
    class SahteYanit:
        status_code = 200
        def json(self):
            return {"data": [{"embedding": [0.4, 0.5]}]}

    gorsel = tmp_path / "test.jpg"
    gorsel.write_bytes(b"sahte-jpeg-verisi")

    yakalanan = {}
    def sahte_post(url, headers, json, timeout):
        yakalanan["json"] = json
        return SahteYanit()

    monkeypatch.setattr(nvembed, "NVIDIA_API_KEY", "sahte-anahtar")
    monkeypatch.setattr(requests, "post", sahte_post)

    sonuc = nvembed.embed_image(str(gorsel))
    assert sonuc == [0.4, 0.5]
    # Görsel + metin AYNI vektör uzayında olmalı: metin input_type="query",
    # görsel input_type="passage" kullanır (VL modelinin beklediği format).
    assert yakalanan["json"]["input_type"] == "passage"
    assert yakalanan["json"]["input"][0].startswith("data:image/jpeg;base64,")
