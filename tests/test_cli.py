import subprocess

from click.testing import CliRunner

from confidence_scorer.cli import main


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_run_exits_nonzero_on_regression(tmp_path):
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "t@example.com"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "m.py").write_text(
        "def is_prime(n: int) -> bool:\n"
        "    if n < 2:\n        return False\n"
        "    for i in range(2, n):\n        if n % i == 0:\n            return False\n"
        "    return True\n",
        encoding="utf-8",
    )
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "init"], tmp_path)

    (tmp_path / "m.py").write_text(
        "def is_prime(n: int) -> bool:\n"
        "    if n <= 2:\n        return False\n"
        "    for i in range(2, n):\n        if n % i == 0:\n            return False\n"
        "    return True\n",
        encoding="utf-8",
    )
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-q", "-m", "bug"], tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main, ["run", "--repo", str(tmp_path), "--base", "HEAD~1", "--head", "HEAD", "--format", "json"]
    )
    assert result.exit_code == 1
    assert '"verdict": "hard_fail"' in result.output


def test_init_creates_config(tmp_path):
    target = tmp_path / "confidence.yml"
    runner = CliRunner()

    result = runner.invoke(main, ["init", "--path", str(target)])
    assert result.exit_code == 0
    assert target.exists()

    result2 = runner.invoke(main, ["init", "--path", str(target)])
    assert result2.exit_code != 0

    result3 = runner.invoke(main, ["init", "--path", str(target), "--force"])
    assert result3.exit_code == 0
