from antrack.app_info import _git_commit_from_files


def test_git_commit_from_files_reads_head_ref(tmp_path):
    git_dir = tmp_path / ".git"
    ref_dir = git_dir / "refs" / "heads"
    ref_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (ref_dir / "main").write_text("abcdef1234567890\n", encoding="utf-8")

    assert _git_commit_from_files(tmp_path) == "abcdef1"
