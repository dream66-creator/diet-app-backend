"""工具集入口：导入即完成全部工具的注册（副作用在 base.register_tool）。

对外暴露：
- get_openai_tools()  阶段 2 主循环取工具 schema
- dispatch_tool()     阶段 2 主循环分发执行
- TOOL_REGISTRY       调试/测试用
"""
from app.agent.tools.base import (  # noqa: F401
    TOOL_REGISTRY, dispatch_tool, fail, get_openai_tools, ok, register_tool,
)
# 导入以下模块触发工具注册（模块底部调用 register_tool）
from app.agent.tools import calc_tools  # noqa: E402,F401
from app.agent.tools import context_tools  # noqa: E402,F401
from app.agent.tools import food_tools  # noqa: E402,F401
from app.agent.tools import plan_tools  # noqa: E402,F401
from app.agent.tools import profile_tools  # noqa: E402,F401
from app.agent.tools import record_tools  # noqa: E402,F401
