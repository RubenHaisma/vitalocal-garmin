#!/bin/bash
# GarminGPT — one-click setup + launch for macOS.
# Just double-click this file. The first run installs everything (a few minutes);
# after that it opens instantly. Keep the window open while you use the app.

cd "$(dirname "$0")" || exit 1

B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; X=$'\033[0m'
step(){ printf "\n${B}▸ %s${X}\n" "$*"; }
ok(){ printf "${G}  ✓ %s${X}\n" "$*"; }
die(){ printf "\n${R}✗ %s${X}\n\n${Y}Stuck? Open the Claude or ChatGPT app, paste this whole folder's\nREADME, and say \"help me get this running on my Mac\". It will walk you through it.${X}\n\n"; printf "Press any key to close…"; read -r -n1; exit 1; }

clear
printf "${B}GarminGPT · your private health ML dashboard${X}\n"
printf "${D}First run sets everything up. Nothing leaves this computer.${X}\n"

# choose a model that fits this machine's memory
RAM=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 8000000000) / 1073741824 ))
MODEL="${GARMINGPT_MODEL:-qwen2.5:3b}"
if [ "$RAM" -lt 6 ]; then MODEL="llama3.2:1b"; fi
printf "${D}~%sGB RAM detected → AI model: %s${X}\n" "$RAM" "$MODEL"

# 1) uv (installs Python + dependencies, no system Python needed)
step "Python tooling"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  printf "Installing uv…\n"
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "Could not install uv."
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv is still not found on PATH."
ok "uv ready"

step "App dependencies"
uv sync --quiet || die "Dependency install failed."
ok "dependencies ready"

# 2) Ollama (the local AI engine)
step "Local AI engine (Ollama)"
OLLAMA=""
command -v ollama >/dev/null 2>&1 && OLLAMA="$(command -v ollama)"
[ -z "$OLLAMA" ] && [ -x "/Applications/Ollama.app/Contents/Resources/ollama" ] && OLLAMA="/Applications/Ollama.app/Contents/Resources/ollama"
if [ -z "$OLLAMA" ]; then
  printf "Downloading Ollama…\n"
  curl -L --fail https://ollama.com/download/Ollama-darwin.zip -o /tmp/gg_ollama.zip || die "Could not download Ollama."
  unzip -oq /tmp/gg_ollama.zip -d /Applications || die "Could not unpack Ollama."
  xattr -dr com.apple.quarantine /Applications/Ollama.app 2>/dev/null
  OLLAMA="/Applications/Ollama.app/Contents/Resources/ollama"
fi
[ -x "$OLLAMA" ] || die "Ollama not found after install."
ok "Ollama installed"

# start the Ollama server if it isn't already up
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  printf "Starting Ollama…\n"
  "$OLLAMA" serve >/tmp/gg_ollama.log 2>&1 &
  for _ in $(seq 1 30); do curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
fi
curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || die "Ollama did not start."
ok "Ollama running"

# 3) the model
step "AI model ($MODEL)"
if curl -s http://127.0.0.1:11434/api/tags | grep -q "\"$MODEL\""; then
  ok "$MODEL already downloaded"
else
  printf "Downloading %s — the one big download, a few minutes…\n" "$MODEL"
  "$OLLAMA" pull "$MODEL" || die "Could not download the AI model."
  ok "$MODEL ready"
fi

# 4) launch
step "Starting your dashboard"
export GARMINGPT_MODEL="$MODEL"
( sleep 3; open "http://127.0.0.1:8800" >/dev/null 2>&1 ) &
printf "${G}Opening http://127.0.0.1:8800 in your browser.${X}\n"
printf "${D}Sign in to Garmin on that page. Keep THIS window open while using the app — close it to stop.${X}\n\n"
uv run python -m garmingpt serve
