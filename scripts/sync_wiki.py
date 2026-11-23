"""
GitHub Wiki Sync Utility (`scripts/sync_wiki.py`).

Syncs `docs/wiki/*.md` cleanly to `https://github.com/AI-Avalon/Disklavier-Auto.wiki.git`.
If the remote GitHub Wiki repository has not yet had its first page created via the
GitHub web UI (`Repository not found`), prints clear actionable guidance and exits cleanly
without crashing or leaving stray temp directories.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DOCS_WIKI_DIR = ROOT / "docs" / "wiki"
DEFAULT_WIKI_REMOTE = "https://github.com/AI-Avalon/Disklavier-Auto.wiki.git"
DEFAULT_WEB_NEW_URL = "https://github.com/AI-Avalon/Disklavier-Auto/wiki/_new"


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
    )


def sync_wiki(wiki_remote: str = DEFAULT_WIKI_REMOTE, dry_run: bool = False) -> int:
    pages = sorted(DOCS_WIKI_DIR.glob("*.md"))
    if not pages:
        print(f"[ERROR] No Markdown files found under {DOCS_WIKI_DIR}")
        return 1

    print(f"[WIKI] Found {len(pages)} local pages in {DOCS_WIKI_DIR}:")
    for p in pages:
        print(f"  - {p.name}")

    probe = _run_git(["ls-remote", wiki_remote])
    if probe.returncode != 0:
        err = (probe.stderr or probe.stdout or "").strip()
        if "not found" in err.lower() or "does not appear to be a git repository" in err.lower():
            print("")
            print("[WIKI_REPO_UNINITIALIZED] Remote Wiki Git repository does not exist on GitHub yet.")
            print("GitHub enables `.wiki.git` right after the first Wiki page is saved once in the browser:")
            print(f"  1. Open {DEFAULT_WEB_NEW_URL} and click 'Save Page'.")
            print("  2. Then re-run: python scripts/sync_wiki.py")
            print(f"Remote detail: {err}")
            return 0
        print(f"[ERROR] Failed probing Wiki remote {wiki_remote}: {err}")
        return 1

    with tempfile.TemporaryDirectory() as tmpd:
        clone_dir = Path(tmpd) / "wiki_repo"
        cl = _run_git(["clone", wiki_remote, str(clone_dir)])
        if cl.returncode != 0:
            print(f"[ERROR] git clone failed: {cl.stderr.strip()}")
            return 1

        for p in pages:
            shutil.copy2(p, clone_dir / p.name)

        status = _run_git(["status", "--porcelain"], cwd=clone_dir)
        if not status.stdout.strip():
            print("[WIKI] Remote GitHub Wiki is already up to date (0 file diffs).")
            return 0

        _run_git(["add", "."], cwd=clone_dir)
        if dry_run:
            print("[WIKI DRY-RUN] Pending diff:")
            print(status.stdout)
            return 0

        cm = _run_git(
            ["commit", "-m", "docs(wiki): sync 7 Disklavier-Auto technical & stage runbook pages"],
            cwd=clone_dir,
        )
        if cm.returncode != 0:
            print(f"[ERROR] git commit failed: {cm.stderr.strip() or cm.stdout.strip()}")
            return 1

        ps = _run_git(["push", "origin", "HEAD"], cwd=clone_dir)
        if ps.returncode != 0:
            print(f"[ERROR] git push failed: {ps.stderr.strip()}")
            return 1

        print("[WIKI_SYNCED] Successfully committed and pushed all Wiki pages to GitHub Wiki.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync docs/wiki/*.md to GitHub Wiki repo")
    parser.add_argument("--remote", default=DEFAULT_WIKI_REMOTE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return sync_wiki(wiki_remote=args.remote, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
