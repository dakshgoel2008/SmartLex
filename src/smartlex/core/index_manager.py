import json
from collections import Counter
from pathlib import Path


def save_index(data, output_file):
    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)


def load_index(output_file):
    with open(output_file, "r") as f:
        return json.load(f)


def generate_autocomplete(data, top_n):
    words = []
    for kws in data.values():
        words.extend(kws)
    freq = Counter(words)
    return [w for w, _ in freq.most_common(top_n)]
