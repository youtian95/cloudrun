"""云端环境管理：本地检测环境模式与依赖 + 远程安装还原。

功能：
    检测——detect_mode 判断走 uv 还是 venv；resolve_requirements 解析本地依赖成 requirements
          （auto 依次检测 .venv/requirements.txt/pyproject，freeze 冻结指定解释器），剔除 wheel 已提供的包。
    初始化——ensure_system_setup 在云端执行一次配置的系统开发环境命令。
    安装——ensure_env 上传自定义 wheel；uv 模式远程装 uv 并 uv sync（建 .venv、装锁定依赖、安装项目），
          再强制覆盖自定义 wheel；venv 回退模式建 venv 装 wheel+requirements。按哈希缓存复用。
使用流程：连接后执行 ensure_system_setup；项目上传后执行 detect_mode 和 ensure_env 同步 Python 环境。
输入：Config、工作目录、Remote、模式、远程项目目录。
输出：环境模式字符串 / requirements 文本 / 云端环境就绪。
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess

from .config import Config
from .ssh import Remote

# uv 安装脚本装到 ~/.local/bin，远程命令统一补 PATH
_UV_PATH = 'export PATH="$HOME/.local/bin:$PATH"'


# ---------- 本地检测 ----------
def detect_mode(cfg: Config, workdir: str) -> str:
    """决定用 uv 还是 venv 同步环境。

    参数：
        cfg: 配置（env.source 为 uv/requirements/freeze 时直接定模式）。
        workdir: 本地工作目录（auto 模式下据此探测 uv 项目）。

    返回：
        'uv'（存在 pyproject.toml + uv.lock）或 'venv'（回退）。
    """
    source = cfg.env_source
    if source == "uv":
        return "uv"
    if source in ("requirements", "freeze"):
        return "venv"
    # auto：工作目录是 uv 项目就用 uv，否则回退 venv
    if os.path.isfile(os.path.join(workdir, "pyproject.toml")) and os.path.isfile(os.path.join(workdir, "uv.lock")):
        return "uv"
    return "venv"


def _freeze(python: str) -> str:
    """用 pip freeze 冻结指定解释器已安装的包。参数 python: 本地解释器路径。返回 pip freeze 原始文本。"""
    result = subprocess.run([python, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
    return result.stdout


def _skip_names(cfg: Config) -> tuple:
    """列出要从 requirements 剔除的包名：cloudrun 本身 + 各自定义 wheel 提供的包。参数 cfg: 配置。返回小写包名前缀元组。"""
    names = ["cloudrun"]
    for w in cfg.env_wheels:
        names.append(os.path.basename(w).split("-")[0].lower())
    return tuple(names)


def _filter(text: str, skip: tuple) -> str:
    """按包名前缀剔除 requirements 文本中的对应行。参数 text: 文本；skip: 小写包名前缀元组。返回过滤后的文本。"""
    lines = []
    for line in text.splitlines():
        name = line.split("==")[0].split("@")[0].split(">=")[0].strip().lower()
        if name and not name.startswith(skip):
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _venv_python(workdir: str) -> str:
    """在工作目录下找 .venv 或 venv 的解释器（兼容 Windows/POSIX）。参数 workdir: 工作目录。返回解释器路径，无则空。"""
    for venv in (".venv", "venv"):
        win = os.path.join(workdir, venv, "Scripts", "python.exe")
        posix = os.path.join(workdir, venv, "bin", "python")
        if os.path.isfile(win):
            return win
        if os.path.isfile(posix):
            return posix
    return ""


def _pyproject_deps(workdir: str) -> str:
    """从 pyproject.toml 的 [project].dependencies 生成 requirements。参数 workdir: 工作目录。返回每行一个依赖的文本，无则空。"""
    path = os.path.join(workdir, "pyproject.toml")
    if not os.path.isfile(path):
        return ""
    import tomllib
    with open(path, "rb") as f:
        data = tomllib.load(f)
    deps = data.get("project", {}).get("dependencies", [])
    return "\n".join(deps) + ("\n" if deps else "")


def resolve_requirements(cfg: Config, workdir: str) -> str:
    """按 env.source 解析出要同步到云端的依赖，并剔除不需要的行。

    参数：
        cfg: 配置（读 env_source/env_requirements/env_freeze_python/env_wheels）。
        workdir: 本地工作目录（auto 模式的探测根目录）。

    返回：
        过滤后的 requirements 文本，可能为空字符串。

    异常：
        ValueError: env.source=freeze 但未填写 env.freeze_python。
    """
    skip = _skip_names(cfg)
    source = cfg.env_source
    if source == "requirements":
        path = cfg.env_requirements or os.path.join(workdir, "requirements.txt")
        with open(path, "r", encoding="utf-8") as f:
            return _filter(f.read(), skip)
    if source == "freeze":
        if not cfg.env_freeze_python:
            raise ValueError("env.source=freeze 需要填写 env.freeze_python")
        return _filter(_freeze(cfg.env_freeze_python), skip)

    # auto：依次检测本地环境
    venv_py = _venv_python(workdir)
    if venv_py:
        print(f"检测到本地虚拟环境：{venv_py}，冻结其依赖")
        return _filter(_freeze(venv_py), skip)
    req = os.path.join(workdir, "requirements.txt")
    if os.path.isfile(req):
        print(f"检测到 {req}")
        with open(req, "r", encoding="utf-8") as f:
            return _filter(f.read(), skip)
    deps = _pyproject_deps(workdir)
    if deps:
        print("检测到 pyproject.toml 依赖")
        return _filter(deps, skip)

    # 都没检测到 -> 手动输入
    answer = input("未检测到本地环境。请输入 requirements.txt 路径，或要冻结的本地 python 路径：").strip()
    if not answer:
        return ""
    if answer.lower().endswith(".txt"):
        with open(answer, "r", encoding="utf-8") as f:
            return _filter(f.read(), skip)
    return _filter(_freeze(answer), skip)


# ---------- 远程安装 ----------
def ensure_system_setup(remote: Remote, cfg: Config) -> None:
    """在云端工作根目录执行一次系统初始化，失败后允许重试。

    参数：
        remote: 已连接的 SSH 会话。
        cfg: 配置（提供工作根目录和 Bash 初始化脚本）。
    """
    if not cfg.remote_setup_commands:
        return

    # 在同一把远端文件锁下检查和写入标记，避免并发启动时重复安装。
    marker = shlex.quote(cfg.remote_workdir + "/.cloudrun_system_ready")
    lock = shlex.quote(cfg.remote_workdir + "/.cloudrun_system_setup.lock")
    workdir = shlex.quote(cfg.remote_workdir or "/")
    commands = shlex.quote(cfg.remote_setup_commands)
    setup = f'''if [[ -f {marker} ]]; then exit 0; fi
echo "初始化云端系统开发环境……"
(cd -- {workdir} && bash -e -o pipefail -c {commands}) && touch -- {marker}'''
    remote.run(f"mkdir -p -- {workdir} && flock --close -- {lock} bash -c {shlex.quote(setup)}", check=True)


def _upload_wheels(remote: Remote, cfg: Config) -> tuple[str, list]:
    """把自定义 wheel 上传到云端根目录的 wheels/。参数 remote: 会话；cfg: 配置。返回 (wheels 目录, 文件名列表)。"""
    wheels_dir = cfg.remote_workdir + "/wheels"
    remote.mkdirs(wheels_dir)
    names = []
    for w in cfg.env_wheels:
        name = os.path.basename(w)
        remote.put(w, wheels_dir + "/" + name)
        names.append(name)
    return wheels_dir, names


def _wheels_hash(cfg: Config):
    """把每个 wheel 的名/大小/mtime 喂进一个新 sha256 对象。参数 cfg: 配置。返回哈希对象（可继续 update）。"""
    h = hashlib.sha256()
    for w in cfg.env_wheels:
        st = os.stat(w)
        h.update(f"{os.path.basename(w)}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h


def ensure_uv(remote: Remote) -> None:
    """确保远端有 uv，没有就用官方脚本装到 ~/.local/bin。参数 remote: 会话。"""
    remote.run(f'{_UV_PATH}; command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh')


def ensure_env(remote: Remote, cfg: Config, workdir: str, mode: str, remote_project: str) -> None:
    """确保云端环境就绪；指纹未变化时直接复用，跳过安装。

    参数：
        remote: 已连接的 SSH 会话。
        cfg: 配置（读 remote_workdir/remote_python/remote_index）。
        workdir: 本地工作目录（读 uv.lock、检测本地环境）。
        mode: 同步模式，取值 uv 或 venv。
        remote_project: 远程项目目录（uv 模式的 .venv 建在这里）。
    """
    wheels_dir, wheel_names = _upload_wheels(remote, cfg)
    marker = cfg.remote_workdir + "/.cloudrun_env_hash"
    # 引用传给 Bash 的路径和值，保留原始路径供 SFTP 使用。
    index = shlex.quote(cfg.remote_index)

    if mode == "uv":
        ensure_uv(remote)
        h = _wheels_hash(cfg)
        with open(os.path.join(workdir, "uv.lock"), "rb") as f:
            h.update(f.read())
        fingerprint = h.hexdigest()
        if remote.exists(marker) and remote.exists(remote_project + "/.venv") and remote.read_text(marker).strip() == fingerprint:
            print("云端环境未变化，复用 uv 环境")
            return
        print("远程 uv sync 还原锁定环境并安装项目……")
        enter_project = f"cd -- {shlex.quote(remote_project)}"
        remote.run(f"{enter_project} && {_UV_PATH} && export UV_DEFAULT_INDEX={index} && uv sync", check=True)
        if wheel_names:
            # 只引用目录，让 Bash 继续展开 wheel 通配符。
            remote.run(f"{enter_project} && {_UV_PATH} && uv pip install --python .venv/bin/python --force-reinstall --no-deps {shlex.quote(wheels_dir)}/*.whl", check=True)
        remote.write_text(marker, fingerprint)
        print("云端环境就绪（uv）")
    else:
        req = resolve_requirements(cfg, workdir)
        requirements = cfg.remote_workdir + "/requirements.txt"
        venv = cfg.remote_workdir + "/venv"
        pip = shlex.quote(venv + "/bin/pip")
        h = _wheels_hash(cfg)
        h.update(req.encode("utf-8"))
        fingerprint = h.hexdigest()
        remote.write_text(requirements, req)
        if remote.exists(marker) and remote.exists(venv + "/bin/python") and remote.read_text(marker).strip() == fingerprint:
            print("云端环境未变化，复用 venv")
            return
        print("初始化云端 venv 并安装依赖……")
        remote.run(f"{cfg.remote_python} -m venv {shlex.quote(venv)}", check=True)
        for name in wheel_names:
            wheel = shlex.quote(wheels_dir + "/" + name)
            remote.run(f"{pip} install -i {index} {wheel}", check=True)
        if req.strip():
            remote.run(f"{pip} install -i {index} -r {shlex.quote(requirements)}", check=True)
        remote.write_text(marker, fingerprint)
        print("云端环境就绪（venv）")
