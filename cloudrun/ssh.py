"""SSH/SFTP 传输与远程执行核心。

功能：封装 paramiko，提供远程命令执行（实时流式输出）、后台启动、
      以及"只传变化文件"的增量上传/下载（按 大小+mtime 判断，保留 mtime）。
使用流程：Remote(cfg) 建立连接 -> upload_tree/run/download_tree -> close()。
输入：Config 对象、本地/远程路径。
输出：远程命令退出码、上传/下载文件计数、文件状态快照 snapshot。
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import shlex
import stat

import paramiko

from .config import Config


def _excluded(rel: str, patterns: list[str]) -> bool:
    """判断相对路径是否命中任一排除模式，对每一级目录名与整体路径都做匹配。

    参数：
        rel: 相对路径（用 / 分隔）。
        patterns: 排除模式列表，如 __pycache__、*.pyc。

    返回：
        命中任一模式则 True。
    """
    parts = rel.split("/")
    return any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(rel, pat) for pat in patterns for p in parts)


class Remote:
    """一台云服务器的 SSH/SFTP 会话。

    参数（构造）：
        cfg: Config 对象，提供主机/端口/用户/密钥等。
    """

    def __init__(self, cfg: Config):
        """建立 SSH/SFTP 会话，首次连接自动接受主机指纹。"""
        # 建立 SSH 连接；首次连接自动接受主机指纹（自租服务器场景足够）
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if cfg.server_key:
            self.ssh.connect(cfg.server_host, port=cfg.server_port, username=cfg.server_user, key_filename=cfg.server_key)
        else:
            self.ssh.connect(cfg.server_host, port=cfg.server_port, username=cfg.server_user, password=cfg.server_password)
        self.sftp = self.ssh.open_sftp()

    # ---------- 远程命令 ----------
    def run(self, cmd: str, check: bool = False) -> int:
        """在云端用 bash 执行命令，并逐行实时打印输出。

        参数：
            cmd: 要执行的 shell 命令，可含 source、&& 等 bash 语法。
            check: 为 True 时退出码非 0 直接抛异常。

        返回：
            命令退出码。

        异常：
            RuntimeError: check=True 且退出码非 0。
        """
        wrapped = "bash -lc '" + cmd.replace("'", "'\\''") + "'"
        _, stdout, _ = self.ssh.exec_command(wrapped, get_pty=True)
        for line in iter(stdout.readline, ""):
            print(line, end="")
        code = stdout.channel.recv_exit_status()
        if check and code != 0:
            raise RuntimeError(f"远程命令失败（退出码 {code}）：{cmd}")
        return code

    def output(self, cmd: str) -> str:
        """执行命令并返回 stdout 文本，供读取标记或查询状态（不打印）。"""
        wrapped = "bash -lc '" + cmd.replace("'", "'\\''") + "'"
        _, stdout, _ = self.ssh.exec_command(wrapped)
        return stdout.read().decode("utf-8", "replace")

    # ---------- 远程文件基础操作 ----------
    def exists(self, path: str) -> bool:
        """判断远程路径是否存在。"""
        try:
            self.sftp.stat(path)
            return True
        except FileNotFoundError:
            return False

    def mkdirs(self, path: str) -> None:
        """递归创建远程目录，等价于 mkdir -p。"""
        if path in ("", "/"):
            return
        parent = posixpath.dirname(path)
        if parent != path:
            self.mkdirs(parent)
        if not self.exists(path):
            self.sftp.mkdir(path)

    def read_text(self, path: str) -> str:
        """读取远程文本文件内容。"""
        with self.sftp.open(path, "r") as f:
            return f.read().decode("utf-8", "replace")

    def write_text(self, path: str, text: str) -> None:
        """写远程文本文件，父目录不存在时自动创建。"""
        self.mkdirs(posixpath.dirname(path))
        with self.sftp.open(path, "w") as f:
            f.write(text)

    def put(self, local: str, remote: str) -> None:
        """增量上传单个文件：大小与 mtime 未变则跳过，上传后保留 mtime。

        参数：
            local: 本地文件路径。
            remote: 远程文件路径。
        """
        ls = os.stat(local)
        if self.exists(remote):
            rs = self.sftp.stat(remote)
            if rs.st_size == ls.st_size and int(rs.st_mtime) >= int(ls.st_mtime):
                return
        self.mkdirs(posixpath.dirname(remote))
        self.sftp.put(local, remote)
        self.sftp.utime(remote, (int(ls.st_atime), int(ls.st_mtime)))

    def _walk(self, remote_dir: str, exclude: list[str] | None = None):
        """递归遍历远程目录下的文件，剪掉命中排除模式的路径。

        参数：
            remote_dir: 远程目录。
            exclude: 排除模式列表，为 None 时不过滤。

        返回：
            生成器，逐项 yield (远程文件路径, SFTPAttributes)。
        """
        for attr in self.sftp.listdir_attr(remote_dir):
            if exclude and _excluded(attr.filename, exclude):
                continue
            rpath = posixpath.join(remote_dir, attr.filename)
            if stat.S_ISDIR(attr.st_mode):
                yield from self._walk(rpath, exclude)
            else:
                yield rpath, attr

    # ---------- 增量同步 ----------
    def upload_tree(self, local_dir: str, remote_dir: str, exclude: list[str]) -> tuple[int, int, dict]:
        """增量上传整个目录，只传新增或改动过的文件。

        参数：
            local_dir: 本地作业目录。
            remote_dir: 远程目标目录。
            exclude: 排除模式列表。

        返回：
            (上传数, 跳过数, snapshot)；snapshot 为 {相对路径: (大小, mtime)}，供下载时识别未变化的输入。
        """
        uploaded = skipped = 0
        snapshot: dict[str, tuple[int, int]] = {}
        self.mkdirs(remote_dir)
        for root, dirs, files in os.walk(local_dir):
            rel_root = os.path.relpath(root, local_dir).replace(os.sep, "/")
            # 剪掉排除目录，避免走进 __pycache__ / results 等
            dirs[:] = [d for d in dirs if not _excluded(d if rel_root == "." else rel_root + "/" + d, exclude)]
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), local_dir).replace(os.sep, "/")
                if _excluded(rel, exclude):
                    continue
                lp = os.path.join(root, name)
                rp = posixpath.join(remote_dir, rel)
                ls = os.stat(lp)
                snapshot[rel] = (ls.st_size, int(ls.st_mtime))
                if self.exists(rp):
                    rs = self.sftp.stat(rp)
                    if rs.st_size == ls.st_size and int(rs.st_mtime) >= int(ls.st_mtime):
                        skipped += 1
                        continue
                self.mkdirs(posixpath.dirname(rp))
                self.sftp.put(lp, rp)
                self.sftp.utime(rp, (int(ls.st_atime), int(ls.st_mtime)))
                uploaded += 1
        return uploaded, skipped, snapshot

    def download_tree(self, remote_dir: str, local_dir: str, snapshot: dict | None = None, exclude: list[str] | None = None) -> tuple[int, int]:
        """增量下载远程目录里新产生或已变动的文件，跳过未变化的输入。

        参数：
            remote_dir: 远程作业目录。
            local_dir: 本地结果目录。
            snapshot: 上传时记录的文件状态快照，命中且未变动则跳过。
            exclude: 排除模式列表，为 None 时不过滤。

        返回：
            (下载数, 跳过数)。
        """
        downloaded = skipped = 0
        for rp, attr in self._walk(remote_dir, exclude):
            rel = posixpath.relpath(rp, remote_dir)
            if exclude and _excluded(rel, exclude):
                continue
            # 与输入一致的文件说明没被计算改动，不必拉回
            if snapshot and rel in snapshot:
                size, mtime = snapshot[rel]
                if attr.st_size == size and int(attr.st_mtime) <= mtime:
                    skipped += 1
                    continue
            lp = os.path.join(local_dir, rel.replace("/", os.sep))
            if os.path.isfile(lp):
                lst = os.stat(lp)
                if lst.st_size == attr.st_size and int(lst.st_mtime) >= int(attr.st_mtime):
                    skipped += 1
                    continue
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            self.sftp.get(rp, lp)
            os.utime(lp, (int(attr.st_atime), int(attr.st_mtime)))
            downloaded += 1
        return downloaded, skipped

    def read_log(self, log: str, offset: int) -> tuple[str, int]:
        """从上次偏移处增量读取远程日志（SFTP seek，偏移按字节计）。

        参数：
            log: 远程日志文件路径。
            offset: 上次读到的字节偏移。

        返回：
            (新增文本, 新偏移)；日志尚未生成时返回 ("", offset)。
        """
        try:
            with self.sftp.open(log, "rb") as f:
                f.seek(offset)
                data = f.read()
        except FileNotFoundError:
            return "", offset
        return data.decode("utf-8", "replace"), offset + len(data)

    def alive(self, pidfile: str) -> bool:
        """根据 PID 文件判断作业是否仍在运行。参数 pidfile: 云端 PID 文件路径。"""
        path = shlex.quote(pidfile)
        out = self.output(f'if [ -f {path} ] && kill -0 "$(cat -- {path})" 2>/dev/null; then echo 1; else echo 0; fi')
        return out.strip() == "1"

    def close(self) -> None:
        """关闭 SFTP 与 SSH 连接。"""
        self.sftp.close()
        self.ssh.close()
