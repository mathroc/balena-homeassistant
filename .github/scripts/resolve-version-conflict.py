#!/usr/bin/env python3
"""Resolve a rebase conflict caused only by upstream version bumps.

Upstream's Renovate bumps version references (compose `image:`, Dockerfile
`FROM`, workflow `uses:`), which conflicts with our edits on adjacent lines.
This re-runs the 3-way merge with every such reference reduced to its name.
If that merge is clean, the conflict was only about versions: write the
merged file with versions restored (upstream's, else ours) and `git add` it.
Otherwise exit 1 and leave the conflict alone.

Usage: resolve-version-conflict.py <path>   (during a conflicted rebase)
"""

import os
import re
import subprocess
import sys
import tempfile

# prefix + ref + suffix; ref is what Renovate bumps
PATTERNS = [
    re.compile(r"^(?P<prefix>\s*image:\s*['\"]?)(?P<ref>[^\s'\"]+)(?P<suffix>.*)$"),
    re.compile(r"^(?P<prefix>\s*FROM\s+(?:--platform=\S+\s+)?)(?P<ref>\S+)(?P<suffix>.*)$"),
    re.compile(r"^(?P<prefix>\s*(?:-\s+)?uses:\s*['\"]?)(?P<ref>[^\s'\"]+)(?P<suffix>.*)$"),
]
# Trailing "# v1.2.3" comments next to pinned digests change with the ref
VERSION_COMMENT = re.compile(r"\s+#\s*v?\d[\w.+-]*\s*$")


def ref_name(ref: str) -> str:
    """docker.io/x/y:1.2@sha256:... -> docker.io/x/y ; owner/action@sha -> owner/action"""
    ref = ref.split("@", 1)[0]
    head, sep, tail = ref.rpartition(":")
    return head if sep and "/" not in tail else ref


def match(line: str):
    for pattern in PATTERNS:
        m = pattern.match(line.rstrip("\n"))
        if m:
            return m
    return None


def stage(n: int, path: str) -> list[str]:
    out = subprocess.run(["git", "show", f":{n}:{path}"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"{path}: stage {n} missing, not a content conflict")
    return out.stdout.splitlines(keepends=True)


def normalize(lines: list[str], versions: dict[str, tuple[str, str]]) -> list[str]:
    result = []
    for line in lines:
        m = match(line)
        if m:
            name = ref_name(m["ref"])
            suffix = m["suffix"]
            comment = VERSION_COMMENT.search(suffix)
            version_comment = comment.group(0) if comment else ""
            suffix = suffix[: comment.start()] if comment else suffix
            versions.setdefault(name, (m["ref"], version_comment))
            line = f"{m['prefix']}{name}{suffix}\n"
        result.append(line)
    return result


def main() -> int:
    path = sys.argv[1]
    # During a rebase: 1 = merge base, 2 = HEAD (upstream side), 3 = commit being replayed (ours)
    upstream_versions: dict[str, tuple[str, str]] = {}
    our_versions: dict[str, tuple[str, str]] = {}
    base = normalize(stage(1, path), {})
    upstream = normalize(stage(2, path), upstream_versions)
    ours = normalize(stage(3, path), our_versions)

    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for name, lines in (("ours", ours), ("base", base), ("upstream", upstream)):
            f = os.path.join(tmp, name)
            with open(f, "w") as fh:
                fh.writelines(lines)
            files.append(f)
        merge = subprocess.run(["git", "merge-file", "-p", *files], capture_output=True, text=True)

    if merge.returncode != 0:
        print(f"{path}: conflict is not only about version bumps", file=sys.stderr)
        return 1

    restored = []
    for line in merge.stdout.splitlines(keepends=True):
        m = match(line)
        if m:
            name = m["ref"]
            ref, comment = upstream_versions.get(name) or our_versions.get(name) or (name, "")
            line = f"{m['prefix']}{ref}{m['suffix']}{comment}\n"
        restored.append(line)

    with open(path, "w") as fh:
        fh.writelines(restored)
    subprocess.run(["git", "add", "--", path], check=True)
    print(f"Resolved version-bump conflict in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
