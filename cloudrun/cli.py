"""命令行入口：解析 init/size/push/run/fetch/logs/status/up/down/destroy 并调用 jobs。

功能：提供 cloudrun <子命令> 的 CLI。始终使用当前工作目录下的 config_cloudrun.yaml。
使用流程：cd 工作目录 -> cloudrun init -> 填 config_cloudrun.yaml -> cloudrun run。
输入：命令行参数 + 当前目录 config_cloudrun.yaml。
输出：调用对应作业函数。
"""

from __future__ import annotations

import argparse
import os
import sys

from .config import CONFIG_FILENAME, Config
from . import jobs


def _prepare_run_argv(argv: list) -> list:
    """在 python 参数前补 --，让 run 子命令免写分隔符。参数 argv: sys.argv[1:]。返回处理后的参数列表；非 run 原样返回。"""
    if "run" not in argv:
        return argv
    i = argv.index("run")
    head, tail = argv[:i + 1], argv[i + 1:]
    if not tail or tail[0] in ("-h", "--help", "--"):
        return argv   # 无参数/帮助/已带 -- 都交给 argparse
    return head + ["--"] + tail


def main() -> None:
    """解析命令行参数并分发到对应作业函数。"""
    parser = argparse.ArgumentParser(prog="cloudrun", description="把本地 Python 计算提交到自租云服务器执行")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # init：生成配置模板
    p_init = sub.add_parser("init", help=f"在工作目录生成 {CONFIG_FILENAME} 模板")
    p_init.add_argument("dir", nargs="?", default=".", help="目标目录（默认当前目录）")
    p_init.add_argument("--force", action="store_true", help="已存在时强制覆盖")

    # size：本地统计；push：同步文件与环境
    sub.add_parser("size", help="统计全量上传的本地文件数与大小，无需连接服务器")
    sub.add_parser("push", help="同步本地文件与环境到云端（不运行）")

    # run：自动同步并启动作业，跟随日志，结束后下载结果
    p_run = sub.add_parser("run", help="自动 push 并运行，参数原样转发给 python")
    p_run.add_argument("args", nargs=argparse.REMAINDER, help="传给 python 的参数，与本地一致（如 -m 包.模块 或 脚本.py）")

    # fetch：拉回结果
    sub.add_parser("fetch", help="增量拉回结果到本地")

    # logs：看日志
    p_logs = sub.add_parser("logs", help="查看运行日志")
    p_logs.add_argument("--tail", type=int, default=50, help="末尾行数（默认 50）")
    p_logs.add_argument("--follow", action="store_true", help="持续跟随日志")

    # status：查存活
    sub.add_parser("status", help="查看作业是否仍在运行")

    # up/down/destroy：服务器生命周期
    sub.add_parser("up", help="云服务商：自动创建/启动实例并分配公网")
    sub.add_parser("down", help="云服务商：停机不收费")
    p_destroy = sub.add_parser("destroy", help="云服务商：释放实例")
    p_destroy.add_argument("--cleanup", action="store_true", help="一并删除网络/密钥")

    args = parser.parse_args(_prepare_run_argv(sys.argv[1:]))

    if args.cmd == "init":
        jobs.init(os.path.abspath(args.dir), force=args.force)
        return

    # 其余命令读取当前目录下的 Cloudrun 专用配置。
    workdir = os.getcwd()
    try:
        cfg = Config.load(os.path.join(workdir, CONFIG_FILENAME))
    except FileNotFoundError:
        parser.error(f"当前目录缺少 {CONFIG_FILENAME}。请重命名已有的 Cloudrun 配置，或运行 cloudrun init 生成模板。")

    if args.cmd == "size":
        jobs.size(cfg, workdir)
    elif args.cmd == "push":
        jobs.push(cfg, workdir)
    elif args.cmd == "run":
        jobs.run(cfg, workdir, args=args.args)
    elif args.cmd == "fetch":
        jobs.fetch(cfg, workdir)
    elif args.cmd == "logs":
        jobs.logs(cfg, workdir, tail=args.tail, follow=args.follow)
    elif args.cmd == "status":
        jobs.status(cfg, workdir)
    elif args.cmd == "up":
        jobs.up(cfg, workdir)
    elif args.cmd == "down":
        jobs.down(cfg, workdir)
    elif args.cmd == "destroy":
        jobs.destroy(cfg, workdir, cleanup=args.cleanup)


if __name__ == "__main__":
    main()
