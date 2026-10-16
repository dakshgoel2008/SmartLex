#include "smartlex/inverted_index.hpp"
#include "smartlex/tokenizer.hpp"
#include "smartlex/varint.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>
#include <unordered_set>

namespace smartlex {

namespace {

constexpr char kMagicHeader[4] = {'S', 'L', 'E', 'X'};
constexpr uint32_t kFormatVersion = 1;

// Extract basename and lowercase extension from a file path
void split_path_info(const std::string& path, std::string& out_filename, std::string& out_ext) {
    size_t last_slash = path.find_last_of("/\\");
    out_filename = (last_slash == std::string::npos) ? path : path.substr(last_slash + 1);

    size_t last_dot = out_filename.find_last_of('.');
    if (last_dot != std::string::npos && last_dot > 0) {
        out_ext = out_filename.substr(last_dot);
        for (char& c : out_ext) {
            if (c >= 'A' && c <= 'Z') {
                c = static_cast<char>(c + ('a' - 'A'));
            }
        }
    } else {
        out_ext.clear();
    }
}

std::string to_lower_copy(const std::string& s) {
    std::string out = s;
    for (char& c : out) {
        if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c + ('a' - 'A'));
        }
    }
    return out;
}

void write_string(std::ofstream& out, const std::string& s) {
    uint32_t len = static_cast<uint32_t>(s.size());
    out.write(reinterpret_cast<const char*>(&len), sizeof(len));
    if (len > 0) {
        out.write(s.data(), len);
    }
}

std::string read_string(std::ifstream& in) {
    uint32_t len = 0;
    in.read(reinterpret_cast<char*>(&len), sizeof(len));
    if (!in || len == 0) {
        return "";
    }
    std::string s(len, '\0');
    in.read(&s[0], len);
    return s;
}

}  // namespace

void InvertedIndex::append_posting(CompressedPostingList& list, const Posting& posting) {
    uint32_t doc_delta = posting.doc_id - list.last_doc_id;
    VarIntCodec::encode_uint32(doc_delta, list.compressed_bytes);
    VarIntCodec::encode_uint32(posting.title_tf, list.compressed_bytes);
    VarIntCodec::encode_delta_list(posting.body_positions, list.compressed_bytes);

    list.last_doc_id = posting.doc_id;
    list.doc_count += 1;
    list.total_frequency += posting.title_tf + posting.body_positions.size();
}

std::vector<Posting> InvertedIndex::decode_postings(const CompressedPostingList& list) {
    std::vector<Posting> result;
    result.reserve(list.doc_count);

    const uint8_t* data = list.compressed_bytes.data();
    const size_t size = list.compressed_bytes.size();
    size_t offset = 0;
    uint32_t prev_doc_id = 0;

    for (uint32_t i = 0; i < list.doc_count; ++i) {
        Posting p;
        uint32_t doc_delta = VarIntCodec::decode_uint32(data, size, offset);
        p.doc_id = prev_doc_id + doc_delta;
        prev_doc_id = p.doc_id;

        p.title_tf = VarIntCodec::decode_uint32(data, size, offset);
        p.body_positions = VarIntCodec::decode_delta_list(data, size, offset);
        result.push_back(std::move(p));
    }

    return result;
}

bool InvertedIndex::remove_document(const std::string& path) {
    auto it = path_to_doc_id_.find(path);
    if (it == path_to_doc_id_.end()) {
        return false;
    }

    uint32_t doc_id = it->second;
    if (doc_id < documents_.size() && !documents_[doc_id].is_deleted) {
        documents_[doc_id].is_deleted = true;
        if (active_doc_count_ > 0) {
            active_doc_count_ -= 1;
        }
        if (total_body_tokens_ >= documents_[doc_id].body_length) {
            total_body_tokens_ -= documents_[doc_id].body_length;
        }
    }
    path_to_doc_id_.erase(it);
    return true;
}

