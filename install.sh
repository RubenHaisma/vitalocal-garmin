#!/bin/bash
# GarminGPT — 0→100 installer for macOS & Linux.
#
# From a totally fresh machine, open Terminal and paste ONE line:
#   curl -fsSL https://raw.githubusercontent.com/RubenHaisma/garmingpt/main/install.sh | bash
#
# It downloads the code, installs uv+Python, installs Ollama, pulls a small AI
# model, and launches the dashboard. Nothing else needed.

set -u
REPO="https://github.com/RubenHaisma/garmingpt"
DIR="${GARMINGPT_DIR:-$HOME/garmingpt}"
B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; X=$'\033[0m'
printf "${B}GarminGPT installer${X}\n${D}This sets up everything on your own computer. Nothing is uploaded.${X}\n"

# 1) get the code (unless we're already inside it)
if [ -f "./garmingpt/cli.py" ] && [ -f "./start.command" ]; then
  DIR="$(pwd)"
elif [ -d "$DIR/.git" ]; then
  printf "${D}Updating existing copy in %s…${X}\n" "$DIR"
  git -C "$DIR" pull --ff-only >/dev/null 2>&1 || true
else
  printf "${D}Downloading the app to %s…${X}\n" "$DIR"
  if command -v git >/dev/null 2>&1; then
    git clone --depth 1 "$REPO" "$DIR" || { echo "Download failed. Check your internet and try again."; exit 1; }
  else
    mkdir -p "$DIR"
    curl -L --fail "$REPO/archive/refs/heads/main.tar.gz" -o /tmp/garmingpt.tgz || { echo "Download failed."; exit 1; }
    tar -xzf /tmp/garmingpt.tgz -C "$DIR" --strip-components=1 || { echo "Unpack failed."; exit 1; }
  fi
fi

cd "$DIR" || { echo "Could not enter $DIR"; exit 1; }
chmod +x start.command 2>/dev/null

# 2) hand off to the full setup+launch script (installs uv/Python, Ollama, model, then serves)
printf "${G}Code ready. Running setup…${X}\n\n"
exec bash ./start.command
