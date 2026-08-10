#!/usr/bin/env bash
# Check that nothing leaks before publishing: working tree and full history.
#
#   tools/check-leaks.sh            # everything
#   tools/check-leaks.sh --staged   # only what is staged (used by the pre-commit hook)
#
# Two layers:
#   1. gitleaks with .gitleaks.toml, for credentials and generic infra patterns.
#      https://github.com/gitleaks/gitleaks
#   2. your own list of names, in .leakwords.local, which is never versioned.
#      Copy .leakwords.local.example to create it.
set -uo pipefail

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"

MODE="${1:-}"
WORDLIST=".leakwords.local"
failed=0

# ---- 1. gitleaks ---------------------------------------------------------- #
if command -v gitleaks >/dev/null; then
  if [ "$MODE" = "--staged" ]; then
    echo "==> gitleaks (staged changes)"
    gitleaks git --staged --redact --no-banner || failed=1
  else
    echo "==> gitleaks (working tree)"
    gitleaks dir . --redact --no-banner || failed=1
    echo "==> gitleaks (history)"
    gitleaks git . --redact --no-banner || failed=1
  fi
else
  echo "==> gitleaks is not installed; skipping the credentials layer"
  echo "    install it with: go install github.com/gitleaks/gitleaks/v8@latest"
fi

# ---- 2. your own list of names -------------------------------------------- #
if [ -f "$WORDLIST" ]; then
  patterns=$(grep -vE '^\s*(#|$)' "$WORDLIST" | paste -sd'|')
  if [ -n "$patterns" ]; then
    echo "==> names from $WORDLIST"
    if [ "$MODE" = "--staged" ]; then
      # Added lines only: removing an internal name is exactly what we want to allow.
      found=$(git diff --cached -U0 | grep -E '^\+' | grep -vE '^\+\+\+' \
              | grep -inE "$patterns" | head -20)
    else
      found=$( { git grep -inE "$patterns" -- . ; git log -p --all | grep -inE "$patterns"; } \
               2>/dev/null | head -20)
    fi
    if [ -n "$found" ]; then
      echo "    These names should not be published:"
      printf '%s\n' "$found" | sed 's/^/      /'
      failed=1
    fi
  fi
else
  echo "==> no $WORDLIST (copy .leakwords.local.example to enable this layer)"
fi

# ---- 3. the real configuration is never versioned ------------------------- #
if git ls-files --error-unmatch tunnels.yaml >/dev/null 2>&1; then
  echo "==> tunnels.yaml is versioned: it belongs in ~/.config/tunnels-manager/ only"
  failed=1
fi

echo
[ "$failed" -eq 0 ] && echo "Clean." || echo "Look at the findings above before publishing."
exit "$failed"