bool InvertedIndex::is_up_to_date(const std::string& path,
                                  uint64_t mtime_ns,
                                  uint64_t file_size) const {
    if (mtime_ns == 0 && file_size == 0) {
        return false;
    }
    auto it = path_to_doc_id_.find(path);
    if (it == path_to_doc_id_.end()) {
        return false;
    }
    const DocumentMeta& meta = documents_[it->second];
    return !meta.is_deleted && meta.mtime_ns == mtime_ns && meta.file_size == file_size;
}

uint32_t InvertedIndex::add_document(const std::string& path,
                                     const std::string& raw_text,
                                     uint64_t mtime_ns,
                                     uint64_t file_size) {
    // If this path was already indexed, tombstone the old version first
    remove_document(path);

    uint32_t doc_id = static_cast<uint32_t>(documents_.size());

    DocumentMeta meta;
    meta.doc_id = doc_id;
    meta.path = path;
    split_path_info(path, meta.filename, meta.extension);
    meta.mtime_ns = mtime_ns;
    meta.file_size = file_size;
    meta.is_deleted = false;

    // Tokenize both the filename (title) and the document body text
    std::vector<Token> title_tokens = Tokenizer::tokenize(meta.filename);
    std::vector<Token> body_tokens = Tokenizer::tokenize(raw_text);
    meta.body_length = static_cast<uint32_t>(body_tokens.size());

    documents_.push_back(meta);
    path_to_doc_id_[path] = doc_id;
    active_doc_count_ += 1;
    total_body_tokens_ += meta.body_length;

    // Aggregate postings for this document in memory before compressing
    std::unordered_map<std::string, Posting> doc_exact_postings;
    std::unordered_map<std::string, Posting> doc_stem_postings;

    for (const Token& tok : title_tokens) {
        Posting& exact_p = doc_exact_postings[tok.text];
        exact_p.doc_id = doc_id;
        exact_p.title_tf += 1;

        if (tok.stem != tok.text) {
            Posting& stem_p = doc_stem_postings[tok.stem];
            stem_p.doc_id = doc_id;
            stem_p.title_tf += 1;
        }
    }

    for (const Token& tok : body_tokens) {
        Posting& exact_p = doc_exact_postings[tok.text];
        exact_p.doc_id = doc_id;
        exact_p.body_positions.push_back(tok.position);

        if (tok.stem != tok.text) {
            Posting& stem_p = doc_stem_postings[tok.stem];
            stem_p.doc_id = doc_id;
            if (stem_p.body_positions.empty() || stem_p.body_positions.back() != tok.position) {
                stem_p.body_positions.push_back(tok.position);
            }
        }
    }

    // Append compressed postings into global vocabulary tables
    for (const auto& [term, posting] : doc_exact_postings) {
        append_posting(exact_postings_[term], posting);
    }

    for (const auto& [stem, posting] : doc_stem_postings) {
        append_posting(stem_postings_[stem], posting);
    }

    return doc_id;
}

