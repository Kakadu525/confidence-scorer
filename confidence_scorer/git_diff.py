from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

WORKTREE = "WORKTREE"


class GitError(RuntimeError):
    pass


@dataclass
class ChangedFile:
    path: str
    status: str
    old_content: str | None
    new_content: str | None
    language: str | None


def _run_git(args: list[str], repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _show(ref: str, path: str, repo_dir: Path) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_cat_file_batch(stdout: bytes, paths: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    offset = 0
    for path in paths:
        newline = stdout.find(b"\n", offset)
        if newline == -1:
            out[path] = None
            continue
        header = stdout[offset:newline]
        offset = newline + 1

        parts = header.split(b" ")
        if len(parts) < 3 or parts[-1] == b"missing":
            out[path] = None
            continue

        try:
            size = int(parts[2])
        except ValueError:
            out[path] = None
            continue

        blob = stdout[offset : offset + size]
        offset += size + 1
        try:
            out[path] = blob.decode("utf-8")
        except UnicodeDecodeError:
            out[path] = None
    return out


def _show_many(ref: str, paths: list[str], repo_dir: Path) -> dict[str, str | None]:
    if not paths:
        return {}

    request = "".join(f"{ref}:{path}\n" for path in paths).encode("utf-8")
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=str(repo_dir),
            input=request,
            capture_output=True,
            check=False,
        )
    except OSError:
        proc = None

    if proc is None or proc.returncode != 0:
        return {path: _show(ref, path, repo_dir) for path in paths}

    return _parse_cat_file_batch(proc.stdout, paths)


def _read_worktree(repo_dir: Path, path: str) -> str | None:
    full = repo_dir / path
    if not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _language_for(path: str) -> str | None:
    for ext, lang in LANGUAGE_BY_EXT.items():
        if path.endswith(ext):
            return lang
    return None


def _is_excluded(path: str, exclude_globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in exclude_globs)


def merge_base(base_branch: str, repo_dir: Path, head: str = "HEAD") -> str:
    ref = "HEAD" if head == WORKTREE else head
    out = _run_git(["merge-base", ref, base_branch], repo_dir)
    return out.strip()


def get_changed_files(
    base: str,
    head: str,
    repo_dir: str | Path = ".",
    exclude_globs: list[str] | None = None,
    languages: list[str] | None = None,
) -> list[ChangedFile]:
    repo_dir = Path(repo_dir)
    exclude_globs = exclude_globs or []
    languages = languages or list(set(LANGUAGE_BY_EXT.values()))

    diff_head_ref = "HEAD" if head == WORKTREE else head
    name_status = _run_git(["diff", "--name-status", f"{base}...{diff_head_ref}"], repo_dir)
    if head == WORKTREE:
        name_status += _run_git(["diff", "--name-status", "HEAD"], repo_dir)
        name_status += _run_git(["diff", "--name-status", "--cached"], repo_dir)

    seen: dict[str, str] = {}
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, *paths = parts
        status = status[0]
        path = paths[-1]
        seen[path] = status

    interesting: list[tuple[str, str]] = []
    for path, status in sorted(seen.items()):
        language = _language_for(path)
        if language is None or language not in languages:
            continue
        if _is_excluded(path, exclude_globs):
            continue
        interesting.append((path, status))

    old_needed = [path for path, status in interesting if status != "A"]
    old_contents = _show_many(base, old_needed, repo_dir)

    if head == WORKTREE:
        new_contents = {
            path: _read_worktree(repo_dir, path) for path, status in interesting if status != "D"
        }
    else:
        new_needed = [path for path, status in interesting if status != "D"]
        new_contents = _show_many(head, new_needed, repo_dir)

    return [
        ChangedFile(
            path=path,
            status=status,
            old_content=None if status == "A" else old_contents.get(path),
            new_content=None if status == "D" else new_contents.get(path),
            language=_language_for(path),
        )
        for path, status in interesting
    ]


def unified_diff_text(base: str, head: str, repo_dir: str | Path = ".", paths: list[str] | None = None) -> str:
    repo_dir = Path(repo_dir)
    path_args = ["--", *paths] if paths else []

    if head == WORKTREE:
        committed = _run_git(["diff", f"{base}...HEAD", *path_args], repo_dir)
        uncommitted = _run_git(["diff", "HEAD", *path_args], repo_dir)
        return committed + uncommitted

    return _run_git(["diff", f"{base}...{head}", *path_args], repo_dir)
