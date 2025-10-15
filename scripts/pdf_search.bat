@echo off
REM ==================================================
REM pdf_search.bat - File Path Collection Script
REM Scans system for PDF and DOCX files
REM ==================================================

echo Starting file scan...
echo This may take a few minutes...

REM Create output directory
if not exist "all" mkdir all

REM Clear old files
del /Q all\*.txt 2>NUL

REM Scan C: drive for PDF and DOCX files
echo Scanning C: drive...
dir /s /b C:\*.pdf C:\*.docx > all\all_files.txt 2>NUL

REM Count total files
for /f %%a in ('type all\all_files.txt ^| find /c /v ""') do set total=%%a
echo Found %total% files

REM Calculate files per batch (divide by 8)
set /a batch_size=%total%/8
if %batch_size% LSS 1 set batch_size=1

echo Splitting into 8 batches (%batch_size% files each)...

REM Split into 8 parts using PowerShell
powershell -Command "$content = Get-Content 'all\all_files.txt'; $batchSize = [math]::Ceiling($content.Count / 8); for ($i = 0; $i -lt 8; $i++) { $start = $i * $batchSize; $end = [math]::Min($start + $batchSize - 1, $content.Count - 1); if ($start -le $end) { $content[$start..$end] | Out-File -FilePath \"all\pdf_part_$($i+1).txt\" -Encoding UTF8 } }"

echo File collection complete!
echo Batch files created in 'all' folder

REM Display batch file info
for %%f in (all\pdf_part_*.txt) do (
    for /f %%a in ('type "%%f" ^| find /c /v ""') do echo %%f: %%a files
)

echo.
echo Ready to start indexing...
pause