"""Agent 节点工具函数"""


def get_state_val(state, key: str, default=None):
    """兼容 dict 和 Pydantic state 的取值"""
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)
