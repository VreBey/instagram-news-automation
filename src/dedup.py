"""
Haber Tekilleştirme Yardımcıları
Aynı olayın farklı kaynaklarca (RSS/NewsAPI/Currents) tekrar toplanmasını
başlık benzerliğine bakarak tespit eder.
"""

import re
from difflib import SequenceMatcher


def normalize_title(title: str) -> str:
    """Başlığı karşılaştırma için sadeleştir: küçük harf, noktalama temizliği, tek boşluk."""
    if not title:
        return ""
    text = title.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text


def title_similarity(a: str, b: str) -> float:
    """İki başlık arasındaki benzerlik oranı (0.0 - 1.0)."""
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()
