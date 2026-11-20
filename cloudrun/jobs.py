"""作业编排：init/push/run/fetch/logs/status/up/down/destroy，以及"边算边取回"的跟随循环。

功能：
    init  在工作目录生成 config.yaml 模板；
    push  增量上传项目 + 同步环境（uv sync 或 venv）；
    run   指定脚本自动转运行命令 -> push -> 云端后台启动 -> 跟随（实时日志 + 增量取回）到算完；
    fetch 增量拉回新/变动文件，原位写回工作目录；logs 看日志；status 查存活。
使用流程：cloudrun init -> 填 config.yaml -> cloudrun run 脚本.py。
输入：Config、工作目录（config.yaml 所在目录）。
输出：新/变动文件写回工作目录原相对路径、实时日志。
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from .config import Config
from .env import ensure_env, _UV_PATH, detect_mode
from .providers import get_provider
from .ssh import Remote
from .template import TEMPLATE


def _remote_project(cfg: Config, workdir: str) -> str:
    """远程项目目录：本地工作目录按文件夹名镜像到 remote_workdir 下。"""
    return cfg.remote_workdir + "/" + os.path.basename(workdir.rstrip(os.sep))


def _snapshot_path(cfg: Config, workdir: str) -> str:
    """文件状态快照 JSON 的本地路径（workdir/.cloudrun/snapshot.json）。"""
    return os.path.join(workdir, ".cloudrun", "snapshot.json")


def _hook(cmd: str) -> None:
    """在本机执行开机/关机钩子命令，命令为空则跳过。"""
    if cmd:
        subprocess.run(cmd, shell=True, check=True)


def _apply_provider(cfg: Config, workdir: str) -> None:
    """若配置了云服务商，把状态文件里的公网 IP/密钥套用到 cfg。"""
    provider = get_provider(cfg)
    if provider:
        provider.apply_state(cfg, workdir)


def up(cfg: Config, workdir: str) -> None:
    """云服务商：自动供应并开机，未配置 provider 时报错。"""
    provider = get_provider(cfg)
    if provider is None:
        raise ValueError("未配置云服务商（请在 config 启用，如 aliyun.enabled: true）")
    provider.up(cfg, workdir)


def down(cfg: Config, workdir: str) -> None:
    """云服务商：停机不收费。"""
    provider = get_provider(cfg)
    if provider:
        provider.down(cfg, workdir)


def destroy(cfg: Config, workdir: str, cleanup: bool = False) -> None:
    """云服务商：释放实例；cleanup 为 True 时一并删除网络与密钥。"""
    provider = get_provider(cfg)
    if provider:
        provider.destroy(cfg, workdir, cleanup)


def build_command(cfg: Config, args: list | None) -> str:
    """构造远程运行命令，语义与本地 python 调用保持一致。

    参数：
        cfg: 配置（默认取 run_command）。
        args: 传给 python 的参数列表，如 ['-m', '包.模块'] 或 ['脚本.py']。

    返回：
        最终要执行的命令字符串。
    """
    if args:
        a = args[1:] if args[0] == "--" else args   # 兼容 cloudrun run -- -m 包.模块
        return "python " + " ".join(a)
    return cfg.run_command


def _push(remote: Remote, cfg: Config, workdir: str) -> tuple[dict, str]:
    """增量上传项目、同步环境，并记录文件状态快照。

    参数：
        remote: SSH 会话。
        cfg: 配置（读 sync_exclude）。
        workdir: 本地工作目录。

    返回：
        (snapshot, mode)：snapshot 为 {相对路径: (大小, mtime)}，mode 为 uv 或 venv。
    """
    mode = detect_mode(cfg, workdir)
    remote_project = _remote_project(cfg, workdir)
    up, skip, snapshot = remote.upload_tree(workdir, remote_project, cfg.sync_exclude)
    print(f"上传 {up} 个文件，跳过 {skip} 个未变化文件 -> {remote_project}")
    ensure_env(remote, cfg, workdir, mode, remote_project)
    os.makedirs(os.path.join(workdir, ".cloudrun"), exist_ok=True)
    with open(_snapshot_path(cfg, workdir), "w", encoding="utf-8") as f:
        json.dump(snapshot, f)
    return snapshot, mode


def _launch(remote: Remote, cfg: Config, remote_project: str, cmd: str, mode: str) -> None:
    """用 nohup 在云端后台启动作业，并写入 pid 与日志文件。

    参数：
        remote: SSH 会话。
        cfg: 配置（venv 模式下用 remote_workdir 定位虚拟环境）。
        remote_project: 远程项目目录。
        cmd: 要执行的完整命令。
        mode: 环境模式，取值 uv 或 venv。
    """
    script = remote_project + "/.cloudrun_run.sh"
    log = remote_project + "/.cloudrun_run.log"
    pid = remote_project + "/.cloudrun_run.pid"
    if mode == "uv":
        body = f"cd {remote_project}\n{_UV_PATH}\nexec uv run {cmd}\n"
    else:
        venv = cfg.remote_workdir + "/venv"
        body = f"cd {remote_project}\nsource {venv}/bin/activate\nexec {cmd}\n"
    remote.write_text(script, "#!/bin/bash\n" + body)
    remote.run(f"nohup bash {script} > {log} 2>&1 & echo $! > {pid}")


def _follow(remote: Remote, cfg: Config, remote_project: str, snapshot: dict, workdir: str) -> None:
    """跟随后台作业：流式读日志 + 周期性增量取回新文件，直到进程退出。

    参数：
        remote: SSH 会话。
        cfg: 配置（读 run_poll_interval）。
        remote_project: 远程项目目录。
        snapshot: 上传时记录的文件状态快照，用于跳过未变化的文件。
        workdir: 本地工作目录（结果原位写回这里）。
    """
    log = remote_project + "/.cloudrun_run.log"
    pid = remote_project + "/.cloudrun_run.pid"
    offset = 0
    while True:
        text, offset = remote.read_log(log, offset)
        if text:
            print(text, end="", flush=True)
        down, _ = remote.download_tree(remote_project, workdir, snapshot=snapshot, exclude=cfg.sync_download_exclude)
        if down:
            print(f"[取回 {down} 个新文件]", flush=True)
        if not remote.alive(pid):
            text, offset = remote.read_log(log, offset)
            if text:
                print(text, end="", flush=True)
            return
        time.sleep(cfg.run_poll_interval)


def init(workdir: str, force: bool = False) -> None:
    """在工作目录生成 config.yaml 模板，已存在时默认跳过。

    参数：
        workdir: 目标目录。
        force: 为 True 时覆盖已存在的 config.yaml。
    """
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, "config.yaml")
    if os.path.exists(path) and not force:
        print("config.yaml 已存在，未覆盖（加 --force 可强制重新生成）")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(TEMPLATE)
    print("已生成", path)
    print("请填写 server 连接信息与 env.wheels，然后 cloudrun run 脚本.py")


def push(cfg: Config, workdir: str) -> None:
    """只同步项目与环境到云端，不运行。"""
    _apply_provider(cfg, workdir)
    remote = Remote(cfg)
    try:
        _push(remote, cfg, workdir)
    finally:
        remote.close()


def run(cfg: Config, workdir: str, args: list | None = None) -> None:
    """提交并运行：push -> 后台启动 -> 跟随取回，直到算完（config.run_detach 可启动即返回）。

    参数：
        cfg: 配置。
        workdir: 本地工作目录。
        args: 传给 python 的参数（与本地一致，如 ['-m', '包.模块']）。
    """
    detach = cfg.run_detach
    cmd = build_command(cfg, args)
    provider = get_provider(cfg)
    if provider:
        provider.up(cfg, workdir)
    remote_project = _remote_project(cfg, workdir)
    _hook(cfg.power_on)
    remote = Remote(cfg)
    completed = False
    try:
        snapshot, mode = _push(remote, cfg, workdir)
        _launch(remote, cfg, remote_project, cmd, mode)
        if detach:
            print("已后台启动。cloudrun logs/status 查看，cloudrun fetch 取结果")
            return
        print(f"开始运行：{cmd}（Ctrl-C 可断开，云端继续算）")
        try:
            _follow(remote, cfg, remote_project, snapshot, workdir)
        except KeyboardInterrupt:
            print("\n已断开跟随，云端仍在运行。cloudrun fetch 取结果，cloudrun logs --follow 继续看")
            return
        # 算完显式最终取回，确保关机前结果已全部拉回本地
        down, _ = remote.download_tree(remote_project, workdir, snapshot=snapshot, exclude=cfg.sync_download_exclude)
        if down:
            print(f"[最终取回 {down} 个文件]", flush=True)
        completed = True
        print("运行结束，结果已写回工作目录")
        _hook(cfg.power_off)
    finally:
        remote.close()
    # 前台正常算完才自动停机/释放；后台或中途断开则不动，云端继续
    if provider and completed:
        if provider.reuse(cfg):
            provider.down(cfg, workdir)
        else:
            provider.destroy(cfg, workdir)


def fetch(cfg: Config, workdir: str) -> None:
    """增量拉回新产生/变动的文件，按原相对路径写回工作目录。"""
    remote_project = _remote_project(cfg, workdir)
    snapshot = None
    ip = _snapshot_path(cfg, workdir)
    if os.path.isfile(ip):
        with open(ip, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
    _apply_provider(cfg, workdir)
    remote = Remote(cfg)
    try:
        down, skip = remote.download_tree(remote_project, workdir, snapshot=snapshot, exclude=cfg.sync_download_exclude)
        print(f"下载 {down} 个文件（跳过 {skip}）-> 工作目录")
    finally:
        remote.close()


def logs(cfg: Config, workdir: str, tail: int = 50, follow: bool = False) -> None:
    """查看运行日志。

    参数：
        cfg: 配置（读 run_poll_interval）。
        workdir: 本地工作目录（用于定位远程项目）。
        tail: 首次打印的末尾行数。
        follow: 为 True 时持续追加新日志，Ctrl-C 退出。
    """
    log = _remote_project(cfg, workdir) + "/.cloudrun_run.log"
    _apply_provider(cfg, workdir)
    remote = Remote(cfg)
    try:
        print(remote.output(f"tail -n {tail} {log}"))
        if follow:
            try:
                offset = remote.sftp.stat(log).st_size
            except FileNotFoundError:
                offset = 0
            while True:
                text, offset = remote.read_log(log, offset)
                if text:
                    print(text, end="", flush=True)
                time.sleep(cfg.run_poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        remote.close()


def status(cfg: Config, workdir: str) -> None:
    """查询后台作业进程是否仍在运行。"""
    pid = _remote_project(cfg, workdir) + "/.cloudrun_run.pid"
    _apply_provider(cfg, workdir)
    remote = Remote(cfg)
    try:
        print("RUNNING" if remote.alive(pid) else "STOPPED")
    finally:
        remote.close()