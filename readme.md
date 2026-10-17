# SmartLex

**Search inside your files, including the text in your screenshots and photos.**

SmartLex is a desktop search engine for Windows, macOS and Linux. Type a few words and it finds the documents, spreadsheets, slides, code, images, audio and video that contain them, ranked by relevance. It works offline: your files never leave your computer.

[![PyPI](https://img.shields.io/pypi/v/smartlex-search)](https://pypi.org/project/smartlex-search/)
[![Python](https://img.shields.io/pypi/pyversions/smartlex-search)](https://pypi.org/project/smartlex-search/)
[![CI](https://github.com/dakshgoel2008/SmartLex/actions/workflows/ci.yml/badge.svg)](https://github.com/dakshgoel2008/SmartLex/actions/workflows/ci.yml)
![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue)

![SmartLex finding "revenue forecast" in a script, a spreadsheet, meeting notes, a whiteboard photo and a PDF](https://raw.githubusercontent.com/dakshgoel2008/SmartLex/main/images/search_results.png)

## Why SmartLex

- **It reads inside files.** PDFs, Word, Excel, PowerPoint, e-books, Markdown, code and more, not just file names.
- **It reads text in images.** Screenshots, scanned receipts and photos of whiteboards are searchable by the words in them.
- **It finds what you meant.** Results are ranked by relevance. Typos are tolerated and word forms match, so `running` also finds `run`.
- **It stays up to date.** New, edited, moved and deleted files are picked up while the app is open. Moved files are never re-read.
- **It is fast.** The search core is written in C++20. Searches take milliseconds, and the window opens instantly after the first run.
- **It is private.** Everything runs on your machine. Nothing is uploaded, and there is no account or telemetry.

## Install

You need Python 3.10–3.14. Prebuilt packages are available for Windows (x64), macOS (Apple Silicon and Intel) and Linux (x64), so no compiler is needed.

```bash
pipx install smartlex-search
```

Then start the app:

```bash
smartlex
```

[pipx](https://pipx.pypa.io) installs SmartLex in its own environment and makes the `smartlex` command available everywhere. To get pipx, run `python -m pip install --user pipx` and then `python -m pipx ensurepath`, or use `brew install pipx` on macOS. Plain `pip install smartlex-search` works too.

To update later:

```bash
pipx upgrade smartlex-search
```

## Using SmartLex

On the first launch SmartLex scans your home folder (all drives on Windows) and builds its index in the background. You can search right away: file names are searchable within seconds, contents follow as they are read, and text inside images is read last. Later launches only process files that changed.

Type in the search bar, pick a filter (Docs, Sheets, Slides, Images, Audio, Video, Code) and press Enter. Double-click a result to open it, or right-click to show it in its folder or copy its path.

### Search syntax

| Query | Finds |
|---|---|
| `invoice 2024` | Files containing these words, best matches first |
| `"neural network"` | The exact phrase |
| `report -draft` | Files with "report" but not "draft" |
| `budget ext:xlsx` | Only `.xlsx` files (`ext:png,jpg` for several) |
| `type:image receipt` | Only images; also `doc`, `sheet`, `slide`, `audio`, `video`, `code` |
| `notes path:projects` | Only files whose path contains "projects" |
| `recieve` | Also matches "receive" (typo tolerance) |

### Searching images

![Searching a scanned receipt by the words printed on it](https://raw.githubusercontent.com/dakshgoel2008/SmartLex/main/images/image_search.png)

SmartLex reads the text inside images with [RapidOCR](https://github.com/RapidAI/RapidOCR), which is installed with it and runs locally.

- **Documents come first.** OCR takes about a second or two per image, so it runs after everything else is indexed. The progress bar shows "Reading text in images", and you can search the whole time.
- **Each image is read once.** Results are saved, so unchanged, moved or duplicated images are never read again.
- **Real-world images work.** Rotated phone photos, transparent logos, multi-page TIFF scans and long scrolling screenshots are handled.
- **Metadata is included.** Camera details, EXIF and XMP descriptions and tags, and a caption file next to the image (`photo.txt` or `photo.caption`) are searchable too.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Enter` | Search, or open the selected result |
| `Tab` / `Down` | Move between the search bar and results |
| `Ctrl+Enter` | Show the selected result in its folder |
| `Ctrl+C` | Copy the selected result's path |
| `Ctrl+F` | Focus the search bar |
| `Esc` | Clear the search |

### Terminal mode

```bash
smartlex-cli --dir ~/Documents                    # index a folder, then search interactively
smartlex-cli --dir ~/Documents --query "tax 2024" # one search, then exit
```

In interactive mode, type `:auto <prefix>` for word suggestions, `:status` for index size and `:q` to quit.

## Supported files

| Kind | Formats | What is searched |
|---|---|---|
| Documents | `.pdf` `.docx` `.odt` `.epub` `.txt` `.md` | Full text, with page numbers in results |
| Spreadsheets | `.xlsx` `.ods` `.csv` | Every cell, with sheet names |
| Presentations | `.pptx` `.odp` | Slide text and speaker notes |
| Code and data | `.py` `.js` `.ts` `.c` `.cpp` `.h` `.hpp` `.html` `.xml` `.json` `.yaml` | Full text |
| Images | `.png` `.jpg` `.jpeg` `.webp` `.bmp` `.tiff` `.tif` `.gif` | Text inside the image plus metadata |
| Audio | `.mp3` `.wav` `.flac` `.m4a` `.ogg` `.aac` | Tags, and lyrics or transcripts next to the file |
| Video | `.mp4` `.mkv` `.mov` `.webm` `.avi` | Metadata and subtitles, with timestamps in results |
| Subtitles | `.srt` `.vtt` `.lrc` | Timestamped text |

Optional extras:

- **Speech-to-text** for audio and video without subtitles: install [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with `pipx inject smartlex-search faster-whisper`. It downloads a speech model the first time it runs.
- **Embedded subtitle tracks** in videos are read when `ffmpeg` is on your PATH.
- **Scanned PDFs** with no text layer are OCR'd when Poppler's `pdftoppm` is on your PATH.

## How it works

1. **Scan.** A multi-threaded C++ scanner walks your folders, skipping system, hidden and build folders such as `Windows`, `node_modules` and `.git`.
2. **Index in tiers.** File names become searchable first, then file contents, then text inside images, so the app is useful from the first seconds.
3. **Rank.** A compressed positional inverted index in C++ scores results with BM25F, boosts exact phrases and nearby words, and falls back to stemming and typo matching.
4. **Remember.** A local SQLite catalog stores each file's size, modification time, content hash and extracted text. Unchanged files are skipped, moved files are recognised by their content, and result snippets come from the catalog instead of re-opening files.
5. **Watch.** A file watcher applies changes to the index while the app runs.

## Settings and data

SmartLex keeps its index, catalog and logs in `~/.smartlex` (`C:\Users\<you>\.smartlex` on Windows). Delete that folder to start fresh.

To change what is indexed, create `~/.smartlex/config.json`. Any key you leave out keeps its default:

```json
{
  "SUPPORTED_FORMATS": [".pdf", ".docx", ".xlsx", ".md", ".png", ".jpg"],
  "NUM_PROCESSES": 4
}
```

## Troubleshooting

- **`smartlex: command not found`.** Run `pipx ensurepath` and open a new terminal.
- **The first index takes a while.** It depends on how many files you have, and OCR adds a second or two per image. This only happens once; you can search while it runs.
- **macOS asks for access to Desktop, Documents or Downloads.** Allow it to search those folders. If you decline, they are skipped.
- **Linux: "Could not load the Qt platform plugin xcb".** Install Qt's system libraries, for example `sudo apt install libxcb-xinerama0 libxkbcommon-x11-0` on Ubuntu or Debian.
- **Linux on ARM and Windows on ARM** are not supported yet, because PyQt5 or the OCR libraries are not available there.

## Development

```bash
git clone https://github.com/dakshgoel2008/SmartLex.git
cd SmartLex
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
python -m unittest discover -s tests
```

Building from source needs a C++20 compiler (GCC 10+, Clang 14+ or Visual Studio 2022). Run the app from the checkout with `python run.py`.

```text
SmartLex/
├── cpp/                  C++20 core: tokenizer, inverted index, parallel scanner, Python bindings
├── src/smartlex/
│   ├── core/             Text extraction, OCR, catalog, indexing pipeline, file watcher
│   ├── gui/              PyQt5 window and widgets
│   ├── main.py           Desktop app entry point
│   └── cli.py            Terminal mode
├── tests/                Unit and end-to-end tests
└── .github/workflows/    CI and release automation
```

Every push runs the tests on Windows, macOS and Linux. To publish a release, bump `version` in `pyproject.toml`, commit, then push a matching tag (`git tag v2.1.1 && git push origin v2.1.1`). The release workflow builds and tests packages for every platform and publishes to PyPI only if all of them pass.

## Reference

- [Base paper](https://drive.google.com/file/d/10f3bUmaTRzAZ2jOq6oWFu0ilP6Q6dyJx/view?usp=sharing)

## License

Copyright (c) 2025 Daksh Goel. All rights reserved. See [LICENSE](LICENSE).
