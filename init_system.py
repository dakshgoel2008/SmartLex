import json
import os
import subprocess
import sys


def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")


def check_python_version():
    print_header("Checking Python Version")
    version = sys.version_info
    print(f"Python version: {version.major}.{version.minor}.{version.micro}")

    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("❌ Error: Python 3.8 or higher required!")
        return False

    print("SUCCESSFUL: Python version OK")
    return True


def install_dependencies():
    print_header("Installing Dependencies")

    try:
        print("Installing packages from requirements.txt...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
        )
        print("SUCCESSFUL: Dependencies installed successfully")
        return True
    except subprocess.CalledProcessError:
        print("❌ Error installing dependencies")
        return False


def download_nltk_data():
    print_header("Downloading NLTK Data")

    try:
        import nltk

        print("Downloading stopwords...")
        nltk.download("stopwords", quiet=True)
        print("Downloading punkt tokenizer...")
        nltk.download("punkt", quiet=True)
        print("SUCCESSFUL: NLTK data downloaded")
        return True
    except Exception as e:
        print(f" Error downloading NLTK data: {e}")
        return False


def create_directory_structure():
    print_header("Creating Directory Structure")

    folders = ["all", "logs", "backups"]

    for folder in folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"SUCCESSFUL: Created folder: {folder}")
        else:
            print(f"INFO: Folder exists: {folder}")

    return True


def create_default_config():
    print_header("Creating Configuration")

    if os.path.exists("config.json"):
        print("config.json already exists")
        return True

    default_config = {
        "NUM_PROCESSES": 8,
        "TOP_KEYWORDS": 150,
        "AUTOCOMPLETE_WORDS": 100,
        "SUPPORTED_FORMATS": [".pdf", ".docx"],
        "INDEX_FOLDER": "all",
        "OUTPUT_FILE": "output.json",
        "AUTOCOMPLETE_FILE": "autocomplete_words.json",
    }

    with open("config.json", "w") as f:
        json.dump(default_config, f, indent=2)

    print("SUCCESSFUL: Created config.json")
    return True


def run_tests():
    print_header("Running Tests")

    try:
        # Test imports
        print("Testing imports...")
        import fitz
        import nltk
        import PyQt5
        import rake_nltk

        print("SUCCESSFUL: All imports successful")

        # Test RAKE
        print("Testing RAKE algorithm...")
        from rake_nltk import Rake

        r = Rake()
        r.extract_keywords_from_text("machine learning algorithms")
        keywords = r.get_ranked_phrases()
        if keywords:
            print(f"SUCCESSFUL: RAKE working (extracted: {keywords[:3]})")
        else:
            print("ERROR: RAKE returned no keywords")

        return True

    except Exception as e:
        print(f"ERROR: Test failed: {e}")
        return False


def main():
    print("\n" + "🚀" * 30)
    print("  LEXICAL SEARCH ENGINE - SETUP")
    print("🚀" * 30)

    steps = [
        ("Checking Python version", check_python_version),
        ("Installing dependencies", install_dependencies),
        ("Downloading NLTK data", download_nltk_data),
        ("Creating directories", create_directory_structure),
        ("Creating configuration", create_default_config),
        ("Running tests", run_tests),
    ]

    failed_steps = []

    for step_name, step_func in steps:
        try:
            if not step_func():
                failed_steps.append(step_name)
        except Exception as e:
            print(f"ERROR: Error in {step_name}: {e}")
            failed_steps.append(step_name)

    # Summary
    print_header("Setup Summary")

    if not failed_steps:
        print("SUCCESSFUL: Setup completed successfully!")
        print("\nNext steps:")
        print("1. Run the application:")
        print("   python searchEngine.py")
        print("   (It will automatically scan and index your files on the first run)")
    else:
        print("⚠️ ERROR: Setup completed with some issues:")
        for step in failed_steps:
            print(f"   ERROR: {step}")
        print("\nPlease resolve the issues above and try again.")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
