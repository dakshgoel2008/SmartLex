# Smart Lexical Search Engine

## A powerful desktop search application that searches **inside** PDF and Word documents based on content keywords given by the user.

## ✨ Features

### Core Features

-   🔎 **Content-based search**: Find documents by keywords inside them
-   📄 **Multi-format support**: PDF and DOCX files
-   ⚡ **Fast search**: ~0.02 seconds after initial indexing
-   🎯 **Smart ranking**: Results sorted by relevance
-   🔤 **Auto-complete**: Suggests keywords as you type

### Enhanced Features (New)

-   📊 **Progress tracking**: Visual feedback during indexing
-   🔧 **Configurable settings**: Easy customization via config.json
-   📝 **Logging**: Detailed logs for debugging
-   🔄 **Parallel processing**: Uses 8 cores for faster indexing

---

## 🚀 Installation

### Method 1: Automated Setup (Recommended)

1. **Clone/Download the project**

```bash
git clone https://github.com/dakshgoel2008/SmartLex.git
cd SmartLex
```

2. **Run setup script**

```bash
python setup.py
```

### Method 2: Manual Setup

1. **Install Python 3.8+**

    - Download from [python.org](https://www.python.org/downloads/)

2. **Install dependencies**

```bash
pip install -r requirements.txt
```

3. **Download NLTK data**

```python
python -c "import nltk; nltk.download('stopwords'); nltk.download('punkt')"
```

4. **Create folders**

```bash
mkdir all logs backups
```

---

## 📖 Usage

### First Run (Indexing)

**Windows:**

```bash
# 1. Scan for files
scripts/pdf_search.bat

# 2. Run application (will index during first launch)
python searchEngine.py
```

**Linux/Mac:**

```bash
# 1. Scan for files
./scripts/pdf_search.sh

# 2. Run application
python searchEngine.py  # run it twice -> first run is for building
```

⏱️ **First run**: ~5 minutes (for ~327GB of documents)
⏱️ **Subsequent runs**: ~0.02 seconds

### Regular Usage

```bash
python searchEngine.py
```

1. Type keywords in search bar (e.g., "machine learning")
2. Press Enter or click Search
3. Click any result to open the document

### Keyboard Shortcuts

-   `Enter`: Search / Open selected document
-   `Tab`: Switch between search bar and results
-   `↑/↓`: Navigate through results
-   `Esc`: Close application

---

## 🔬 How It Works

### Architecture

```
User Query → RAKE Extraction → Keyword Matching → Ranking → Results
                                       ↑
                Document Index ← Multiprocessing ← File Scanner
```

### Step-by-Step Process

#### 1️⃣ **File Collection** (First Run Only)

-   Batch script scans entire system
-   Finds all PDF and DOCX files
-   Splits paths into 8 batches

#### 2️⃣ **Parallel Indexing** (First Run Only)

```python
# 8 processes work simultaneously
Process 1: Files 1-1000    → Extract keywords
Process 2: Files 1001-2000 → Extract keywords
...
Process 8: Files 7001-8000 → Extract keywords
```

#### 3️⃣ **Keyword Extraction** (RAKE Algorithm)

```
Input: "Machine learning algorithms use neural networks"
        ↓
Remove stopwords: "Machine learning algorithms use neural networks"
        ↓
Extract phrases: ["machine learning algorithms", "neural networks"]
        ↓
Split to words: ["machine", "learning", "algorithms", "neural", "networks"]
```

#### 4️⃣ **Index Storage**

```json
{
    "C:/docs/paper1.pdf": ["machine", "learning", "neural", "network"],
    "C:/docs/paper2.pdf": ["algorithm", "optimization", "training"]
}
```

#### 5️⃣ **Search Process**

```
User types: "neural network training"
        ↓
Extract keywords: ["neural", "network", "training"]
        ↓
Find matching documents:
  paper1.pdf: 2 matches (neural, network)
  paper2.pdf: 1 match (training)
        ↓
Rank by relevance:
  1. paper1.pdf (score: 2)
  2. paper2.pdf (score: 1)
```

---

## 📁 Project Structure

```
lexical-search-engine/
│
├── searchEngine.py
│
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── logger.py
│   ├── text_extraction.py
│   ├── keyword_extraction.py
│   ├── processor.py
│   ├── index_manager.py
│   └── search_engine.py
│
├── gui/
│   ├── __init__.py
│   ├── widgets.py
│   ├── main_window.py
│   └── threads.py
│
├── scripts/
│   ├── pdf_search.bat
│   └── pdf_search.sh
│
├── autocomplete_words.json
├── requirements.txt
├── config.json
├── README.md
```

---

## 📚 Reference

-   [Base Paper / Reference PDF](https://drive.google.com/file/d/10f3bUmaTRzAZ2jOq6oWFu0ilP6Q6dyJx/view?usp=sharing)

---
