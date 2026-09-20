"""组织架构 delete/move 操作测试"""

import pytest


@pytest.mark.asyncio
async def test_delete_leaf_node(client):
    """删除叶子节点（无子部门）应成功"""
    # 创建根部门
    resp = await client.post("/api/org/", json={"name": "总部"})
    assert resp.status_code == 200
    root_id = resp.json()["id"]

    # 创建叶子部门
    resp = await client.post("/api/org/", json={"name": "市场部", "parent_id": root_id})
    assert resp.status_code == 200
    leaf_id = resp.json()["id"]

    # 删除叶子节点
    resp = await client.delete(f"/api/org/{leaf_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted_id"] == leaf_id
    assert data["children_moved"] == 0

    # 确认树中不再有该节点
    resp = await client.get("/api/org/tree")
    assert resp.status_code == 200
    tree = resp.json()
    all_ids = _collect_ids(tree)
    assert leaf_id not in all_ids


@pytest.mark.asyncio
async def test_delete_with_children_no_force(client):
    """删除有子部门的节点（force=False）应报错 ORG_HAS_CHILDREN"""
    # 构建二级结构：根 -> 子
    resp = await client.post("/api/org/", json={"name": "根"})
    root_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "子部门", "parent_id": root_id})
    child_id = resp.json()["id"]

    # 删除根节点（不强制）
    resp = await client.delete(f"/api/org/{root_id}")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ORG_HAS_CHILDREN"


@pytest.mark.asyncio
async def test_delete_with_children_force(client):
    """force=True 删除有子部门的节点，子部门应上移到父节点"""
    # 构建三级结构：根 -> 中间 -> 叶
    resp = await client.post("/api/org/", json={"name": "集团"})
    root_id = resp.json()["id"]
    root_path = resp.json()["path"]

    resp = await client.post("/api/org/", json={"name": "事业部", "parent_id": root_id})
    mid_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "小组A", "parent_id": mid_id})
    leaf_id = resp.json()["id"]

    # 强制删除中间层
    resp = await client.delete(f"/api/org/{mid_id}?force=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["children_moved"] == 1

    # 验证叶子节点的 parent_id 已上移到根
    resp = await client.get("/api/org/tree")
    tree = resp.json()
    root_node = _find_node(tree, root_id)
    assert root_node is not None
    # 叶子应直接挂在根下
    child_ids = [c["id"] for c in root_node.get("children", [])]
    assert leaf_id in child_ids

    # 验证叶子的 path 已更新（应为 /root_id/leaf_id）
    leaf_node = _find_node(tree, leaf_id)
    assert leaf_node is not None
    expected_path = f"{root_path}/{leaf_id}"
    assert leaf_node["path"] == expected_path


@pytest.mark.asyncio
async def test_move_success(client):
    """移动节点成功，路径应更新"""
    # 构建：根A，根B，根A -> 子C
    resp = await client.post("/api/org/", json={"name": "部门A"})
    a_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "部门B"})
    b_id = resp.json()["id"]
    b_path = resp.json()["path"]

    resp = await client.post("/api/org/", json={"name": "子部门C", "parent_id": a_id})
    c_id = resp.json()["id"]

    # 将 C 从 A 移动到 B
    resp = await client.put(f"/api/org/{c_id}/move", json={"new_parent_id": b_id})
    assert resp.status_code == 200
    moved = resp.json()
    assert moved["parent_id"] == b_id
    assert moved["path"] == f"{b_path}/{c_id}"


@pytest.mark.asyncio
async def test_move_cycle_detection(client):
    """移动节点到自己的子孙应被拒绝（循环检测）"""
    # 构建：A -> B -> C
    resp = await client.post("/api/org/", json={"name": "顶层"})
    a_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "中层", "parent_id": a_id})
    b_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "底层", "parent_id": b_id})
    c_id = resp.json()["id"]

    # 尝试将 A 移到 C 下面（循环）
    resp = await client.put(f"/api/org/{a_id}/move", json={"new_parent_id": c_id})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PARAM_INVALID"

    # 尝试将 A 移到 B 下面（也是循环）
    resp = await client.put(f"/api/org/{a_id}/move", json={"new_parent_id": b_id})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_move_cascade_path_update(client):
    """移动节点后，其所有后代的 path 应级联更新"""
    # 构建：A -> B -> C -> D，另有独立的 E
    resp = await client.post("/api/org/", json={"name": "A"})
    a_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "B", "parent_id": a_id})
    b_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "C", "parent_id": b_id})
    c_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "D", "parent_id": c_id})
    d_id = resp.json()["id"]

    resp = await client.post("/api/org/", json={"name": "E"})
    e_id = resp.json()["id"]
    e_path = resp.json()["path"]

    # 将 B（及其子树 C, D）移到 E 下
    resp = await client.put(f"/api/org/{b_id}/move", json={"new_parent_id": e_id})
    assert resp.status_code == 200

    # 验证整个子树的 path 都已级联更新
    resp = await client.get("/api/org/tree")
    tree = resp.json()

    b_node = _find_node(tree, b_id)
    c_node = _find_node(tree, c_id)
    d_node = _find_node(tree, d_id)

    assert b_node["path"] == f"{e_path}/{b_id}"
    assert c_node["path"] == f"{e_path}/{b_id}/{c_id}"
    assert d_node["path"] == f"{e_path}/{b_id}/{c_id}/{d_id}"


@pytest.mark.asyncio
async def test_delete_nonexistent(client):
    """删除不存在的节点应返回 404"""
    resp = await client.delete("/api/org/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_move_nonexistent(client):
    """移动不存在的节点应返回 404"""
    resp = await client.put("/api/org/nonexistent-id/move", json={"new_parent_id": None})
    assert resp.status_code == 404


# ===== 辅助函数 =====


def _collect_ids(tree: list[dict]) -> set[str]:
    """递归收集树中所有节点 id"""
    ids = set()
    for node in tree:
        ids.add(node["id"])
        ids.update(_collect_ids(node.get("children", [])))
    return ids


def _find_node(tree: list[dict], node_id: str) -> dict | None:
    """递归查找指定 id 的节点"""
    for node in tree:
        if node["id"] == node_id:
            return node
        found = _find_node(node.get("children", []), node_id)
        if found:
            return found
    return None