ParsedQuery InvertedIndex::parse_query(const std::string& raw_query) {
    ParsedQuery parsed;

    // Step 1: Extract quoted exact phrases: e.g. "neural network"
    std::string remaining;
    remaining.reserve(raw_query.size());

    size_t i = 0;
    while (i < raw_query.size()) {
        if (raw_query[i] == '"') {
            size_t closing = raw_query.find('"', i + 1);
            if (closing != std::string::npos) {
                std::string phrase_str = raw_query.substr(i + 1, closing - i - 1);
                std::vector<Token> phrase_tokens = Tokenizer::tokenize(phrase_str);
                if (!phrase_tokens.empty()) {
                    std::vector<std::string> phrase_words;
                    phrase_words.reserve(phrase_tokens.size());
                    for (const Token& t : phrase_tokens) {
                        phrase_words.push_back(t.text);
                        parsed.terms.push_back(t.text);
                    }
                    parsed.exact_phrases.push_back(std::move(phrase_words));
                }
                i = closing + 1;
                continue;
            }
        }
        remaining.push_back(raw_query[i]);
        ++i;
    }

    // Step 2: Parse whitespace-delimited chunks for operators (ext:, path:, -exclude)
    std::vector<std::string> raw_words;
    size_t start = 0;
    while (start < remaining.size()) {
        while (start < remaining.size() && std::isspace(static_cast<unsigned char>(remaining[start]))) {
            ++start;
        }
        if (start >= remaining.size()) {
            break;
        }
        size_t end = start;
        while (end < remaining.size() && !std::isspace(static_cast<unsigned char>(remaining[end]))) {
            ++end;
        }
        raw_words.push_back(remaining.substr(start, end - start));
        start = end;
    }

    std::vector<std::string> candidate_terms;
    for (const std::string& chunk : raw_words) {
        std::string lower_chunk = to_lower_copy(chunk);

        if (lower_chunk.rfind("ext:", 0) == 0 || lower_chunk.rfind("type:", 0) == 0) {
            size_t colon = lower_chunk.find(':');
            std::string ext = lower_chunk.substr(colon + 1);
            if (!ext.empty()) {
                parsed.ext_filter = (ext[0] == '.') ? ext : ("." + ext);
            }
        } else if (lower_chunk.rfind("path:", 0) == 0 || lower_chunk.rfind("in:", 0) == 0) {
            size_t colon = lower_chunk.find(':');
            parsed.path_filter = lower_chunk.substr(colon + 1);
        } else if (lower_chunk.size() > 1 && lower_chunk[0] == '-') {
            std::string excl = Tokenizer::normalize_word(lower_chunk.substr(1));
            if (!excl.empty()) {
                parsed.excluded_terms.push_back(excl);
            }
        } else {
            std::vector<Token> sub_tokens = Tokenizer::tokenize(chunk);
            for (const Token& t : sub_tokens) {
                candidate_terms.push_back(t.text);
            }
        }
    }

    // Filter out stopwords from unquoted terms ONLY if at least one meaningful word exists
    std::vector<std::string> non_stopwords;
    for (const std::string& term : candidate_terms) {
        if (!Tokenizer::is_stopword(term)) {
            non_stopwords.push_back(term);
        }
    }

    if (!non_stopwords.empty()) {
        parsed.terms.insert(parsed.terms.end(), non_stopwords.begin(), non_stopwords.end());
    } else {
        parsed.terms.insert(parsed.terms.end(), candidate_terms.begin(), candidate_terms.end());
    }

    return parsed;
}

std::string InvertedIndex::find_fuzzy_replacement(const std::string& term) const {
    // Only apply fuzzy typo tolerance to words of length >= 4 without digits
    if (term.size() < 4) {
        return "";
    }
    for (char c : term) {
        if (c >= '0' && c <= '9') {
            return "";
        }
    }

    const int max_dist = (term.size() >= 7) ? 2 : 1;
    int best_dist = max_dist + 1;
    uint64_t best_freq = 0;
    std::string best_match;

    for (const auto& [vocab_word, plist] : exact_postings_) {
        if (plist.doc_count == 0 || vocab_word.empty()) {
            continue;
        }
        // Require matching first letter to keep fuzzy search fast and high-precision
        if (vocab_word[0] != term[0]) {
            continue;
        }
        int dist = Tokenizer::edit_distance(term, vocab_word, max_dist);
        if (dist <= max_dist) {
            if (dist < best_dist || (dist == best_dist && plist.total_frequency > best_freq)) {
                best_dist = dist;
                best_freq = plist.total_frequency;
                best_match = vocab_word;
            }
        }
    }

    return best_match;
}

