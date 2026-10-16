#pragma once

#include <cstdint>
#include <string>
#include <unordered_set>
#include <vector>

namespace smartlex {

/**
 * Represents a single extracted token and its sequential word position (0-indexed)
 * inside a document. Storing positions allows 100% accurate "exact phrase" matching
 * and proximity ranking (boosting documents where search words appear close together).
 */
struct Token {
    std::string text;       // Normalized lowercase token (e.g., "learning", "2024")
    std::string stem;       // Stemmed root form (e.g., "learn", "2024")
    uint32_t position;      // Word index in the document (0, 1, 2, ...)
};

class Tokenizer {
public:
    /**
     * Tokenizes raw text into a sequence of lowercase alphanumeric tokens with positions.
     * Unlike the legacy RAKE implementation, this NEVER drops numbers (like "2024"),
     * technical identifiers, or words next to punctuation.
     */
    static std::vector<Token> tokenize(const std::string& raw_text);

    /**
     * Normalizes a single word to lowercase ASCII/UTF-8 safe characters.
     */
    static std::string normalize_word(const std::string& word);

    /**
     * Light, deterministic English stemmer (Porter-style suffix stripping).
     * Maps inflected forms to a common root:
     *   "algorithms" -> "algorithm"
     *   "running"    -> "run"
     *   "studies"    -> "studi"
     *   "connected"  -> "connect"
     */
    static std::string stem_word(const std::string& word);

    /**
     * Returns true if a word is a common English stopword (like "the", "and", "of").
     * Note: We STILL index stopwords in the positional index for 100% phrase accuracy,
     * but we use this check to keep autocomplete suggestions clean and meaningful.
     */
    static bool is_stopword(const std::string& word);

    /**
     * Computes the exact Levenshtein edit distance between two strings (insertions,
     * deletions, or substitutions). Used for typo-tolerant fuzzy search.
     */
    static int edit_distance(const std::string& a, const std::string& b, int max_distance = 2);
};

}  // namespace smartlex

