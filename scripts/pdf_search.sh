# ==================================================
# pdf_search.sh - File Path Collection Script
# Scans system for PDF and DOCX files (Linux/Mac)
# ==================================================

echo "Starting file scan..."
echo "This may take a few minutes..."

# Create output directory
mkdir -p all

# Clear old batch files
rm -f all/pdf_part_*.txt

# Scan root directory (or home) for PDF and DOCX files
# Change "/" to specific directory if scanning entire system is too slow
echo "Scanning / for PDF and DOCX files..."
find / -type f \( -iname "*.pdf" -o -iname "*.docx" \) 2>/dev/null > all/all_files.txt

# Count total files
total=$(wc -l < all/all_files.txt)
echo "Found $total files"

# Calculate files per batch (divide by 8)
batch_size=$(( (total + 7) / 8 ))
if [ "$batch_size" -lt 1 ]; then
    batch_size=1
fi

echo "Splitting into 8 batches (~$batch_size files each)..."

# Split into 8 batches
split -l $batch_size -d --additional-suffix=.txt all/all_files.txt all/pdf_part_

# Rename split files to match pdf_part_1.txt, pdf_part_2.txt, etc.
i=1
for f in all/pdf_part_*.txt; do
    mv "$f" "all/pdf_part_$i.txt"
    file_count=$(wc -l < "all/pdf_part_$i.txt")
    echo "all/pdf_part_$i.txt: $file_count files"
    ((i++))
done

echo
echo "File collection complete!"
echo "Batch files created in 'all' folder"
echo "Ready to start indexing..."
