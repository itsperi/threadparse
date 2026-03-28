#!/bin/bash

# Check if input file is provided
if [ -z "$1" ]; then
  echo "Usage: $0 <file_with_github_urls>"
  exit 1
fi

INPUT_FILE="$1"
TARGET_DIR="files"

# Create target directory if it doesn't exist
mkdir -p "$TARGET_DIR"

# Read file line by line
while IFS= read -r repo_url || [ -n "$repo_url" ]; do
  # Skip empty lines or comments
  if [[ -z "$repo_url" || "$repo_url" =~ ^# ]]; then
    continue
  fi

  echo "Cloning $repo_url ..."
  git clone "$repo_url" "$TARGET_DIR/$(basename -s .git "$repo_url")"

done < "$INPUT_FILE"

echo "All repositories cloned into '$TARGET_DIR/'"