std::unordered_set<uint32_t> InvertedIndex::compute_phrase_matching_docs(
    const std::vector<std::string>& phrase) const {
    std::unordered_set<uint32_t> matching_docs;
    if (phrase.empty()) {
        return matching_docs;
    }

    // Decode each phrase word's posting list ONCE in O(N) time
    std::vector<std::unordered_map<uint32_t, std::vector<uint32_t>>> word_doc_positions(phrase.size());
    for (size_t i = 0; i < phrase.size(); ++i) {
        auto it = exact_postings_.find(phrase[i]);
        if (it == exact_postings_.end()) {
            return matching_docs;
        }
        for (Posting& p : decode_postings(it->second)) {
            if (!p.body_positions.empty()) {
                word_doc_positions[i].emplace(p.doc_id, std::move(p.body_positions));
            }
        }
    }

    // Iterate over documents containing the first word of the phrase
    for (const auto& [doc_id, first_positions] : word_doc_positions[0]) {
        bool doc_has_all_words = true;
        for (size_t k = 1; k < phrase.size(); ++k) {
            if (word_doc_positions[k].find(doc_id) == word_doc_positions[k].end()) {
                doc_has_all_words = false;
                break;
            }
        }
        if (!doc_has_all_words) {
            continue;
        }

        // Check if any starting position `pos` has consecutive positions `pos + k`
        for (uint32_t start_pos : first_positions) {
            bool all_consecutive = true;
            for (size_t k = 1; k < phrase.size(); ++k) {
                uint32_t target = start_pos + static_cast<uint32_t>(k);
                const auto& vec = word_doc_positions[k].at(doc_id);
                if (!std::binary_search(vec.begin(), vec.end(), target)) {
                    all_consecutive = false;
                    break;
                }
            }
            if (all_consecutive) {
                matching_docs.insert(doc_id);
                break;
            }
        }
    }

    return matching_docs;
}

