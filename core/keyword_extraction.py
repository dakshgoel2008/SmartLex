import re

from rake_nltk import Rake


def rake_keywords(text):
    if not text or not text.strip():
        return []

    r = Rake()
    r.extract_keywords_from_text(text)
    ranked_phrases = r.get_ranked_phrases()
    filtered = []

    for phrase in ranked_phrases:
        if any(
            [
                re.search(r"\b\w\b", phrase),
                re.search(r"\d", phrase),
                re.search(r"[*&!()?/>.<,:;\"\]\[\}\{]", phrase),
            ]
        ):
            continue

        for word in phrase.split():
            if len(word) > 2:
                filtered.append(word.lower())

    return filtered
