Edit a file by replacing an exact string with new content.

The old_string must match exactly one occurrence in the file, including all whitespace, indentation, and line breaks. If the string appears multiple times, the tool reports all match locations (line numbers) to help you add more context.

Features:
- Uniqueness check with match location diagnostics (shows line numbers of all matches)
- Fuzzy match hints when exact match fails (whitespace/case mismatch detection)
- No-op detection (old_string == new_string)
- Post-edit verification snippet with surrounding context lines and change markers
- insert_after_line for line-based insertion (1-based)

Guidelines:
- Always read the file first to get the exact content
- Include enough context in old_string to ensure uniqueness
- Preserve the original indentation and formatting