std::vector<SearchResult> InvertedIndex::search(const std::string& raw_query,
                                                size_t max_results) const {
    if (active_doc_count_ == 0) {
        return {};
    }

    ParsedQuery parsed = parse_query(raw_query);
    if (parsed.terms.empty() && parsed.exact_phrases.empty() &&
        parsed.ext_filter.empty() && parsed.path_filter.empty()) {
        return {};
    }

    // 1. Collect excluded doc_ids from `-term` operators
    std::unordered_set<uint32_t> excluded_docs;
    for (const std::string& excl : parsed.excluded_terms) {
        auto it = exact_postings_.find(excl);
        if (it != exact_postings_.end()) {
            for (const Posting& p : decode_postings(it->second)) {
                excluded_docs.insert(p.doc_id);
            }
        }
    }

    // Helper to check if a document passes metadata filters (not deleted, ext:, path:, -excl)
    auto passes_filters = [&](uint32_t doc_id) -> bool {
        if (doc_id >= documents_.size()) return false;
        const DocumentMeta& meta = documents_[doc_id];
        if (meta.is_deleted) return false;
        if (excluded_docs.count(doc_id) > 0) return false;
        if (!parsed.ext_filter.empty() && meta.extension != parsed.ext_filter) {
            return false;
        }
        if (!parsed.path_filter.empty()) {
            if (to_lower_copy(meta.path).find(parsed.path_filter) == std::string::npos) {
                return false;
            }
        }
        return true;
    };

    // If the user only typed filters like `ext:pdf` or `path:reports` with no keywords:
    if (parsed.terms.empty() && parsed.exact_phrases.empty()) {
        std::vector<SearchResult> filter_results;
        for (const DocumentMeta& meta : documents_) {
            if (passes_filters(meta.doc_id)) {
                filter_results.push_back(SearchResult{meta.doc_id, meta.path, 1.0, 1, 0, {}});
                if (filter_results.size() >= max_results) break;
            }
        }
        return filter_results;
    }

    // 2. Accumulate BM25F scores and term positions per candidate document
    struct DocAccumulator {
        double score = 0.0;
        uint32_t matched_terms = 0;
        uint32_t first_match_pos = std::numeric_limits<uint32_t>::max();
        std::vector<std::string> matched_words;
        std::vector<uint32_t> representative_positions;
    };

    std::unordered_map<uint32_t, DocAccumulator> accumulators;

    const double avg_doc_len = (active_doc_count_ > 0 && total_body_tokens_ > 0)
        ? (static_cast<double>(total_body_tokens_) / static_cast<double>(active_doc_count_))
        : 100.0;
    constexpr double k1 = 1.2;
    constexpr double b = 0.75;

    // Deduplicate query terms while preserving order
    std::vector<std::string> unique_query_terms;
    for (const std::string& t : parsed.terms) {
        if (std::find(unique_query_terms.begin(), unique_query_terms.end(), t) == unique_query_terms.end()) {
            unique_query_terms.push_back(t);
        }
    }

    for (const std::string& q_term : unique_query_terms) {
        std::unordered_map<uint32_t, Posting> term_doc_matches;
        std::unordered_map<uint32_t, double> term_match_weight;
        std::unordered_map<uint32_t, std::string> matched_token_str;

        // Pass A: Exact word match (weight = 1.0)
        auto exact_it = exact_postings_.find(q_term);
        if (exact_it != exact_postings_.end()) {
            for (Posting& p : decode_postings(exact_it->second)) {
                if (passes_filters(p.doc_id)) {
                    uint32_t doc_id = p.doc_id;
                    term_doc_matches[doc_id] = std::move(p);
                    term_match_weight[doc_id] = 1.0;
                    matched_token_str[doc_id] = q_term;
                }
            }
        }

        // Pass B: Stemmed morphological match (weight = 0.85) for docs not already matched
        std::string q_stem = Tokenizer::stem_word(q_term);
        if (q_stem != q_term) {
            auto exact_stem_it = exact_postings_.find(q_stem);
            if (exact_stem_it != exact_postings_.end()) {
                for (Posting& p : decode_postings(exact_stem_it->second)) {
                    if (passes_filters(p.doc_id) && term_doc_matches.find(p.doc_id) == term_doc_matches.end()) {
                        uint32_t doc_id = p.doc_id;
                        term_doc_matches[doc_id] = std::move(p);
                        term_match_weight[doc_id] = 0.85;
                        matched_token_str[doc_id] = q_stem;
                    }
                }
            }
        }
        auto stem_it = stem_postings_.find(q_stem);
        if (stem_it != stem_postings_.end()) {
            for (Posting& p : decode_postings(stem_it->second)) {
                if (passes_filters(p.doc_id) && term_doc_matches.find(p.doc_id) == term_doc_matches.end()) {
                    uint32_t doc_id = p.doc_id;
                    term_doc_matches[doc_id] = std::move(p);
                    term_match_weight[doc_id] = 0.85;
                    matched_token_str[doc_id] = q_term;
                }
            }
        }

        // Pass C: Fuzzy Typo Tolerance (weight = 0.70) if neither Exact nor Stem matched any active doc
        if (term_doc_matches.empty()) {
            std::string fuzzy_word = find_fuzzy_replacement(q_term);
            if (!fuzzy_word.empty()) {
                auto fuzzy_it = exact_postings_.find(fuzzy_word);
                if (fuzzy_it != exact_postings_.end()) {
                    for (Posting& p : decode_postings(fuzzy_it->second)) {
                        if (passes_filters(p.doc_id)) {
                            uint32_t doc_id = p.doc_id;
                            term_doc_matches[doc_id] = std::move(p);
                            term_match_weight[doc_id] = 0.70;
                            matched_token_str[doc_id] = fuzzy_word;
                        }
                    }
                }
            }
        }

        if (term_doc_matches.empty()) {
            continue;
        }

        // Compute Okapi BM25F score for this query term across matching documents
        const double df = static_cast<double>(term_doc_matches.size());
        const double idf = std::log(1.0 + (static_cast<double>(active_doc_count_) - df + 0.5) / (df + 0.5));

        for (const auto& [doc_id, posting] : term_doc_matches) {
            const DocumentMeta& meta = documents_[doc_id];
            const double body_tf = static_cast<double>(posting.body_positions.size());
            const double title_tf = static_cast<double>(posting.title_tf);

            // Title/filename matches count 5x as much as body occurrences (BM25F field boost)
            const double weighted_tf = body_tf + 5.0 * title_tf;
            const double doc_len = static_cast<double>(std::max<uint32_t>(1, meta.body_length));
            const double norm_tf = (weighted_tf * (k1 + 1.0)) /
                                   (weighted_tf + k1 * (1.0 - b + b * (doc_len / avg_doc_len)));

            const double term_score = idf * norm_tf * term_match_weight[doc_id];

            DocAccumulator& acc = accumulators[doc_id];
            acc.score += term_score;
            acc.matched_terms += 1;
            acc.matched_words.push_back(matched_token_str[doc_id]);

            if (!posting.body_positions.empty()) {
                uint32_t first_pos = posting.body_positions.front();
                acc.first_match_pos = std::min(acc.first_match_pos, first_pos);
                acc.representative_positions.push_back(first_pos);
            }
        }
    }

    // 3. Precompute exact phrase matching doc_id sets in O(N) single pass
    std::vector<std::unordered_set<uint32_t>> phrase_doc_sets;
    phrase_doc_sets.reserve(parsed.exact_phrases.size());
    for (const auto& phrase : parsed.exact_phrases) {
        phrase_doc_sets.push_back(compute_phrase_matching_docs(phrase));
    }

    // 4. Apply Exact Phrase Verification, Proximity Bonus, and Query Coverage Multiplier
    std::vector<SearchResult> results;
    results.reserve(accumulators.size());

    for (auto& [doc_id, acc] : accumulators) {
        // Verify all quoted exact phrases (O(1) lookup per phrase)
        bool phrases_ok = true;
        for (const auto& valid_set : phrase_doc_sets) {
            if (valid_set.find(doc_id) == valid_set.end()) {
                phrases_ok = false;
                break;
            }
        }
        if (!phrases_ok) {
            continue;
        }

        // Boost documents that match a higher fraction of the query terms
        if (unique_query_terms.size() > 1) {
            double coverage_ratio = static_cast<double>(acc.matched_terms) /
                                    static_cast<double>(unique_query_terms.size());
            acc.score *= (1.0 + 1.5 * coverage_ratio);
        }

        // Proximity boost: reward documents where query terms appear close together
        if (acc.representative_positions.size() >= 2) {
            for (size_t i = 1; i < acc.representative_positions.size(); ++i) {
                uint32_t pos_a = acc.representative_positions[i - 1];
                uint32_t pos_b = acc.representative_positions[i];
                uint32_t dist = (pos_b >= pos_a) ? (pos_b - pos_a) : (pos_a - pos_b);
                if (dist == 1) {
                    acc.score += 2.5;  // Exact adjacent words!
                } else if (dist <= 5) {
                    acc.score += 1.0 / static_cast<double>(dist);
                }
            }
        }

        uint32_t snippet_pos = (acc.first_match_pos == std::numeric_limits<uint32_t>::max())
                                   ? 0
                                   : acc.first_match_pos;

        results.push_back(SearchResult{
            doc_id,
            documents_[doc_id].path,
            acc.score,
            acc.matched_terms,
            snippet_pos,
            std::move(acc.matched_words)
        });
    }

    // Sort descending by score, breaking ties by number of matched terms and path
    std::sort(results.begin(), results.end(), [](const SearchResult& a, const SearchResult& b) {
        if (std::abs(a.score - b.score) > 1e-9) {
            return a.score > b.score;
        }
        if (a.matched_terms != b.matched_terms) {
            return a.matched_terms > b.matched_terms;
        }
        return a.path < b.path;
    });

    if (results.size() > max_results) {
        results.resize(max_results);
    }

    return results;
}

