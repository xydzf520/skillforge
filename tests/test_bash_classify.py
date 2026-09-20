"""Bash 命令分级测试。

覆盖：
1. safe 命令识别
2. moderate 命令识别
3. dangerous 命令识别（单词+多词）
4. 空命令 / 未知命令默认 moderate
5. 环境变量前缀跳过
6. balanced 策略下 Bash 默认放行但越界拒绝
"""

import pytest

from app.coding_agent.permission_policy import (
    BASH_DANGEROUS_COMMANDS,
    BASH_MODERATE_COMMANDS,
    BASH_SAFE_COMMANDS,
    PermissionPolicy,
    _classify_bash_command,
)


class TestClassifyBashCommand:
    """_classify_bash_command 命令分级测试。"""

    # ── safe 命令 ──

    def test_safe_ls(self):
        assert _classify_bash_command("ls -la") == "safe"

    def test_safe_cat(self):
        assert _classify_bash_command("cat /tmp/foo.txt") == "safe"

    def test_safe_grep(self):
        assert _classify_bash_command("grep -r 'pattern' .") == "safe"

    def test_safe_head(self):
        assert _classify_bash_command("head -20 file.md") == "safe"

    def test_safe_wc(self):
        assert _classify_bash_command("wc -l file.py") == "safe"

    def test_safe_echo(self):
        assert _classify_bash_command("echo hello world") == "safe"

    def test_safe_pwd(self):
        assert _classify_bash_command("pwd") == "safe"

    def test_safe_find(self):
        assert _classify_bash_command("find . -name '*.py'") == "safe"

    def test_safe_which(self):
        assert _classify_bash_command("which python") == "safe"

    # ── moderate 命令 ──

    def test_moderate_cp(self):
        assert _classify_bash_command("cp file1 file2") == "moderate"

    def test_moderate_mv(self):
        assert _classify_bash_command("mv old.py new.py") == "moderate"

    def test_moderate_mkdir(self):
        assert _classify_bash_command("mkdir -p /tmp/dir") == "moderate"

    def test_moderate_curl(self):
        assert _classify_bash_command("curl https://example.com") == "moderate"

    def test_moderate_python(self):
        assert _classify_bash_command("python script.py") == "moderate"

    def test_moderate_pip(self):
        assert _classify_bash_command("pip install requests") == "moderate"

    def test_moderate_npm(self):
        assert _classify_bash_command("npm install") == "moderate"

    def test_moderate_sed(self):
        assert _classify_bash_command("sed -i 's/old/new/g' file.txt") == "moderate"

    # ── dangerous 命令（单词） ──

    def test_dangerous_rm(self):
        assert _classify_bash_command("rm -rf /tmp/dir") == "dangerous"

    def test_dangerous_kill(self):
        assert _classify_bash_command("kill -9 1234") == "dangerous"

    def test_dangerous_sudo(self):
        assert _classify_bash_command("sudo apt install") == "dangerous"

    def test_dangerous_docker(self):
        assert _classify_bash_command("docker rm container") == "dangerous"

    def test_dangerous_systemctl(self):
        assert _classify_bash_command("systemctl restart nginx") == "dangerous"

    def test_dangerous_dd(self):
        assert _classify_bash_command("dd if=/dev/zero of=/tmp/out bs=1M") == "dangerous"

    # ── dangerous 命令（多词前缀匹配） ──

    def test_dangerous_git_push(self):
        assert _classify_bash_command("git push origin main") == "dangerous"

    def test_dangerous_git_push_force(self):
        assert _classify_bash_command("git push --force") == "dangerous"

    def test_dangerous_git_reset(self):
        assert _classify_bash_command("git reset --hard HEAD~1") == "dangerous"

    def test_dangerous_external_publish_prefix(self):
        assert _classify_bash_command("npm publish") == "dangerous"

    # ── 边界情况 ──

    def test_empty_command(self):
        assert _classify_bash_command("") == "moderate"

    def test_whitespace_only(self):
        assert _classify_bash_command("   ") == "moderate"

    def test_unknown_command_defaults_moderate(self):
        assert _classify_bash_command("my_custom_tool --arg") == "moderate"

    def test_git_without_push_not_dangerous(self):
        """普通 git 命令（非 push/reset）不应被判为 dangerous。"""
        assert _classify_bash_command("git status") == "moderate"
        assert _classify_bash_command("git log --oneline") == "moderate"
        assert _classify_bash_command("git diff") == "moderate"

    def test_env_prefix_skipped(self):
        """带环境变量前缀的命令正确提取实际命令。"""
        assert _classify_bash_command("FOO=bar ls -la") == "safe"
        assert _classify_bash_command("PYTHONPATH=/tmp python script.py") == "moderate"
        assert _classify_bash_command("FORCE=1 rm -rf /tmp") == "dangerous"


