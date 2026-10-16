from typing import Any, Dict, List

from smartlex.core.native_engine import NativeIndex, SmartLexEngine


def _dict_to_native_index(index_dict: dict) -> NativeIndex:
    """Converts a legacy {path: [keywords]} or {path: text} dict into a C++ NativeIndex."""
    idx = NativeIndex()
    for path, content in index_dict.items():
        if isinstance(content, (list, tuple, set)):
            text = " ".join(str(x) for x in content)
        else:
            text = str(content)
        idx.add_document(str(path), text)
    return idx


def search(query: str, index_data: Any, max_results: int = 100) -> List[str]:
    """
    Executes a ranked search query using C++20 BM25F + Exact Phrase + Proximity + Fuzzy search.
    Returns a list of matching file paths ordered by relevance.
    """
    if not query or not query.strip():
        return []

    if isinstance(index_data, (SmartLexEngine, NativeIndex)):
        return index_data.search_paths(query, max_results)

    if isinstance(index_data, dict):
        if not index_data:
            return []
        temp_idx = _dict_to_native_index(index_data)
        return temp_idx.search_paths(query, max_results)

    return []


def search_with_snippets(
    query: str, index_data: Any, max_results: int = 100
) -> List[Dict[str, Any]]:
    """
    Executes a ranked search query and returns detailed result dictionaries:
      [{"path": str, "score": float, "matched_words": list[str], "snippet": str}, ...]
    """
    if not query or not query.strip():
        return []

    if isinstance(index_data, SmartLexEngine):
        return index_data.search_detailed(query, max_results=max_results, with_snippets=True)

    if isinstance(index_data, NativeIndex):
        results = index_data.search(query, max_results)
        for r in results:
            r["snippet"] = ""
        return results

    if isinstance(index_data, dict):
        if not index_data:
            return []
        temp_idx = _dict_to_native_index(index_data)
        results = temp_idx.search(query, max_results)
        for r in results:
            r["snippet"] = ""
        return results

    return []

