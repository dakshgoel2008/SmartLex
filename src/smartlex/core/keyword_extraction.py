from typing import List

from smartlex.core.native_engine import tokenize_keywords


def extract_keywords(text: str, include_stopwords: bool = False) -> List[str]:
    """Splits text into normalized keywords using the C++ tokenizer."""
    if not text or not text.strip():
        return []
    return tokenize_keywords(text, include_stopwords=include_stopwords)
