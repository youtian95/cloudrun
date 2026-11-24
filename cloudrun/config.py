"""配置加载模块：读取 config.yaml 并展平成各模块使用的属性。

功能：把 YAML 里 server/remote/env/sync/run/power/provider/aliyun 各段读成 Config 对象。
使用流程：Config.load("config.yaml") -> 带默认值的配置对象。
输入：config.yaml 路径。
输出：Config 实例。
"""

from __future__ import annotations

import os

import yaml

# 下载排除项默认值：只排垃圾与内部标记，绝不排结果目录（结果要原位写回）
DEFAULT_DOWNLOAD_EXCLUDE = ["__pycache__", "*.pyc", "*.egg-info", "build", "dist", ".git", ".venv", "venv", ".cloudrun", ".cloudrun_run.*"]


class Config:
    """一次运行所需的全部配置。

    属性：
        server_host/server_port/server_user/server_key/server_password: SSH 连接信息。
        remote_workdir: 云端根目录（项目镜像/venv/wheels 放这下面）。
        remote_python: 云端回退 venv 用的 Python 命令。
        remote_index: 云端 pip/uv 源。
        remote_setup_commands: 首次连接时执行的云端 Bash 初始化脚本。
        env_source: 环境来源 auto/uv/requirements/freeze。
        env_requirements: requirements.txt 路径（source=requirements）。
        env_freeze_python: 要冻结的本地解释器路径（source=freeze）。
        env_wheels: 自定义 wheel 路径列表（单独安装/覆盖）。
        sync_exclude: 不上传的模式列表。
        run_command: 未指定脚本时默认执行的命令。
        run_poll_interval: 运行日志的轮询间隔（秒）。
        run_detach: 是否启动后即返回。
        power_on/power_off: 本机开关机钩子命令。
        provider: 云服务商配置段（按服务商名分组，如 provider.aliyun；name 可显式指定）。
    """

    def __init__(self, data: dict):
        """从 YAML 初始化配置。参数 data: 已解析的 YAML 字典，缺失项套用默认值。"""
        # 逐段取值并给默认值，少写某段也不报错
        server = data.get("server", {})
        remote = data.get("remote", {})
        env = data.get("env", {})
        sync = data.get("sync", {})
        run = data.get("run", {})
        power = data.get("power", {})

        self.server_host = server.get("host", "")
        self.server_port = int(server.get("port", 22))
        self.server_user = server.get("user", "root")
        self.server_key = os.path.expanduser(server.get("key_file", "") or "")
        self.server_password = server.get("password", "")

        self.remote_workdir = remote.get("workdir", "/root/cloudrun").rstrip("/")
        self.remote_python = remote.get("python", "python3")
        self.remote_index = remote.get("index_url", "https://mirrors.aliyun.com/pypi/simple/")
        # 接受 Bash 脚本块或命令列表，列表中的命令在同一个 Bash 进程里执行。
        setup = remote.get("setup_commands")
        if setup is None:
            setup = ""
        if isinstance(setup, list) and all(isinstance(command, str) for command in setup):
            setup = "\n".join(setup)
        if not isinstance(setup, str):
            raise ValueError("remote.setup_commands 必须是 Bash 脚本字符串或字符串列表")
        self.remote_setup_commands = setup.strip()

        self.env_source = env.get("source", "auto")
        self.env_requirements = os.path.expanduser(env.get("requirements", "") or "")
        self.env_freeze_python = os.path.expanduser(env.get("freeze_python", "") or "")
        self.env_wheels = [os.path.expanduser(w) for w in env.get("wheels", [])]

        self.sync_exclude = sync.get("exclude", ["__pycache__", "*.pyc", "*.egg-info", "build", "dist", ".git", ".venv", "venv", "results", "wheels", "config.yaml", ".cloudrun", "*.db"])
        self.sync_download_exclude = sync.get("download_exclude", DEFAULT_DOWNLOAD_EXCLUDE)
        self.run_command = run.get("command", "python run.py")
        self.run_poll_interval = float(run.get("poll_interval", 5))
        self.run_detach = bool(run.get("detach", False))
        self.power_on = power.get("on", "") or ""
        self.power_off = power.get("off", "") or ""
        # 云服务商配置：provider 段下按服务商名分组（如 provider.aliyun），name 可显式指定
        self.provider = data.get("provider", {}) or {}

    @classmethod
    def load(cls, path: str) -> "Config":
        """从 YAML 文件加载配置。

        参数：
            path: config.yaml 的路径。

        返回：
            Config 实例。
        """
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)
