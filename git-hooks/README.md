# Git Hooks

## Installation

Copy the desired script into a repository's `.git/hooks/` directory, renaming
it to match the hook point (i.e. drop the file extension):

```bash
cp git-prepare-commit-msg-hook.sh /path/to/repo/.git/hooks/prepare-commit-msg
chmod +x /path/to/repo/.git/hooks/prepare-commit-msg
```
