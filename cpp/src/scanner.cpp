#include "smartlex/scanner.hpp"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <limits>
#include <mutex>
#include <queue>
#include <thread>
#include <unordered_set>

namespace fs = std::filesystem;

namespace smartlex {

namespace {

std::string to_lower_ascii_str(const std::string& s) {
    std::string out = s;
    for (char& c : out) {
        if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c + ('a' - 'A'));
        }
    }
    return out;
}

bool has_supported_extension(const std::string& filename_lower,
                             const std::vector<std::string>& norm_extensions) {
    for (const std::string& ext : norm_extensions) {
        if (filename_lower.size() >= ext.size() &&
            filename_lower.compare(filename_lower.size() - ext.size(), ext.size(), ext) == 0) {
            return true;
        }
    }
    return false;
}

int compute_priority_tier(const std::string& path_lower) {
    if (path_lower.find("/desktop/") != std::string::npos ||
        path_lower.find("/documents/") != std::string::npos ||
        path_lower.find("/downloads/") != std::string::npos ||
        path_lower.find("\\desktop\\") != std::string::npos ||
        path_lower.find("\\documents\\") != std::string::npos ||
        path_lower.find("\\downloads\\") != std::string::npos) {
        return 0;  // High-priority user workspace folders
    }
    return 1;
}

// path::string() throws on Windows for names that do not fit the ANSI code page,
// so always convert through UTF-8.
std::string path_to_utf8(const fs::path& path) {
    std::u8string utf8 = path.u8string();
    return std::string(utf8.begin(), utf8.end());
}

// clock_cast is missing from some standard libraries (older macOS libc++), so convert via
// the offset between the two clocks. Microseconds keep extreme file times from overflowing.
uint64_t file_time_to_ns(fs::file_time_type ftime) {
    using std::chrono::microseconds;
    auto offset = std::chrono::duration_cast<microseconds>(ftime - fs::file_time_type::clock::now());
    auto now = std::chrono::duration_cast<microseconds>(
        std::chrono::system_clock::now().time_since_epoch());
    long long us = (now + offset).count();
    if (us <= 0) {
        return 0;
    }
    constexpr long long kMaxMicroseconds = std::numeric_limits<long long>::max() / 1000;
    return static_cast<uint64_t>(std::min(us, kMaxMicroseconds)) * 1000;
}

fs::path utf8_to_path(const std::string& utf8) {
    return fs::path(std::u8string(utf8.begin(), utf8.end()));
}

bool is_internal_runtime_file(const std::string& name_lower) {
    if (name_lower == "autocomplete_words.json" ||
        name_lower == "output.json" ||
        name_lower == "search_engine.log" ||
        name_lower == "cli_batch.txt") {
        return true;
    }
    return name_lower.rfind("pdf_part_", 0) == 0 &&
           name_lower.size() > 4 &&
           name_lower.substr(name_lower.size() - 4) == ".txt";
}

}  // namespace

// Lists one directory: matching files go to `files`, subdirectories worth
// descending into go to `subdirs`.
void FileScanner::list_directory(const std::string& dir,
                                 const std::vector<std::string>& norm_extensions,
                                 std::vector<std::string>& subdirs,
                                 std::vector<ScannedFile>& files) {
    std::error_code ec;
    fs::directory_iterator it(utf8_to_path(dir), fs::directory_options::skip_permission_denied, ec);
    if (ec) {
        return;
    }

    for (const fs::directory_entry& entry : it) {
        std::error_code entry_ec;
        if (entry.is_symlink(entry_ec)) {
            continue;  // Never follow symlinks to avoid infinite loops
        }

        std::string name = path_to_utf8(entry.path().filename());
        if (entry.is_directory(entry_ec)) {
            if (!is_excluded_directory(name)) {
                subdirs.push_back(path_to_utf8(entry.path()));
            }
            continue;
        }

        if (!entry.is_regular_file(entry_ec)) {
            continue;
        }
        if (name.empty() || name[0] == '.' || name.rfind("~$", 0) == 0) {
            continue;
        }
        std::string name_lower = to_lower_ascii_str(name);
        if (is_internal_runtime_file(name_lower) ||
            !has_supported_extension(name_lower, norm_extensions)) {
            continue;
        }

        std::string full_path = path_to_utf8(entry.path());
        uint64_t file_size = entry.file_size(entry_ec);
        if (entry_ec) {
            file_size = 0;
        }
        uint64_t mtime_ns = 0;
        auto ftime = entry.last_write_time(entry_ec);
        if (!entry_ec) {
            mtime_ns = file_time_to_ns(ftime);
        }

        int tier = compute_priority_tier(to_lower_ascii_str(full_path));
        files.push_back(ScannedFile{std::move(full_path), mtime_ns, file_size, tier});
    }
}

