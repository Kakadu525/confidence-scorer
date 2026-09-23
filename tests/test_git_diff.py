import subprocess

import pytest

from confidence_scorer.git_diff import WORKTREE, get_changed_files, merge_base, unified_diff_text


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _git_out(args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "t@example.com"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)
    return tmp_path


def test_get_changed_files_between_commits(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "change"], repo)

    files = get_changed_files("HEAD~1", "HEAD", repo_dir=repo)
    assert [f.path for f in files] == ["a.py"]
    assert files[0].status == "M"
    assert "return 1" in files[0].old_content
    assert "return 2" in files[0].new_content


def test_excludes_and_language_filters(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "b.txt").write_text("not code\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "change"], repo)

    files = get_changed_files("HEAD~1", "HEAD", repo_dir=repo, exclude_globs=["a.py"])
    assert files == []


def test_added_file_has_no_old_content(repo):
    (repo / "new_file.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "add"], repo)

    files = get_changed_files("HEAD~1", "HEAD", repo_dir=repo)
    assert files[0].status == "A"
    assert files[0].old_content is None


def test_merge_base_uses_explicit_head_not_current_checkout(repo):
    default_branch = _git_out(["symbolic-ref", "--short", "HEAD"], repo)

    _git(["checkout", "-b", "feature"], repo)
    (repo / "feature.py").write_text("def h():\n    return 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "feature commit"], repo)
    feature_sha = _git_out(["rev-parse", "HEAD"], repo)

    _git(["checkout", default_branch], repo)
    fork_point = _git_out(["rev-parse", "HEAD"], repo)
    (repo / "main_only.py").write_text("def i():\n    return 2\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "main advances"], repo)

    result = merge_base(default_branch, repo, head=feature_sha)
    assert result == fork_point

    default_result = merge_base(default_branch, repo)
    assert default_result != fork_point


def test_unified_diff_text_worktree_excludes_base_only_commits(repo):
    default_branch = _git_out(["symbolic-ref", "--short", "HEAD"], repo)

    _git(["checkout", "-b", "feature"], repo)
    (repo / "a.py").write_text("def f():\n    return 999\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "feature change"], repo)

    _git(["checkout", default_branch], repo)
    (repo / "unrelated.py").write_text("def unrelated():\n    return 0\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "unrelated main change"], repo)

    _git(["checkout", "feature"], repo)

    diff_text = unified_diff_text(default_branch, WORKTREE, repo_dir=repo)
    assert "return 999" in diff_text
    assert "unrelated" not in diff_text
