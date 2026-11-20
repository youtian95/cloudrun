"""云服务商插件包：每个服务商一个模块，统一接口，按配置懒加载选用。

功能：get_provider(cfg) 依 provider.name 显式指定、或自动检测已启用的服务商，返回对应模块。
      每个服务商模块须实现：is_enabled/reuse/up/down/destroy/apply_state。
      新增服务商：加一个模块文件并在 PROVIDERS 登记名字即可，核心代码无需改动。
使用流程：jobs 调 get_provider(cfg) 拿到模块再调 up/down 等。
输入：Config。
输出：服务商模块；未配置任何服务商时返回 None。
"""

from __future__ import annotations

import importlib

from ..config import Config

# 登记所有服务商模块名（新增服务商在此追加）
PROVIDERS = ["aliyun"]


def get_provider(cfg: Config):
    """按 provider.name 显式指定，或自动探测已启用的服务商模块。

    参数：
        cfg: 配置。

    返回：
        服务商模块；未配置任何服务商时返回 None。
    """
    name = cfg.provider.get("name")
    if name:
        return importlib.import_module(f".{name}", __package__)
    for name in PROVIDERS:
        mod = importlib.import_module(f".{name}", __package__)
        if mod.is_enabled(cfg):
            return mod
    return None