#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace smartlex {

/**
 * Metadata for a single file discovered by the C++20 Parallel Scanner.
 */
struct ScannedFile {
    std::string path;       // Full file path
    uint64_t mtime_ns = 0;  // Last modification time in nanoseconds
    uint64_t file_size = 0; // File size in bytes
    int priority_tier = 1;  // 0 = High priority (Desktop/Documents/Downloads), 1 = Normal
};

class FileScanner {
public:
    /**
     * Recursively scans `root_paths` in parallel using C++20 worker threads.
     *
     * Features:
     * - Automatically skips system/virtual directories (Windows, Program Files,
     *   /proc, /sys, /dev, node_modules, .git, venv, build, etc.) and hidden folders.
     * - Collects (path, mtime_ns, file_size) in a single directory pass so Python
     *   doesn't have to call os.stat() on every file again.
     * - Sorts returned files by priority:
     *     1. High-priority user folders (Desktop, Documents, Downloads) first.
     *     2. Most recently modified files (mtime_ns descending) first.
     */
    static std::vector<ScannedFile> scan_directories(
        const std::vector<std::string>& root_paths,
        const std::vector<std::string>& extensions,
        size_t num_threads = 8);

    /**
     * Returns true if a directory name should be excluded from scanning.
     */
    static bool is_excluded_directory(const std::string& dir_name);
};

}  // namespace smartlex

