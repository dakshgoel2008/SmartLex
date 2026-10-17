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

inline bool ends_with(const std::string& word, const std::string& suffix) {
    if (word.size() < suffix.size()) {
        return false;
    }
    return word.compare(word.size() - suffix.size(), suffix.size(), suffix) == 0;
}

enum class CharKind { Word, Separator, Ignorable };

// Unicode punctuation and symbols (dashes, quotes, bullets, emoji, ...) separate words;
// letters and digits of every script are part of words.
CharKind classify_code_point(uint32_t cp) {
    // Soft hyphen and zero-width (non-)joiners sit inside words without splitting them.
    if (cp == 0x00AD || cp == 0x200C || cp == 0x200D) {
        return CharKind::Ignorable;
    }
    if (cp >= 0x00A0 && cp <= 0x00BF) {
        // Latin-1 punctuation, except the letter/number-like characters in this range.
        bool letter_like = cp == 0x00AA || cp == 0x00B2 || cp == 0x00B3 || cp == 0x00B5 ||
                           cp == 0x00B9 || cp == 0x00BA || (cp >= 0x00BC && cp <= 0x00BE);
        return letter_like ? CharKind::Word : CharKind::Separator;
    }
    if (cp == 0x00D7 || cp == 0x00F7) {  // multiplication and division signs
        return CharKind::Separator;
    }
    if ((cp >= 0x2000 && cp <= 0x206F) ||    // general punctuation and spaces
        (cp >= 0x2190 && cp <= 0x21FF) ||    // arrows
        (cp >= 0x2500 && cp <= 0x27BF) ||    // box drawing, shapes, symbols, dingbats
        (cp >= 0x3000 && cp <= 0x3004) ||    // CJK space and punctuation
        (cp >= 0x3008 && cp <= 0x3011) ||    // CJK brackets
        (cp >= 0xFF01 && cp <= 0xFF0F) ||    // fullwidth punctuation
        (cp >= 0xFF1A && cp <= 0xFF20) ||
        (cp >= 0xFF3B && cp <= 0xFF40) ||
        (cp >= 0xFF5B && cp <= 0xFF65) ||
        cp == 0xFEFF ||                      // byte order mark
        (cp >= 0x1F300 && cp <= 0x1FAFF)) {  // emoji
        return CharKind::Separator;
    }
    return CharKind::Word;
}

constexpr uint32_t kInvalidCodePoint = 0xFFFFFFFF;

// Reads the UTF-8 character at text[pos] and advances pos past it. Invalid bytes are
// returned one at a time as word bytes with code_point = kInvalidCodePoint.
CharKind read_char(const std::string& text, size_t& pos, uint32_t& code_point) {
    code_point = kInvalidCodePoint;
    unsigned char lead = static_cast<unsigned char>(text[pos]);
    if (lead < 0x80) {
        pos += 1;
        code_point = lead;
        return is_word_byte(lead) ? CharKind::Word : CharKind::Separator;
    }

    size_t length = 0;
    if ((lead & 0xE0) == 0xC0) {
        length = 2;
    } else if ((lead & 0xF0) == 0xE0) {
        length = 3;
    } else if ((lead & 0xF8) == 0xF0) {
        length = 4;
    }
    if (length == 0 || pos + length > text.size()) {
        pos += 1;
        return CharKind::Word;
    }

    uint32_t cp = lead & (0x7F >> length);
    for (size_t k = 1; k < length; ++k) {
        unsigned char next = static_cast<unsigned char>(text[pos + k]);
        if ((next & 0xC0) != 0x80) {
            pos += 1;
            return CharKind::Word;
        }
        cp = (cp << 6) | (next & 0x3F);
    }
    pos += length;
    code_point = cp;
    return classify_code_point(cp);
}

// Lowercases ASCII, Latin-1, Latin Extended-A, Greek and Cyrillic capital letters.
uint32_t to_lower_code_point(uint32_t cp) {
    if (cp >= 'A' && cp <= 'Z') return cp + 0x20;
    if (cp >= 0x00C0 && cp <= 0x00DE && cp != 0x00D7) return cp + 0x20;
    if (cp == 0x0130) return 'i';  // capital I with dot above
    if (cp >= 0x0100 && cp <= 0x0137 && cp % 2 == 0) return cp + 1;
    if (cp >= 0x0139 && cp <= 0x0148 && cp % 2 == 1) return cp + 1;
    if (cp >= 0x014A && cp <= 0x0177 && cp % 2 == 0) return cp + 1;
    if (cp == 0x0178) return 0x00FF;
    if (cp >= 0x0179 && cp <= 0x017E && cp % 2 == 1) return cp + 1;
    if (cp >= 0x0391 && cp <= 0x03A9 && cp != 0x03A2) return cp + 0x20;
    if (cp >= 0x0410 && cp <= 0x042F) return cp + 0x20;
    if (cp >= 0x0400 && cp <= 0x040F) return cp + 0x50;
    return cp;
}

void append_utf8(std::string& out, uint32_t cp) {
    if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
}

// Appends the lowercase form of the character text[start, end) to `out`.
void append_lowercase_char(std::string& out, const std::string& text,
                           size_t start, size_t end, uint32_t code_point) {
    if (code_point == kInvalidCodePoint) {
        out.append(text, start, end - start);
        return;
    }
    append_utf8(out, to_lower_code_point(code_point));
}

}  // namespace

std::string Tokenizer::normalize_word(const std::string& word) {
    std::string out;
    out.reserve(word.size());
    size_t pos = 0;
    uint32_t code_point = 0;
    while (pos < word.size()) {
        size_t start = pos;
        if (read_char(word, pos, code_point) == CharKind::Word) {
            append_lowercase_char(out, word, start, pos, code_point);
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

    size_t pos = 0;
    uint32_t code_point = 0;
    while (pos < raw_text.size()) {
        size_t start = pos;
        CharKind kind = read_char(raw_text, pos, code_point);
        if (kind == CharKind::Word) {
            append_lowercase_char(current, raw_text, start, pos, code_point);
        } else if (kind == CharKind::Separator && !current.empty()) {
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