class TestPermissionPolicyBashClassification:
    """balanced 策略下 Bash 默认放行，路径越界拒绝。"""

    def _policy(self, strategy="balanced"):
        return PermissionPolicy(skill_dir="/tmp/skill-test", strategy=strategy)

    def test_balanced_safe_bash_notify_allow(self):
        """balanced + safe bash 命令 → notify_allow。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "ls -la"})
        assert result.decision == "notify_allow"

    def test_balanced_moderate_bash_notify_allow(self):
        """balanced + moderate bash 命令 → notify_allow。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "python script.py"})
        assert result.decision == "notify_allow"

    def test_balanced_dangerous_bash_ask_user(self):
        """balanced + dangerous bash 命令在允许边界内 → ask_user。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "rm -rf ./tmp/dir"})
        assert result.decision == "ask_user"

    def test_balanced_git_push_ask_user(self):
        """balanced + git push → ask_user。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "git push origin main"})
        assert result.decision == "ask_user"

    def test_balanced_curl_post_external_side_effect_asks_user(self):
        """真实外部写入类请求不能默认放行。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "curl -X POST https://example.com/api -d '{}'"})
        assert result.decision == "ask_user"

    def test_balanced_curl_get_notify_allow(self):
        """只读 GET 探查允许默认放行并通知。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "curl https://example.com/docs"})
        assert result.decision == "notify_allow"

    def test_strict_all_bash_ask_user(self):
        """strict 策略下所有 bash 命令都 ask_user。"""
        policy = self._policy("strict")
        # safe 命令在 strict 下也应 ask_user
        result = policy.evaluate("Bash", {"command": "ls -la"})
        assert result.decision == "ask_user"

    def test_loose_all_bash_auto_allow(self):
        """loose 策略下 bash 命令默认 auto_allow。"""
        policy = self._policy("loose")
        result = policy.evaluate("Bash", {"command": "ls -la"})
        assert result.decision == "auto_allow"

    def test_balanced_empty_bash_notify_allow(self):
        """balanced + 空命令 → notify_allow（默认 moderate）。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": ""})
        assert result.decision == "notify_allow"

    def test_balanced_safe_bash_with_absolute_path_denied(self):
        """balanced + safe 命令访问允许根目录外的绝对路径 → deny。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "cat /etc/passwd"})
        assert result.decision == "deny"

    def test_balanced_safe_bash_with_parent_traversal_denied(self):
        """balanced + safe 命令含 .. 路径跳转到允许根目录外 → deny。"""
        policy = self._policy("balanced")
        result = policy.evaluate("Bash", {"command": "find ../ -name id_rsa"})
        assert result.decision == "deny"

    def test_balanced_bash_absolute_path_inside_extra_root_notify_allow(self, tmp_path):
        """session 工具结果目录属于同一 skill，会被允许。"""
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        config_dir = tmp_path / "coding-agent" / "admin" / "skill_x"
        tool_result = config_dir / "projects" / "p1" / "tool-results" / "call.json"
        tool_result.parent.mkdir(parents=True)
        tool_result.write_text("{}")
        policy = PermissionPolicy(skill_dir=skill_dir, strategy="balanced", extra_allowed_roots=[config_dir])

        result = policy.evaluate("Bash", {"command": f"cat {tool_result} | head -20"})

        assert result.decision == "notify_allow"
