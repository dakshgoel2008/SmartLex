from typing import List

from smartlex.core.native_engine import tokenize_keywords


def extract_keywords(text: str, include_stopwords: bool = False) -> List[str]:
    """
    Tokenizes text into normalized keywords using the C++20 Tokenizer.
    """
    if not text or not text.strip():
        return []
    return tokenize_keywords(text, include_stopwords=include_stopwords)


def rake_keywords(text: str) -> List[str]:
    """
    Backwards-compatible alias for `extract_keywords(text)`.
    """
    return extract_keywords(text, include_stopwords=False)
