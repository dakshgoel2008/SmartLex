#pragma once

#include <cstdint>
#include <stdexcept>
#include <vector>

namespace smartlex {

/**
 * Variable-Byte (VarInt) & Delta Compression Utilities.
 *
 * Why we use this:
 * A normal 32-bit integer (uint32_t) always takes 4 bytes on disk and in RAM,
 * even if the number is small (like 1, 2, or 15).
 *
 * In an inverted index, document IDs and word positions are stored in sorted order:
 *   Original positions: [100, 102, 107, 108]
 *   Delta-encoded:      [100,   2,   5,   1]  (each number minus the previous number)
 *
 * Because the deltas are tiny numbers (< 128), VarInt encodes each of them in
 * just 1 byte instead of 4 bytes — shrinking our index size by ~75-80%!
 *
 * How VarInt works:
 * - Each byte holds 7 bits of the number (the lower 7 bits: 0x7F).
 * - The 8th bit (0x80) is a "continuation flag":
 *     - 1 means "more bytes follow for this number".
 *     - 0 means "this is the final byte of this number".
 */
class VarIntCodec {
public:
    // Appends a single uint32_t to the byte buffer using 1 to 5 bytes.
    static inline void encode_uint32(uint32_t value, std::vector<uint8_t>& out_buffer) {
        while (value >= 0x80) {
            // Write the lowest 7 bits and set the 8th bit (0x80) to indicate more bytes follow.
            out_buffer.push_back(static_cast<uint8_t>((value & 0x7F) | 0x80));
            value >>= 7;
        }
        // Write the final 7 (or fewer) bits with the 8th bit cleared (0).
        out_buffer.push_back(static_cast<uint8_t>(value & 0x7F));
    }

    // Reads a single uint32_t from the byte buffer starting at `offset`, advancing `offset`.
    static inline uint32_t decode_uint32(const uint8_t* data, size_t size, size_t& offset) {
        uint32_t result = 0;
        uint32_t shift = 0;

        while (offset < size) {
            uint8_t byte = data[offset++];
            result |= static_cast<uint32_t>(byte & 0x7F) << shift;

            // If the highest bit is 0, we have reached the end of this integer.
            if ((byte & 0x80) == 0) {
                return result;
            }
            shift += 7;
            if (shift >= 35) {
                throw std::runtime_error("Corrupted VarInt stream: integer overflow");
            }
        }
        throw std::runtime_error("Corrupted VarInt stream: unexpected end of buffer");
    }

    // Compresses a sorted list of uint32_t values using Delta Encoding + VarInt.
    static inline void encode_delta_list(const std::vector<uint32_t>& sorted_values,
                                         std::vector<uint8_t>& out_buffer) {
        encode_uint32(static_cast<uint32_t>(sorted_values.size()), out_buffer);
        uint32_t previous = 0;
        for (uint32_t current : sorted_values) {
            uint32_t delta = current - previous;
            encode_uint32(delta, out_buffer);
            previous = current;
        }
    }

    // Decompresses a Delta + VarInt encoded list back into the original sorted uint32_t values.
    static inline std::vector<uint32_t> decode_delta_list(const uint8_t* data,
                                                          size_t size,
                                                          size_t& offset) {
        uint32_t count = decode_uint32(data, size, offset);
        std::vector<uint32_t> values;
        values.reserve(count);

        uint32_t previous = 0;
        for (uint32_t i = 0; i < count; ++i) {
            uint32_t delta = decode_uint32(data, size, offset);
            uint32_t current = previous + delta;
            values.push_back(current);
            previous = current;
        }
        return values;
    }
};

}  // namespace smartlex

