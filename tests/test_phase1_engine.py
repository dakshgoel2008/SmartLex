import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from smartlex.core.catalog import CatalogDB
from smartlex.core.config import load_config
from smartlex.core.index_manager import generate_autocomplete, load_index, save_index
from smartlex.core.keyword_extraction import extract_keywords, rake_keywords
from smartlex.core.native_engine import SmartLexEngine, edit_distance, stem_word
from smartlex.core.processor import process_all_batches
from smartlex.core.search_engine import search, search_with_snippets
from smartlex.core.text_extraction import extract_text_from_file


class TestPhase1SmartLexEngine(unittest.TestCase):

    def test_tokenizer_keeps_numbers_and_punctuated_words(self):
        """
        Verifies that the bug where 'invoice 2024' dropped '2024' and punctuated
        phrases were discarded is completely fixed.
        """
        kws = rake_keywords("Invoice #2024: Payment of $1,500 is due on Jan 15, 2026!")
        self.assertIn("invoice", kws)
        self.assertIn("2024", kws)
        self.assertIn("1500", "".join(kws) if "1500" not in kws else kws)
        self.assertIn("payment", kws)
        self.assertIn("2026", kws)

    def test_stemmer_and_edit_distance(self):
        self.assertEqual(stem_word("algorithms"), stem_word("algorithm"))
        self.assertEqual(stem_word("running"), stem_word("run"))
        self.assertEqual(stem_word("studies"), stem_word("study"))
        self.assertEqual(edit_distance("algoritm", "algorithm"), 1)
        self.assertEqual(edit_distance("invocie", "invoice"), 2)

    def test_100_percent_recall_beyond_150_words(self):
        """
        In the old engine, only the top 150 words were kept per file.
        Verify that a word appearing once after 400 other unique words is 100% searchable.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SmartLexEngine(
                index_file=os.path.join(tmpdir, "index.slex"),
                catalog_file=os.path.join(tmpdir, "catalog.db"),
            )
            filler_words = [f"unique_token_{i} " * 3 for i in range(400)]
            rare_target = "xylophone_quantum_2099"
            full_text = " ".join(filler_words) + " " + rare_target

            doc_path = "/docs/huge_manual.pdf"
            engine.add_or_update_document(doc_path, full_text, save_to_catalog=False)

            results = search(rare_target, engine)
            self.assertEqual(results, [doc_path])

    def test_exact_phrase_and_proximity_ranking(self):
        """
        Verifies 100% positional accuracy for quoted exact phrases and proximity boosting.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SmartLexEngine(
                index_file=os.path.join(tmpdir, "index.slex"),
                catalog_file=os.path.join(tmpdir, "catalog.db"),
            )
            doc_exact = "/docs/deep_learning.pdf"
            doc_separated = "/docs/general_notes.pdf"

            engine.add_or_update_document(
                doc_exact,
                "Modern neural network architectures use attention mechanisms.",
                save_to_catalog=False,
            )
            engine.add_or_update_document(
                doc_separated,
                "A biological neural pathway is very different from a computer network cable.",
                save_to_catalog=False,
            )

            # 1. Quoted exact phrase must ONLY match `doc_exact`
            exact_hits = search('"neural network"', engine)
            self.assertEqual(exact_hits, [doc_exact])

            # 2. Unquoted query matches both, but ranks `doc_exact` #1 due to adjacency
            broad_hits = search("neural network", engine)
            self.assertEqual(broad_hits[0], doc_exact)
            self.assertIn(doc_separated, broad_hits)

    def test_query_operators_and_fuzzy_typo_search(self):
        """
        Verifies ext:, path:, -exclude operators, and typo-tolerant fuzzy search.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SmartLexEngine(
                index_file=os.path.join(tmpdir, "index.slex"),
                catalog_file=os.path.join(tmpdir, "catalog.db"),
            )
            engine.add_or_update_document(
                "/finance/2024/tax_report.pdf",
                "Annual corporate financial statement and optimization summary",
                save_to_catalog=False,
            )
            engine.add_or_update_document(
                "/finance/2024/tax_draft.docx",
                "Annual corporate financial statement draft version",
                save_to_catalog=False,
            )

            # Extension filter: ext:pdf
            pdf_only = search("financial ext:pdf", engine)
            self.assertEqual(pdf_only, ["/finance/2024/tax_report.pdf"])

            # Exclusion operator: -draft
            no_draft = search("financial -draft", engine)
            self.assertEqual(no_draft, ["/finance/2024/tax_report.pdf"])

            # Path filter: path:finance
            path_hits = search("corporate path:finance", engine)
            self.assertEqual(len(path_hits), 2)

            # Stemming: "optimizing" matches "optimization" / "optimize"
            stem_hits = search("optimizing", engine)
            self.assertEqual(stem_hits, ["/finance/2024/tax_report.pdf"])

            # Fuzzy typo search: "corporat" / "statment" -> "corporate" / "statement"
            fuzzy_hits = search("corporat statment", engine)
            self.assertEqual(len(fuzzy_hits), 2)

    def test_binary_save_load_and_tombstone_removal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            idx_path = os.path.join(tmpdir, "index.slex")
            cat_path = os.path.join(tmpdir, "catalog.db")

            engine = SmartLexEngine(index_file=idx_path, catalog_file=cat_path)
            engine.add_or_update_document(
                "/a.pdf", "alpha beta gamma", save_to_catalog=False
            )
            engine.add_or_update_document(
                "/b.pdf", "beta delta epsilon", save_to_catalog=False
            )
            self.assertTrue(save_index(engine, idx_path))

            loaded = load_index(idx_path, cat_path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(set(search("beta", loaded)), {"/a.pdf", "/b.pdf"})

            # Tombstone remove /a.pdf in O(1) time
            loaded.remove_documents(["/a.pdf"])
            self.assertEqual(len(loaded), 1)
            self.assertEqual(search("beta", loaded), ["/b.pdf"])

    def test_incremental_sync_and_snippets(self):
        """
        Verifies that unchanged files are skipped on subsequent runs and snippets work.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            doc1 = Path(tmpdir) / "invoice_2024.txt"
            doc2 = Path(tmpdir) / "notes.md"
            doc1.write_text(
                "Customer billing statement. Total amount for invoice 2024 is $4,200.",
                encoding="utf-8",
            )
            doc2.write_text(
                "Meeting notes about C++20 inverted index architecture.",
                encoding="utf-8",
            )

            cat_path = os.path.join(tmpdir, "catalog.db")
            idx_path = os.path.join(tmpdir, "index.slex")
            engine = SmartLexEngine(index_file=idx_path, catalog_file=cat_path)

            all_files = [str(doc1), str(doc2)]
            to_index, unchanged, deleted = engine.catalog.plan_incremental_sync(all_files)
            self.assertEqual(len(to_index), 2)
            self.assertEqual(len(unchanged), 0)

            for p in to_index:
                text = extract_text_from_file(p)
                engine.add_or_update_document(p, text)

            # Second sync without modifying files -> 0 files need re-indexing!
            to_index_2, unchanged_2, deleted_2 = engine.catalog.plan_incremental_sync(all_files)
            self.assertEqual(len(to_index_2), 0)
            self.assertEqual(len(unchanged_2), 2)
            self.assertEqual(len(deleted_2), 0)

            # Verify snippet generation
            detailed = search_with_snippets("invoice 2024", engine)
            self.assertEqual(len(detailed), 1)
            self.assertEqual(detailed[0]["path"], str(doc1))
            self.assertIn("invoice 2024", detailed[0]["snippet"].lower())

    def test_docx_table_and_paragraph_extraction(self):
        """
        Creates a minimal valid OpenXML .docx archive containing both a paragraph
        and a table cell, and verifies 100% extraction without needing python-docx.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = os.path.join(tmpdir, "contract.docx")
            document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Confidential Master Service Agreement</w:t></w:r></w:p>
                <w:tbl>
                  <w:tr>
                    <w:tc><w:p><w:r><w:t>Clause 42: Arbitration in Bengaluru</w:t></w:r></w:p></w:tc>
                  </w:tr>
                </w:tbl>
              </w:body>
            </w:document>
            """
            with zipfile.ZipFile(docx_path, "w") as zf:
                zf.writestr("word/document.xml", document_xml)

            extracted = extract_text_from_file(docx_path)
            self.assertIn("Confidential Master Service Agreement", extracted)
            self.assertIn("Clause 42: Arbitration in Bengaluru", extracted)


if __name__ == "__main__":
    unittest.main()

