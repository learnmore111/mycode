Read the contents of a file with line numbers.

Output format: "LINENO:content" for each line, with a file summary header showing total lines and file size.

Features:
- line_offset (1-based) and line_count for partial reads of large files
- Automatic truncation at 2000 lines for large files (use line_offset/line_count to view specific ranges)
- Boundary validation — clear errors for out-of-range line numbers
- Binary file detection
