#!/bin/bash
#
# Project number from branch name prefixed to commit message, eg. 'PROJECT-1234'
#
# If the script exits with a non-zero error code the commit is aborted.
# Git calls script with from one to three arguments. The first is always present
# and is the path to the temporary file containing the commit message.
#
# See:
#     https://git-scm.com/docs/githooks

set -euo pipefail

commit_msg_file="$1"

# Extract prefix from branch name
shopt -s nocasematch
branch=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0
if [[ "$branch" =~ (PROJECT-[0-9]+) ]]; then
    prefix="${BASH_REMATCH[1]}"
else
    exit 0
fi
shopt -u nocasematch

# Don't write prefix twice
message=$(cat "$commit_msg_file")
if [[ "$message" == "$prefix"* ]]; then
    exit 0
fi

# Prepend prefix to commit message
printf '%s %s' "$prefix" "$message" > "$commit_msg_file"
