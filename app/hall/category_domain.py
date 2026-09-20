"""能力（category）到业务领域（domain）的映射。

大厅视图在 category 总数较多时，支持按 domain 折叠分组展示。未登记的 category
自动归入 `DEFAULT_DOMAIN`。维护这份表即可调整分组结果，无需改代码。
"""

from __future__ import annotations

# 默认的业务领域归属 —— 后续可改为从数据库/配置读取
CATEGORY_TO_DOMAIN: dict[str, str] = {
    # 营销
    "营销投放": "营销",
    "投放预警": "营销",
    "文案生成": "营销",
    "内容生成": "营销",
    "广告创意": "营销",
    "ROI 分析": "营销",
    # 客服
    "质检": "客服",
    "话术质检": "客服",
    "投诉归因": "客服",
    "投诉分级": "客服",
    "意图识别": "客服",
    "情绪分析": "客服",
    # 风控 / 合规
    "异常检测": "风控",
    "预警": "风控",
    "欺诈识别": "风控",
    "合规校验": "风控",
    # 销售 / 运营
    "派单": "销售",
    "线索": "销售",
    "客户分层": "销售",
    "订单处理": "销售",
    # 通用
    "数据核对": "通用",
    "任务分配": "通用",
    "其它": "通用",
}

DEFAULT_DOMAIN = "通用"

# 在前端渲染的领域排序
DOMAIN_ORDER: list[str] = ["营销", "客服", "风控", "销售", "运营", "通用"]


def get_domain(category: str | None) -> str:
    """查 category 对应 domain；未登记默认归 `DEFAULT_DOMAIN`。"""
    if not category:
        return DEFAULT_DOMAIN
    return CATEGORY_TO_DOMAIN.get(category, DEFAULT_DOMAIN)
