#!/bin/sh
#
# pre-commit: reject staged files that exceed a size limit.

MAX_SIZE=1000000 # bytes (1 MB)

violations=""

while IFS= read -r file; do
    [ -z "$file" ] && continue
    size=$(git cat-file -s ":$file" 2>/dev/null) || continue
    if [ "$size" -gt "$MAX_SIZE" ]; then
        human=$(awk -v s="$size" 'BEGIN { printf "%.1f MB", s / 1000000 }')
        violations="$violations  $file ($human)
"
    fi
done <<EOF
$(git diff --cached --diff-filter=d --name-only)
EOF

if [ -n "$violations" ]; then
    printf "\nLarge file(s) found:\n\n" >&2
    printf "%s" "$violations" >&2
    printf "\nTo unstage: git reset HEAD <file>\n" >&2
    printf "To commit anyway: git commit --no-verify\n\n" >&2
    exit 1
fi
