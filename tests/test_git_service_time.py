from datetime import timedelta

from git import Repo

from app.skills.core.git_service import GitService


def _init_repo(path):
    repo = Repo.init(path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@example.com")
    (path / "README.md").write_text("init\n", encoding="utf-8")
    repo.git.add("-A")
    repo.index.commit("init")
    return repo


def test_git_service_commit_all_writes_beijing_git_metadata(tmp_path):
    _init_repo(tmp_path)
    skill_dir = tmp_path / "skill-a"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("draft\n", encoding="utf-8")

    commit_sha = GitService(str(tmp_path)).commit_all(
        "save skill",
        "tester",
        skill_id="skill-a",
        validate=False,
    )

    commit = Repo(tmp_path).commit(commit_sha)
    assert commit.committed_datetime.utcoffset() == timedelta(hours=8)
    assert "Timestamp:" in commit.message
    assert "+08:00" in commit.message


def test_git_service_commit_writes_beijing_git_metadata(tmp_path):
    _init_repo(tmp_path)
    skill_dir = tmp_path / "skill-b"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("draft\n", encoding="utf-8")

    commit_sha = GitService(str(tmp_path)).commit("skill-b", "save skill", "tester")

    commit = Repo(tmp_path).commit(commit_sha)
    assert commit.committed_datetime.utcoffset() == timedelta(hours=8)
    assert "Timestamp:" in commit.message
    assert "+08:00" in commit.message
