import concurrent.futures
import os
from collections import Counter
from pathlib import Path

from core.keyword_extraction import rake_keywords
from core.logger import setup_logger
from core.text_extraction import extract_text_docx, extract_text_pdf

logger = setup_logger(__name__)


def extract_keywords_from_file(file_path):
    if file_path.endswith(".pdf"):
        text = extract_text_pdf(file_path)
    elif file_path.endswith(".docx"):
        text = extract_text_docx(file_path)
    else:
        return []
    return rake_keywords(text)


def process_batch(batch_file):
    result = {}
    if not os.path.exists(batch_file):
        logger.error(f"Batch file not found: {batch_file}")
        return result

    with open(batch_file, "r", encoding="utf-8") as f:
        paths = [line.strip() for line in f]

    for path in paths:
        if os.path.exists(path):
            kws = extract_keywords_from_file(path)
            if kws:
                result[path] = kws
    return result


def refine_keywords(data, top_n):
    for k, v in data.items():
        c = Counter(v)
        top_values = [w for w, _ in c.most_common(top_n)]
        data[k] = list(set(top_values))
    return data


def process_all_batches(batch_files, top_n):
    D = {}
    with concurrent.futures.ProcessPoolExecutor() as executor:
        results = list(executor.map(process_batch, batch_files))
    for res in results:
        D.update(res)
    return refine_keywords(D, top_n)
