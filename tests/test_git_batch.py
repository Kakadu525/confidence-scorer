from __future__ import annotations

import subprocess

from confidence_scorer.git_diff import _show, _show_many, get_changed_files


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path):
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "t@example.com"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    return tmp_path


def test_batch_read_matches_per_file_read(tmp_path):
    repo = _repo(tmp_path)
    files = {
        "a.py": "def a() -> int:\n    return 1\n",
        "b.py": "# комментарий с не-ASCII символами\ndef b() -> int:\n    return 2\n",
        "c.py": "",
    }
    for name, content in files.items():
        (repo / name).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    batched = _show_many("HEAD", list(files), repo)
    one_by_one = {name: _show("HEAD", name, repo) for name in files}

    assert batched == one_by_one


def test_missing_path_yields_none(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    result = _show_many("HEAD", ["a.py", "нет-такого.py"], repo)

    assert result["a.py"] == "x = 1\n"
    assert result["нет-такого.py"] is None


def test_empty_request_is_noop(tmp_path):
    assert _show_many("HEAD", [], _repo(tmp_path)) == {}


def test_added_and_deleted_files_have_the_right_sides(tmp_path):
    repo = _repo(tmp_path)
    (repo / "keep.py").write_text("def keep() -> int:\n    return 1\n", encoding="utf-8")
    (repo / "gone.py").write_text("def gone() -> int:\n    return 2\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    (repo / "gone.py").unlink()
    (repo / "added.py").write_text("def added() -> int:\n    return 3\n", encoding="utf-8")
    (repo / "keep.py").write_text("def keep() -> int:\n    return 11\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "change"], repo)

    by_path = {cf.path: cf for cf in get_changed_files("HEAD~1", "HEAD", repo_dir=repo)}

    assert by_path["added.py"].old_content is None
    assert by_path["added.py"].new_content is not None
    assert by_path["gone.py"].old_content is not None
    assert by_path["gone.py"].new_content is None
    assert by_path["keep.py"].old_content != by_path["keep.py"].new_content