bool FileScanner::is_excluded_directory(const std::string& dir_name) {
    if (dir_name.empty()) {
        return true;
    }
    // Skip hidden directories (starting with '.') such as .git, .cache, .venv, .local
    if (dir_name[0] == '.') {
        return true;
    }

    static const std::unordered_set<std::string> kExcludedDirs = {
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "appdata",
        "system volume information",
        "$recycle.bin",
        "recovery",
        "proc",
        "sys",
        "dev",
        "run",
        "snap",
        "lost+found",
        "timeshift",
        "library",
        "node_modules",
        "venv",
        "env",
        "__pycache__",
        "site-packages",
        "dist-packages",
        "build",
        "dist",
        "logs",
        "backups"
    };

    std::string lower = to_lower_ascii_str(dir_name);
    return kExcludedDirs.find(lower) != kExcludedDirs.end();
}

std::vector<ScannedFile> FileScanner::scan_directories(
    const std::vector<std::string>& root_paths,
    const std::vector<std::string>& extensions,
    size_t num_threads) {

    std::vector<std::string> norm_extensions;
    norm_extensions.reserve(extensions.size());
    for (const std::string& ext : extensions) {
        if (!ext.empty()) {
            std::string lower_ext = to_lower_ascii_str(ext);
            if (lower_ext[0] != '.') {
                lower_ext = "." + lower_ext;
            }
            norm_extensions.push_back(lower_ext);
        }
    }

    std::queue<std::string> dir_queue;
    for (const std::string& root : root_paths) {
        std::error_code ec;
        if (fs::is_directory(utf8_to_path(root), ec)) {
            dir_queue.push(root);
        }
    }

    if (dir_queue.empty() || norm_extensions.empty()) {
        return {};
    }

    std::mutex mutex;
    std::condition_variable cv;
    size_t active_workers = 0;
    std::vector<ScannedFile> all_files;

    const size_t worker_count = std::max<size_t>(1, std::min<size_t>(num_threads, 32));
    std::vector<std::thread> workers;
    workers.reserve(worker_count);

    auto worker_fn = [&]() {
        while (true) {
            std::string current_dir;
            {
                std::unique_lock<std::mutex> lock(mutex);
                cv.wait(lock, [&]() {
                    return !dir_queue.empty() || active_workers == 0;
                });

                if (dir_queue.empty() && active_workers == 0) {
                    return;  // All directories have been scanned
                }

                current_dir = std::move(dir_queue.front());
                dir_queue.pop();
                active_workers += 1;
            }

            std::vector<std::string> discovered_subdirs;
            std::vector<ScannedFile> discovered_files;

            // Iterating a directory can throw (e.g. it is deleted mid-scan). An exception
            // escaping a worker thread would terminate the process, so skip that directory.
            try {
                list_directory(current_dir, norm_extensions, discovered_subdirs, discovered_files);
            } catch (const std::exception&) {
                discovered_subdirs.clear();
                discovered_files.clear();
            }

            {
                std::unique_lock<std::mutex> lock(mutex);
                for (std::string& subdir : discovered_subdirs) {
                    dir_queue.push(std::move(subdir));
                }
                if (!discovered_files.empty()) {
                    all_files.insert(
                        all_files.end(),
                        std::make_move_iterator(discovered_files.begin()),
                        std::make_move_iterator(discovered_files.end())
                    );
                }
                active_workers -= 1;
            }
            cv.notify_all();
        }
    };

    for (size_t i = 0; i < worker_count; ++i) {
        workers.emplace_back(worker_fn);
    }

    for (std::thread& t : workers) {
        if (t.joinable()) {
            t.join();
        }
    }

    // Sort by priority_tier ascending (0 before 1), then mtime_ns descending (newest first)
    std::sort(all_files.begin(), all_files.end(), [](const ScannedFile& a, const ScannedFile& b) {
        if (a.priority_tier != b.priority_tier) {
            return a.priority_tier < b.priority_tier;
        }
        if (a.mtime_ns != b.mtime_ns) {
            return a.mtime_ns > b.mtime_ns;
        }
        return a.path < b.path;
    });

    return all_files;
}

}  // namespace smartlex

