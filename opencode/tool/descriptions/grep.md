Search file contents using a regex pattern. Uses ripgrep (rg) when available, falls back to grep.

Features:
- Returns matches with file paths, line numbers, and 1 line of surrounding context
- Summary header showing match count and file count
- Results limited to 100 matches with clear truncation message
- Use the include parameter to filter by file type (e.g. '*.py')
- Directory validation before search