std::vector<std::string> InvertedIndex::suggest_autocomplete(const std::string& prefix,
                                                             size_t max_words) const {
    std::string norm_prefix = Tokenizer::normalize_word(prefix);

    struct WordFreq {
        std::string word;
        uint64_t freq;
    };
    std::vector<WordFreq> candidates;
    candidates.reserve(exact_postings_.size() / 2);

    for (const auto& [word, plist] : exact_postings_) {
        if (plist.doc_count == 0 || word.size() < 3) {
            continue;
        }
        if (Tokenizer::is_stopword(word)) {
            continue;
        }
        if (!norm_prefix.empty() && word.rfind(norm_prefix, 0) != 0) {
            continue;
        }
        candidates.push_back(WordFreq{word, plist.total_frequency});
    }

    std::sort(candidates.begin(), candidates.end(), [](const WordFreq& a, const WordFreq& b) {
        if (a.freq != b.freq) {
            return a.freq > b.freq;
        }
        return a.word < b.word;
    });

    if (candidates.size() > max_words) {
        candidates.resize(max_words);
    }

    std::vector<std::string> out;
    out.reserve(candidates.size());
    for (const auto& item : candidates) {
        out.push_back(item.word);
    }
    return out;
}

std::vector<std::string> InvertedIndex::get_document_keywords(const std::string& path,
                                                              size_t max_keywords) const {
    auto it = path_to_doc_id_.find(path);
    if (it == path_to_doc_id_.end()) {
        return {};
    }
    uint32_t target_doc_id = it->second;

    struct TermCount {
        std::string term;
        size_t count;
    };
    std::vector<TermCount> doc_terms;

    for (const auto& [term, plist] : exact_postings_) {
        if (term.size() < 3 || Tokenizer::is_stopword(term)) {
            continue;
        }
        for (const Posting& p : decode_postings(plist)) {
            if (p.doc_id == target_doc_id) {
                doc_terms.push_back(TermCount{term, p.title_tf + p.body_positions.size()});
                break;
            }
        }
    }

    std::sort(doc_terms.begin(), doc_terms.end(), [](const TermCount& a, const TermCount& b) {
        if (a.count != b.count) return a.count > b.count;
        return a.term < b.term;
    });

    if (doc_terms.size() > max_keywords) {
        doc_terms.resize(max_keywords);
    }

    std::vector<std::string> out;
    out.reserve(doc_terms.size());
    for (const auto& tc : doc_terms) {
        out.push_back(tc.term);
    }
    return out;
}

