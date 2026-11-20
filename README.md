# cloudrun

把本地计算放到自租云服务器上跑：增量同步项目与环境，边算边取回结果（**原位写回工作目录**）。

## 安装

```powershell
uv tool install cloudrun              # 全局命令（发布到 PyPI 后）
uv tool install "cloudrun[aliyun]"    # 含阿里云自动开机
```
从本地 wheel 安装：`uv tool install dist/cloudrun-0.2.0-py3-none-any.whl`（或 `pip install dist/…whl` 装进当前环境）。
开发模式（cloudrun 源码目录，可编辑安装）：`uv sync --extra aliyun`。

## 快速使用

```powershell
cd 你的项目目录
cloudrun init          # 生成 config.yaml
```

填写 `config.yaml`（二选一）：
- **手动服务器**：填 `server`（IP/用户/密钥）+ `env.wheels`（你的自定义 wheel）。
- **阿里云全自动**：`provider.aliyun.enabled: true`，凭证设环境变量 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`，`server.host` 留空（公网 IP 自动管理）。

**环境同步**：项目有 `pyproject.toml` + `uv.lock` 时，远程自动 `uv sync` 还原锁定环境并安装项目本身（你的自定义 wheel 随后强制覆盖）；非 uv 项目回退到依赖检测。

```powershell
cloudrun run -m single_building.ifc_ida_analysis   # 等同本地 python -m single_building.ifc_ida_analysis
cloudrun run root_script.py                        # 等同本地 python root_script.py
```

**参数原样转发给 python**——本地怎么跑，远程就怎么跑（把 `python` 换成 `cloudrun run` 即可）。新产生/变动的文件**原位写回工作目录**（程序写 `figures/x.pdf`，本地就出现在 `figures/x.pdf`），日志实时同步。下次换个脚本再 `run` 即可，同一个项目目录。

## 命令

| 命令 | 作用 |
|---|---|
| `init [目录] [--force]` | 生成 config.yaml 模板 |
| `push` | 同步项目 + 环境到云端（不运行） |
| `run [python 参数…]` | 同步并运行，参数原样转发给 python，实时取回（后台用 `run.detach`） |
| `fetch` | 增量拉回新/变动文件 |
| `logs [--tail N] [--follow]` | 看日志 |
| `status` | 查作业是否仍在运行 |
| `up` | 云服务商：自动创建/开机 + 分配公网 |
| `down` | 云服务商：停机不收费 |
| `destroy [--cleanup]` | 云服务商：释放实例（`--cleanup` 一并清网络/密钥） |

> 安装后直接用 `cloudrun ...`；开发模式下用 `uv run cloudrun ...`（或激活 `.venv` 后直接 `cloudrun`）。
