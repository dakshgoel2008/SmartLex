from core.keyword_extraction import rake_keywords


def search(query, index_data):
    if not query.strip():
        return []
    query_kws = rake_keywords(query)
    scores = {}

    for kw in query_kws:
        for path, kws in index_data.items():
            if kw in kws:
                scores[path] = scores.get(path, 0) + 1

    return sorted(scores, key=scores.get, reverse=True)