std::vector<std::string> InvertedIndex::get_all_paths() const {
    std::vector<std::string> paths;
    paths.reserve(active_doc_count_);
    for (const DocumentMeta& meta : documents_) {
        if (!meta.is_deleted) {
            paths.push_back(meta.path);
        }
    }
    return paths;
}

bool InvertedIndex::save_binary(const std::string& file_path) const {
    std::ofstream out(file_path, std::ios::binary | std::ios::trunc);
    if (!out) {
        return false;
    }

    // 1. Write Magic Header & Format Version
    out.write(kMagicHeader, 4);
    out.write(reinterpret_cast<const char*>(&kFormatVersion), sizeof(kFormatVersion));

    // 2. Write Summary Counters
    uint64_t active_docs = static_cast<uint64_t>(active_doc_count_);
    out.write(reinterpret_cast<const char*>(&active_docs), sizeof(active_docs));
    out.write(reinterpret_cast<const char*>(&total_body_tokens_), sizeof(total_body_tokens_));

    // 3. Write Document Metadata Table
    uint32_t doc_table_size = static_cast<uint32_t>(documents_.size());
    out.write(reinterpret_cast<const char*>(&doc_table_size), sizeof(doc_table_size));
    for (const DocumentMeta& meta : documents_) {
        out.write(reinterpret_cast<const char*>(&meta.doc_id), sizeof(meta.doc_id));
        write_string(out, meta.path);
        write_string(out, meta.filename);
        write_string(out, meta.extension);
        out.write(reinterpret_cast<const char*>(&meta.body_length), sizeof(meta.body_length));
        out.write(reinterpret_cast<const char*>(&meta.mtime_ns), sizeof(meta.mtime_ns));
        out.write(reinterpret_cast<const char*>(&meta.file_size), sizeof(meta.file_size));
        uint8_t deleted_flag = meta.is_deleted ? 1 : 0;
        out.write(reinterpret_cast<const char*>(&deleted_flag), sizeof(deleted_flag));
    }

    // Helper lambda to serialize a posting map
    auto write_posting_map = [&](const std::unordered_map<std::string, CompressedPostingList>& map) {
        uint32_t map_size = static_cast<uint32_t>(map.size());
        out.write(reinterpret_cast<const char*>(&map_size), sizeof(map_size));
        for (const auto& [term, plist] : map) {
            write_string(out, term);
            out.write(reinterpret_cast<const char*>(&plist.doc_count), sizeof(plist.doc_count));
            out.write(reinterpret_cast<const char*>(&plist.last_doc_id), sizeof(plist.last_doc_id));
            out.write(reinterpret_cast<const char*>(&plist.total_frequency), sizeof(plist.total_frequency));
            uint32_t byte_len = static_cast<uint32_t>(plist.compressed_bytes.size());
            out.write(reinterpret_cast<const char*>(&byte_len), sizeof(byte_len));
            if (byte_len > 0) {
                out.write(reinterpret_cast<const char*>(plist.compressed_bytes.data()), byte_len);
            }
        }
    };

    // 4. Write Exact & Stemmed Compressed Posting Tables
    write_posting_map(exact_postings_);
    write_posting_map(stem_postings_);

    return out.good();
}

