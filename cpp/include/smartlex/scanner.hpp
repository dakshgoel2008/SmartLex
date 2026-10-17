#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace smartlex {

// A file found by the scanner.
struct ScannedFile {
    std::string path;       // Full file path
    uint64_t mtime_ns = 0;  // Last modification time in nanoseconds
    uint64_t file_size = 0; // File size in bytes
    int priority_tier = 1;  // 0 = High priority (Desktop/Documents/Downloads), 1 = Normal
};

class FileScanner {
public:
    // Scans `root_paths` in parallel, skipping hidden, system and build folders.
    // Results are sorted with Desktop/Documents/Downloads first, then newest first.
    static std::vector<ScannedFile> scan_directories(
        const std::vector<std::string>& root_paths,
        const std::vector<std::string>& extensions,
        size_t num_threads = 8);

    // True if a directory should not be scanned.
    static bool is_excluded_directory(const std::string& dir_name);

private:
    static void list_directory(const std::string& dir,
                               const std::vector<std::string>& norm_extensions,
                               std::vector<std::string>& subdirs,
                               std::vector<ScannedFile>& files);
};

}  // namespace smartlex

