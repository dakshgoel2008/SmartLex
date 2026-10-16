#pragma once

#include <cstdint>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace smartlex {

/**
 * Metadata stored per indexed document.
 */
struct DocumentMeta {
    uint32_t doc_id = 0;
    std::string path;           // Full normalized file path
    std::string filename;       // Basename of the file (e.g., "invoice_2024.pdf")
    std::string extension;      // Lowercase extension including dot (e.g., ".pdf")
    uint32_t body_length = 0;   // Total number of tokens in the document body
    uint64_t mtime_ns = 0;      // Last modified timestamp in nanoseconds
    uint64_t file_size = 0;     // File size in bytes
    bool is_deleted = false;    // Tombstone flag for instant incremental deletion/updates
};

/**
 * Decoded posting for a single document containing a given term.
 */
struct Posting {
    uint32_t doc_id = 0;
    uint32_t title_tf = 0;                  // Occurrences in filename/title
    std::vector<uint32_t> body_positions;   // Sorted 0-based token positions in body text
};

/**
 * Compressed posting list for a single term in the vocabulary.
 * Kept compressed in memory using Delta + VarInt encoding so RAM usage stays minimal.
 */
struct CompressedPostingList {
    uint32_t doc_count = 0;                 // Number of documents in this posting list
    uint32_t last_doc_id = 0;               // Used for delta-encoding consecutive doc_ids
    uint64_t total_frequency = 0;           // Total occurrences across the corpus (for autocomplete)
    std::vector<uint8_t> compressed_bytes;  // VarInt + Delta encoded stream of Postings
};

/**
 * Ranked search result returned by the C++ engine.
 */
struct SearchResult {
    uint32_t doc_id = 0;
    std::string path;
    double score = 0.0;
    uint32_t matched_terms = 0;
    uint32_t first_match_pos = 0;           // Token offset of best match (for snippet generation)
    std::vector<std::string> matched_words; // Words that matched in this document (including fuzzy/stem)
};

/**
 * Parsed representation of a user's search query.
 * Supports:
 *   - Regular keywords:       machine learning 2024
 *   - Exact phrases:          "neural network"
 *   - Extension filter:       ext:pdf  or  type:docx
 *   - Path filter:            path:reports  or  in:downloads
 *   - Excluded keywords:      -draft  -old
 */
struct ParsedQuery {
    std::vector<std::string> terms;
    std::vector<std::vector<std::string>> exact_phrases;
    std::vector<std::string> excluded_terms;
    std::string ext_filter;
    std::unordered_set<std::string> allowed_extensions;
    std::string path_filter;
};

class InvertedIndex {
public:
    InvertedIndex() = default;

    /**
     * Adds or updates a document in the inverted index.
     * If `path` already exists in the index, its previous doc_id is marked as deleted
     * (tombstoned) in O(1) time before adding the new version.
     *
     * Returns the assigned doc_id.
     */
    uint32_t add_document(const std::string& path,
                          const std::string& raw_text,
                          uint64_t mtime_ns = 0,
                          uint64_t file_size = 0);

    /**
     * Marks a document path as deleted in O(1) time using a tombstone flag.
     * Returns true if the document existed and was removed.
     */
    bool remove_document(const std::string& path);

    /**
     * Updates a document's path in O(1) time when a file is moved to another folder.
     */
    bool rename_document(const std::string& old_path, const std::string& new_path);

    /**
     * Checks if a document at `path` is already indexed with the exact same
     * modification timestamp (`mtime_ns`) and `file_size`.
     * Enables zero-wait incremental startup (skipping unchanged files immediately).
     */
    bool is_up_to_date(const std::string& path, uint64_t mtime_ns, uint64_t file_size) const;

    /**
     * Executes a full search query using BM25F + Exact Phrase + Proximity + Fuzzy Typo Tolerance.
     */
    std::vector<SearchResult> search(const std::string& raw_query, size_t max_results = 100) const;

    /**
     * Returns the top prefix-matched or frequency-ranked autocomplete words.
     */
    std::vector<std::string> suggest_autocomplete(const std::string& prefix = "",
                                                  size_t max_words = 100) const;

    /**
     * Returns all indexed keywords for a given file path (for backwards compatibility).
     */
    std::vector<std::string> get_document_keywords(const std::string& path,
                                                   size_t max_keywords = 150) const;

    /**
     * Saves the compressed binary index to disk (`.slex` format).
     * Automatically compacts out any deleted/tombstoned documents during save.
     */
    bool save_binary(const std::string& file_path) const;

    /**
     * Loads a compressed binary index from disk (`.slex` format).
     */
    bool load_binary(const std::string& file_path);

    /**
     * Parses a raw query string into terms, phrases, and filters.
     */
    static ParsedQuery parse_query(const std::string& raw_query);

    /**
     * Number of active (non-deleted) documents in the index.
     */
    size_t active_document_count() const;

    /**
     * Total number of unique terms in the inverted index dictionary.
     */
    size_t vocabulary_size() const;

    /**
     * Returns all active document paths currently in the index.
     */
    std::vector<std::string> get_all_paths() const;

private:
    bool remove_document_unlocked(const std::string& path);

    // Appends a single Posting into a CompressedPostingList
    static void append_posting(CompressedPostingList& list, const Posting& posting);

    // Decodes all Postings from a CompressedPostingList
    static std::vector<Posting> decode_postings(const CompressedPostingList& list);

    // Finds the best fuzzy match in the vocabulary within Levenshtein distance <= 2
    std::string find_fuzzy_replacement(const std::string& term) const;

    // Computes all doc_ids containing the exact consecutive phrase in a single O(N) pass
    std::unordered_set<uint32_t> compute_phrase_matching_docs(
        const std::vector<std::string>& phrase) const;

    mutable std::shared_mutex mutex_;
    std::vector<DocumentMeta> documents_;
    std::unordered_map<std::string, uint32_t> path_to_doc_id_;

    // Exact normalized word -> compressed positional posting list
    std::unordered_map<std::string, CompressedPostingList> exact_postings_;

    // Stemmed root word -> compressed positional posting list (for morphological recall)
    std::unordered_map<std::string, CompressedPostingList> stem_postings_;

    size_t active_doc_count_ = 0;
    uint64_t total_body_tokens_ = 0;
};

}  // namespace smartlex