bool InvertedIndex::load_binary(const std::string& file_path) {
    std::ifstream in(file_path, std::ios::binary);
    if (!in) {
        return false;
    }

    char magic[4] = {0};
    in.read(magic, 4);
    if (!in || std::memcmp(magic, kMagicHeader, 4) != 0) {
        return false;
    }

    uint32_t version = 0;
    in.read(reinterpret_cast<char*>(&version), sizeof(version));
    if (!in || version != kFormatVersion) {
        return false;
    }

    uint64_t active_docs = 0;
    uint64_t total_tokens = 0;
    in.read(reinterpret_cast<char*>(&active_docs), sizeof(active_docs));
    in.read(reinterpret_cast<char*>(&total_tokens), sizeof(total_tokens));

    uint32_t doc_table_size = 0;
    in.read(reinterpret_cast<char*>(&doc_table_size), sizeof(doc_table_size));
    if (!in) {
        return false;
    }

    std::vector<DocumentMeta> new_docs(doc_table_size);
    std::unordered_map<std::string, uint32_t> new_path_map;
    new_path_map.reserve(doc_table_size);

    for (uint32_t i = 0; i < doc_table_size; ++i) {
        DocumentMeta& meta = new_docs[i];
        in.read(reinterpret_cast<char*>(&meta.doc_id), sizeof(meta.doc_id));
        meta.path = read_string(in);
        meta.filename = read_string(in);
        meta.extension = read_string(in);
        in.read(reinterpret_cast<char*>(&meta.body_length), sizeof(meta.body_length));
        in.read(reinterpret_cast<char*>(&meta.mtime_ns), sizeof(meta.mtime_ns));
        in.read(reinterpret_cast<char*>(&meta.file_size), sizeof(meta.file_size));
        uint8_t deleted_flag = 0;
        in.read(reinterpret_cast<char*>(&deleted_flag), sizeof(deleted_flag));
        meta.is_deleted = (deleted_flag != 0);

        if (!meta.is_deleted) {
            new_path_map[meta.path] = meta.doc_id;
        }
    }

    auto read_posting_map = [&](std::unordered_map<std::string, CompressedPostingList>& map) -> bool {
        uint32_t map_size = 0;
        in.read(reinterpret_cast<char*>(&map_size), sizeof(map_size));
        if (!in) return false;
        map.clear();
        map.reserve(map_size);

        for (uint32_t i = 0; i < map_size; ++i) {
            std::string term = read_string(in);
            CompressedPostingList plist;
            in.read(reinterpret_cast<char*>(&plist.doc_count), sizeof(plist.doc_count));
            in.read(reinterpret_cast<char*>(&plist.last_doc_id), sizeof(plist.last_doc_id));
            in.read(reinterpret_cast<char*>(&plist.total_frequency), sizeof(plist.total_frequency));
            uint32_t byte_len = 0;
            in.read(reinterpret_cast<char*>(&byte_len), sizeof(byte_len));
            if (!in) return false;
            plist.compressed_bytes.resize(byte_len);
            if (byte_len > 0) {
                in.read(reinterpret_cast<char*>(plist.compressed_bytes.data()), byte_len);
            }
            map.emplace(std::move(term), std::move(plist));
        }
        return in.good();
    };

    std::unordered_map<std::string, CompressedPostingList> new_exact;
    std::unordered_map<std::string, CompressedPostingList> new_stem;
    if (!read_posting_map(new_exact) || !read_posting_map(new_stem)) {
        return false;
    }

    documents_ = std::move(new_docs);
    path_to_doc_id_ = std::move(new_path_map);
    exact_postings_ = std::move(new_exact);
    stem_postings_ = std::move(new_stem);
    active_doc_count_ = static_cast<size_t>(active_docs);
    total_body_tokens_ = total_tokens;
    return true;
}

}  // namespace smartlex
