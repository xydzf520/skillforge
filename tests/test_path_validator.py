"""path_validator 路径穿越防护单元测试。

覆盖：正常路径、../各种位置、绝对路径、空字符串、边界情况。
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from app.common.exceptions import AppError
from app.common.path_validator import validate_file_path


@pytest.fixture
def base_dir(tmp_path):
    """创建临时基础目录"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.md").write_text("ok")
    return tmp_path


# ── 正常路径 ──

def test_normal_file(base_dir):
    """正常相对路径应返回 resolve 后的绝对路径"""
    result = validate_file_path("sub/file.md", base_dir)
    assert result == (base_dir / "sub" / "file.md").resolve()


def test_simple_filename(base_dir):
    """单文件名应正常通过"""
    result = validate_file_path("SKILL.md", base_dir)
    assert result == (base_dir / "SKILL.md").resolve()


def test_nested_path(base_dir):
    """多级嵌套路径应正常通过"""
    result = validate_file_path("a/b/c/d.yaml", base_dir)
    assert str(result).startswith(str(base_dir.resolve()))


# ── 路径穿越攻击 ──

def test_dotdot_prefix(base_dir):
    """以 .. 开头的路径应被拒绝"""
    with pytest.raises(AppError) as exc_info:
        validate_file_path("../etc/passwd", base_dir)
    assert exc_info.value.status == 400


def test_dotdot_embedded(base_dir):
    """中间包含 /../ 的路径应被拒绝"""
    with pytest.raises(AppError) as exc_info:
        validate_file_path("sub/../../../etc/passwd", base_dir)
    assert exc_info.value.status == 400


def test_dotdot_trailing(base_dir):
    """以 /.. 结尾的路径：被快速检查拦截（包含 /../）或 resolve 防御捕获"""
    # "sub/.." 不包含 "/../" 但 startswith("..") 也不匹配
    # resolve 后等于 base_dir 自身，relative_to 成功 → 不报错，这是安全的
    result = validate_file_path("sub/..", base_dir)
    assert result == base_dir.resolve()


def test_absolute_path(base_dir):
    """绝对路径应被拒绝"""
    with pytest.raises(AppError) as exc_info:
        validate_file_path("/etc/passwd", base_dir)
    assert exc_info.value.status == 400


def test_empty_path(base_dir):
    """空路径应被拒绝"""
    with pytest.raises(AppError) as exc_info:
        validate_file_path("", base_dir)
    assert exc_info.value.status == 400


def test_dotdot_only(base_dir):
    """纯 .. 应被拒绝"""
    with pytest.raises(AppError) as exc_info:
        validate_file_path("..", base_dir)
    assert exc_info.value.status == 400


def test_dotdot_url_encoded_like(base_dir):
    """..%2f 变体以 .. 开头，被快速检查拦截"""
    # "..%2fetc" startswith("..") → 快速拒绝
    with pytest.raises(AppError) as exc_info:
        validate_file_path("..%2fetc", base_dir)
    assert exc_info.value.status == 400


def test_multiple_slashes(base_dir):
    """多余斜杠不应绕过防护"""
    result = validate_file_path("sub//file.md", base_dir)
    assert str(result).startswith(str(base_dir.resolve()))


def test_dot_path(base_dir):
    """单个 . 应解析为基础目录自身"""
    result = validate_file_path(".", base_dir)
    assert result == base_dir.resolve()


# ── Unicode 路径 ──

def test_chinese_filename(base_dir):
    """中文文件名应正常通过"""
    result = validate_file_path("数据/报告.md", base_dir)
    assert "数据" in str(result)
