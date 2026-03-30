Execute a shell command. Use this to run commands, install packages, or interact with the system.

Commands are executed in the project root directory with a POSIX shell (bash/zsh/sh).
The environment variable AGENT=1 is set to indicate the command is run by an agent.

Guidelines:
- Prefer non-interactive commands (pass -y, --yes, --non-interactive flags)
- For long-running processes, set an appropriate timeout
- Output exceeding 100K characters will be automatically truncated
- Avoid commands that require user input or open a pager
