from types import SimpleNamespace


def test_docker_git_version_uses_configured_skill_branch(monkeypatch):
    from app.config import settings
    from app.skills import docker_git_version as mod

    calls: list[list[str]] = []

    def fake_run(args, timeout=4.0):
        calls.append(args)
        if "log" in args:
            return SimpleNamespace(
                returncode=0,
                stdout="abc123\tabc123\t1777623397\tSkillForge\tupdate skill\n",
                stderr="",
            )
        if "show" in args:
            return SimpleNamespace(
                returncode=0,
                stdout='{"packaged_at":"2026-05-01T15:46:14+08:00"}',
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(settings, "SKILL_REPO_BRANCH", "master")
    monkeypatch.delenv("SKILLFORGE_GITEA_BRANCH", raising=False)
    monkeypatch.setattr(mod, "_run_docker_git", fake_run)
    mod._CACHE.clear()

    result = mod.get_docker_git_version("tmall-link-decline-analysis-v2")

    assert result["branch"] == "master"
    assert result["committed_at"] == "2026-05-01T16:16:37+08:00"
    assert result["package_manifest"]["packaged_at"] == "2026-05-01T15:46:14+08:00"
    assert "master" in calls[0]
    assert any(str(item).startswith("master:") for item in calls[1])
