import argparse
import os
import time
from pathlib import Path

from smartlex.core.config import load_config
from smartlex.core.native_engine import SmartLexEngine, scan_directories
from smartlex.core.processor import process_all_batches
from smartlex.core.search_engine import search_with_snippets
from smartlex.core.watcher import RealTimeFileWatcher


def index_directory(target_dir: str, engine: SmartLexEngine, extensions: list) -> float:
    """Scans and incrementally indexes `target_dir` into `engine`, returning elapsed ms."""
    import tempfile

    t0 = time.perf_counter()
    scanned = scan_directories([target_dir], extensions, 8)
    paths = [rec[0] for rec in scanned]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix="_cli_batch.txt", encoding="utf-8", delete=False
    ) as tmp:
        batch_file = Path(tmp.name)
        for p in paths:
            tmp.write(f"{p}\n")

    try:
        process_all_batches([str(batch_file)], engine=engine)
        engine.save()
    finally:
        try:
            batch_file.unlink()
        except OSError:
            pass
    return (time.perf_counter() - t0) * 1000.0


def print_results(query: str, engine: SmartLexEngine) -> None:
    t0 = time.perf_counter()
    results = search_with_snippets(query, engine, max_results=15)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    print(f"\nQuery: '{query}' — {len(results)} result(s) in {elapsed_ms:.3f} ms")
    print("-" * 76)
    if not results:
        print("  No matching documents found.")
        return

    for idx, res in enumerate(results, start=1):
        score = res.get("score", 0.0)
        path = res["path"]
        snippet = res.get("snippet", "")
        matched = ", ".join(res.get("matched_words", []))
        print(f"  {idx:2d}. [{score:6.2f}] {path}")
        if matched:
            print(f"      Matched: {matched}")
        if snippet:
            print(f"      Snippet: {snippet}")
    print("-" * 76)


def interactive_loop(target_dir: str) -> None:
    cfg = load_config()
    extensions = cfg["SUPPORTED_FORMATS"]
    engine = SmartLexEngine(
        index_file=cfg.get("INDEX_FILE", "index.slex"),
        catalog_file=cfg.get("CATALOG_FILE", "smartlex_catalog.db"),
    )
    engine.load()

    print("=" * 76)
    print("  SmartLex C++20 Search Engine — Interactive Terminal Mode")
    print("=" * 76)
    print(f"  Target Directory : {os.path.abspath(target_dir)}")
    print(f"  Supported Formats: {', '.join(extensions)}")

    elapsed_ms = index_directory(target_dir, engine, extensions)
    print(
        f"  Indexed {len(engine)} active files "
        f"({engine.native_index.vocabulary_size()} unique terms) in {elapsed_ms:.1f} ms"
    )

    watcher = RealTimeFileWatcher(
        engine=engine,
        watch_roots=[target_dir],
        supported_extensions=extensions,
        debounce_seconds=0.2,
        poll_interval=1.0,
    )
    watcher.start()
    print("  Real-Time File Watcher: ACTIVE (try creating/renaming/editing a file!)")
    print("-" * 76)
    print("  Commands & Query Examples:")
    print("    • Standard search : inverted index compression")
    print('    • Exact phrase    : "exact phrase"')
    print("    • Extension filter: tokenizer ext:cpp   or   ext:png,jpg")
    print("    • Category filter : type:image | type:audio | type:video | type:sheet | type:slide | type:doc")
    print("    • Path filter     : search path:gui")
    print("    • Exclude word    : index -json")
    print("    • Typo tolerance  : invrted indx")
    print("    • Autocomplete    : :auto <prefix>   (e.g. :auto inv)")
    print("    • Status / Quit   : :status          or   :q")
    print("=" * 76)

    try:
        while True:
            try:
                query = input("\nsmartlex> ").strip()
            except EOFError:
                break

            if not query:
                continue
            if query.lower() in (":q", ":quit", "exit", "quit"):
                break
            if query.lower() == ":status":
                watcher.scan_for_delta_events()
                print(
                    f"  Active Documents: {len(engine)} | "
                    f"Vocabulary Terms: {engine.native_index.vocabulary_size()}"
                )
                continue
            if query.lower().startswith(":auto"):
                prefix = query[5:].strip()
                t0 = time.perf_counter()
                suggestions = engine.suggest_autocomplete(prefix, 15)
                dt = (time.perf_counter() - t0) * 1000.0
                print(f"  Autocomplete ({dt:.3f} ms): {', '.join(suggestions)}")
                continue

            watcher.flush_ready_events(force=True)
            print_results(query, engine)
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        watcher.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartLex C++20 Search Engine CLI")
    parser.add_argument(
        "--dir",
        "-d",
        default=".",
        help="Directory to index and watch (default: current directory)",
    )
    parser.add_argument(
        "--query",
        "-q",
        default=None,
        help="Run a single search query and exit (omit for interactive mode)",
    )
    args = parser.parse_args()

    target_dir = os.path.abspath(args.dir)
    if args.query is not None:
        cfg = load_config()
        engine = SmartLexEngine(
            index_file=cfg.get("INDEX_FILE", "index.slex"),
            catalog_file=cfg.get("CATALOG_FILE", "smartlex_catalog.db"),
        )
        engine.load()
        dt = index_directory(target_dir, engine, cfg["SUPPORTED_FORMATS"])
        print(
            f"Synced {len(engine)} files ({engine.native_index.vocabulary_size()} terms) in {dt:.1f} ms"
        )
        print_results(args.query, engine)
    else:
        interactive_loop(target_dir)


if __name__ == "__main__":
    main()

