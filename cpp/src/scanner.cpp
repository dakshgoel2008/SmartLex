#include "smartlex/scanner.hpp"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <filesystem>
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

uint64_t file_time_to_ns(fs::file_time_type ftime) {
    auto sys_time = std::chrono::time_point_cast<std::chrono::nanoseconds>(
        std::chrono::clock_cast<std::chrono::system_clock>(ftime));
    auto count = sys_time.time_since_epoch().count();
    return (count > 0) ? static_cast<uint64_t>(count) : 0;
}

}  // namespace

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
        "dist"
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
        if (fs::exists(root, ec) && fs::is_directory(root, ec)) {
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

            std::error_code ec;
            fs::directory_iterator it(
                current_dir,
                fs::directory_options::skip_permission_denied,
                ec
            );

            if (!ec) {
                for (const fs::directory_entry& entry : it) {
                    std::error_code entry_ec;
                    if (entry.is_symlink(entry_ec)) {
                        continue;  // Never follow symlinks to avoid infinite loops
                    }

                    std::string name = entry.path().filename().string();
                    if (entry.is_directory(entry_ec)) {
                        if (!is_excluded_directory(name)) {
                            discovered_subdirs.push_back(entry.path().string());
                        }
                    } else if (entry.is_regular_file(entry_ec)) {
                        std::string name_lower = to_lower_ascii_str(name);
                        if (has_supported_extension(name_lower, norm_extensions)) {
                            std::string full_path = entry.path().string();
                            uint64_t fsize = entry.file_size(entry_ec);
                            if (entry_ec) fsize = 0;

                            uint64_t mtime_ns = 0;
                            auto ftime = entry.last_write_time(entry_ec);
                            if (!entry_ec) {
                                mtime_ns = file_time_to_ns(ftime);
                            }

                            int tier = compute_priority_tier(to_lower_ascii_str(full_path));
                            discovered_files.push_back(ScannedFile{
                                std::move(full_path),
                                mtime_ns,
                                fsize,
                                tier
                            });
                        }
                    }
                }
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

