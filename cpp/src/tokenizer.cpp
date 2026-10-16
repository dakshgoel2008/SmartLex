#include "smartlex/tokenizer.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>

namespace smartlex {

namespace {

inline bool is_ascii_vowel(char c) {
    return c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u' || c == 'y';
}

inline bool contains_vowel(const std::string& s, size_t end_pos) {
    for (size_t i = 0; i < end_pos; ++i) {
        if (is_ascii_vowel(s[i])) {
            return true;
        }
    }
    return false;
}

inline bool contains_digit(const std::string& s) {
    for (unsigned char c : s) {
        if (c >= '0' && c <= '9') {
            return true;
        }
    }
    return false;
}

inline bool is_word_byte(unsigned char c) {
    return (c >= 'a' && c <= 'z') ||
           (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') ||
           (c >= 0x80);  // Preserve UTF-8 multi-byte characters
}

inline char to_lower_ascii(unsigned char c) {
    if (c >= 'A' && c <= 'Z') {
        return static_cast<char>(c + ('a' - 'A'));
    }
    return static_cast<char>(c);
}

inline bool ends_with(const std::string& word, const std::string& suffix) {
    if (word.size() < suffix.size()) {
        return false;
    }
    return word.compare(word.size() - suffix.size(), suffix.size(), suffix) == 0;
}

}  // namespace

std::string Tokenizer::normalize_word(const std::string& word) {
    std::string out;
    out.reserve(word.size());
    for (unsigned char c : word) {
        if (is_word_byte(c)) {
            out.push_back(to_lower_ascii(c));
        }
    }
    return out;
}

std::string Tokenizer::stem_word(const std::string& word) {
    // Never stem short words (<= 3 chars) or tokens with numbers (e.g., "2024", "mp4").
    if (word.size() <= 3 || contains_digit(word)) {
        return word;
    }

    std::string w = word;

    // Step 1: Handle plural suffixes (-sses, -ies, -es, -s)
    if (ends_with(w, "sses")) {
        w.resize(w.size() - 2);  // "classes" -> "class"
    } else if (ends_with(w, "ies") && w.size() > 4) {
        w.resize(w.size() - 3);
        w.push_back('i');        // "studies" -> "studi"
    } else if (ends_with(w, "ss") || ends_with(w, "us") || ends_with(w, "is")) {
        // Keep words like "class", "status", "analysis" unchanged
    } else if ((ends_with(w, "ches") || ends_with(w, "shes") ||
                ends_with(w, "xes") || ends_with(w, "zes")) && w.size() > 4) {
        w.resize(w.size() - 2);  // "searches" -> "search", "boxes" -> "box"
    } else if (ends_with(w, "s") && w.size() > 3) {
        w.resize(w.size() - 1);  // "algorithms" -> "algorithm"
    }

    // Step 2: Handle common derivational suffixes (-ization, -ation, -ment, -ness)
    if (ends_with(w, "ization") && w.size() >= 10) {
        w.resize(w.size() - 7);
        w += "ize";              // "optimization" -> "optimize"
    } else if (ends_with(w, "ation") && w.size() >= 8) {
        w.resize(w.size() - 5);  // "creation" -> "cre" / "generation" -> "gener"
    } else if ((ends_with(w, "ment") || ends_with(w, "ness")) && w.size() >= 8) {
        w.resize(w.size() - 4);  // "management" -> "manage"
    }

    // Step 3: Handle verbal suffixes (-ing, -ed)
    bool stripped_verb_suffix = false;
    if (ends_with(w, "ing") && w.size() >= 6 && contains_vowel(w, w.size() - 3)) {
        w.resize(w.size() - 3);
        stripped_verb_suffix = true;
    } else if (ends_with(w, "ed") && w.size() >= 5 && contains_vowel(w, w.size() - 2)) {
        w.resize(w.size() - 2);
        stripped_verb_suffix = true;
    }

    if (stripped_verb_suffix && w.size() >= 3) {
        // Collapse doubled trailing consonants after stripping -ing/-ed:
        // "running" -> "runn" -> "run", "stopped" -> "stopp" -> "stop"
        char last = w[w.size() - 1];
        char prev = w[w.size() - 2];
        if (last == prev &&
            (last == 'b' || last == 'd' || last == 'g' || last == 'm' ||
             last == 'n' || last == 'p' || last == 'r' || last == 't')) {
            w.pop_back();
        }
    }

    // Step 4: Strip silent trailing 'e' on words >= 5 chars so "optimize" -> "optimiz"
    // matches "optimizing" -> "optimiz" and "optimization" -> "optimiz"
    if (w.size() >= 5 && w.back() == 'e' && contains_vowel(w, w.size() - 1)) {
        w.pop_back();
    }

    // Step 5: Normalize trailing 'y' to 'i' on words > 4 chars so "study" matches "studies"
    if (w.size() > 4 && w.back() == 'y' && !is_ascii_vowel(w[w.size() - 2])) {
        w.back() = 'i';
    }

    return w;
}

std::vector<Token> Tokenizer::tokenize(const std::string& raw_text) {
    std::vector<Token> tokens;
    tokens.reserve(raw_text.size() / 6);

    std::string current;
    current.reserve(32);
    uint32_t position = 0;

    for (unsigned char c : raw_text) {
        if (is_word_byte(c)) {
            current.push_back(to_lower_ascii(c));
        } else if (!current.empty()) {
            tokens.push_back(Token{current, stem_word(current), position++});
            current.clear();
        }
    }

    if (!current.empty()) {
        tokens.push_back(Token{current, stem_word(current), position++});
    }

    return tokens;
}

bool Tokenizer::is_stopword(const std::string& word) {
    static const std::unordered_set<std::string> kStopwords = {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for",
        "from", "had", "has", "have", "he", "her", "his", "how", "i", "if",
        "in", "into", "is", "it", "its", "me", "my", "no", "not", "of",
        "on", "or", "our", "out", "she", "so", "some", "such", "than", "that",
        "the", "their", "them", "then", "there", "these", "they", "this", "to",
        "too", "up", "us", "very", "was", "we", "were", "what", "when", "where",
        "which", "who", "whom", "why", "will", "with", "would", "you", "your"
    };
    return kStopwords.find(word) != kStopwords.end();
}

int Tokenizer::edit_distance(const std::string& a, const std::string& b, int max_distance) {
    const int len_a = static_cast<int>(a.size());
    const int len_b = static_cast<int>(b.size());

    // Fast length difference check: if lengths differ by more than max_distance, abort early.
    if (std::abs(len_a - len_b) > max_distance) {
        return max_distance + 1;
    }

    std::vector<int> prev_row(len_b + 1);
    std::vector<int> curr_row(len_b + 1);

    for (int j = 0; j <= len_b; ++j) {
        prev_row[j] = j;
    }

    for (int i = 1; i <= len_a; ++i) {
        curr_row[0] = i;
        int row_min = curr_row[0];

        for (int j = 1; j <= len_b; ++j) {
            int cost = (a[i - 1] == b[j - 1]) ? 0 : 1;
            curr_row[j] = std::min({
                prev_row[j] + 1,        // deletion
                curr_row[j - 1] + 1,    // insertion
                prev_row[j - 1] + cost  // substitution
            });
            row_min = std::min(row_min, curr_row[j]);
        }

        // Early exit if every cell in the current DP row exceeds max_distance
        if (row_min > max_distance) {
            return max_distance + 1;
        }
        std::swap(prev_row, curr_row);
    }

    return prev_row[len_b];
}

}  // namespace smartlex
