#!/usr/bin/env bash
# Rebase this fork's commits onto upstream main.
#
# Conflicts resolved automatically:
# - upstream modified a file one of our commits deletes (e.g.
#   tailscale/Dockerfile.template, bumped by upstream's Renovate): keep the
#   deletion
# - upstream bumped `image:` versions next to lines we changed: keep our
#   changes with upstream's versions (see resolve-version-conflict.py)
# Any other conflict aborts the rebase and exits non-zero, leaving the branch
# untouched.
#
# Usage: sync-upstream.sh [upstream-url] [upstream-branch]
# Prints "changed=true|false" for GitHub Actions outputs.
set -euo pipefail

# The rebase checks out upstream's tree, where these scripts don't exist yet
tools=$(mktemp -d)
trap 'rm -rf "$tools"' EXIT
cp "$(dirname "$0")/resolve-version-conflict.py" "$tools/"
upstream_url=${1:-https://github.com/klutchell/balena-homeassistant}
upstream_branch=${2:-main}

git fetch --quiet --no-tags "$upstream_url" "+refs/heads/$upstream_branch:refs/remotes/upstream/$upstream_branch"
upstream=refs/remotes/upstream/$upstream_branch

if git merge-base --is-ancestor "$upstream" HEAD; then
  echo "Already up to date with $upstream_url $upstream_branch"
  echo "changed=false"
  exit 0
fi

rebasing() {
  [ -d "$(git rev-parse --git-path rebase-merge)" ] || [ -d "$(git rev-parse --git-path rebase-apply)" ]
}

git rebase "$upstream" >/dev/null 2>&1 || true

for _ in $(seq 100); do
  rebasing || break

  unmerged=$(git status --porcelain | grep -E '^(DD|AU|UD|UA|DU|AA|UU) ' || true)

  if [ -z "$unmerged" ]; then
    # Nothing left to resolve; continue, or skip a commit that became empty
    GIT_EDITOR=true git rebase --continue >/dev/null 2>&1 \
      || { git diff --cached --quiet && git rebase --skip >/dev/null 2>&1; } \
      || true
    continue
  fi

  # UU = both modified: resolvable if it's only upstream image version bumps
  other=""
  while IFS= read -r line; do
    case "$line" in
      "UU "*) python3 -I "$tools/resolve-version-conflict.py" "${line:3}" || other+="$line"$'\n' ;;
      "UD "*) ;;
      *) other+="$line"$'\n' ;;
    esac
  done <<< "$unmerged"

  # UD = deleted by the commit being replayed (ours), modified upstream
  if [ -n "$other" ]; then
    echo "Conflict while replaying $(git rev-parse --short REBASE_HEAD 2>/dev/null || echo '?'): $(git log -1 --format=%s REBASE_HEAD 2>/dev/null || true)" >&2
    printf '%s\n' "$other" >&2
    git rebase --abort
    exit 1
  fi

  printf '%s\n' "$unmerged" | grep '^UD ' | cut -c4- | while IFS= read -r f; do
    echo "Keeping our deletion of $f"
    git rm --quiet -- "$f"
  done
done

if rebasing; then
  echo "Rebase did not converge" >&2
  git rebase --abort
  exit 1
fi

echo "Rebased onto $(git rev-parse --short "$upstream"):"
git log --oneline "$upstream"..HEAD
echo "changed=true"
