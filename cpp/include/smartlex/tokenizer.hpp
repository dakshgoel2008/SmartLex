#pragma once

#include <cstdint>
#include <string>
#include <unordered_set>
#include <vector>

namespace smartlex {

// A token with its word position, used for phrase matching and proximity ranking.
struct Token {
    std::string text;       // Normalized lowercase token (e.g., "learning", "2024")
    std::string stem;       // Stemmed root form (e.g., "learn", "2024")
    uint32_t position;      // Word index in the document (0, 1, 2, ...)
};

class Tokenizer {
public:
    // Splits text into lowercase tokens with positions. Numbers and identifiers are kept.
    static std::vector<Token> tokenize(const std::string& raw_text);

    // Lowercases a word and drops punctuation.
    static std::string normalize_word(const std::string& word);

    // Light English suffix stemmer, e.g. "algorithms" -> "algorithm", "running" -> "run".
    static std::string stem_word(const std::string& word);

    // Stopwords stay in the index for phrase search; this only filters autocomplete.
    static bool is_stopword(const std::string& word);

    // Levenshtein distance, stopping early once it exceeds max_distance.
    static int edit_distance(const std::string& a, const std::string& b, int max_distance = 2);
};

}  // namespace smartlex

