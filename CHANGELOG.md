# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 格式，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-11-20

- 忽略 egg-info
- 修改模版
- 随时读取日志回本地
- 修复bug，现在测试可以运行了

## [0.2.1] - 2026-11-20

- 阿里云改成抢占式

## [0.2.0] - 2026-11-20

### 新增

- 可安装包：`pyproject.toml` + uv 管理开发环境，提供 `cloudrun` 命令行入口。
- `init`：在工作目录生成 `config.yaml` 模板。
- `push`：增量同步项目文件与本地环境到云端。
- `run`：参数原样转发给 python（本地怎么跑远程就怎么跑），支持免 `--` 透传（如 `cloudrun run -m 包.模块`）。
- uv 环境还原：远程自动装 uv，`uv sync` 按 `uv.lock` 还原锁定依赖并安装项目本身，自定义 wheel 强制覆盖；按哈希缓存复用。
- 边算边取回：实时日志 + 周期增量取回新文件 + 进程存活监控；算完在关机前显式最终取回。
- 阿里云服务商插件：自动创建 VPC/交换机/安全组/密钥对/实例、分配公网、开机/停机不收费/释放；默认复用停机实例。
- 阿里云默认抢占式实例（`SpotAsPriceGo`），含保护期 `spot_duration` 与中断行为 `spot_interruption_behavior`。
- 服务商插件架构 `providers/` + `get_provider()` 按配置懒加载分发，新增服务商无需改核心。
- `sync.exclude` / `sync.download_exclude` 可在配置中自定义。
- GitHub Actions 可信发布（OIDC）workflow。
- 开发者模块说明与打包发布文档。

### 变更

- 结果改为**原位写回工作目录**（程序写哪儿本地就出现在哪儿）。
- 远程改为固定项目目录（本地工作目录的镜像）。
- 环境检测与安装合并到单一 `env.py`。
- 阿里云配置归入 `provider.aliyun` 段。
- 增量同步清单更名为文件状态快照 `snapshot.json`。
- 阿里云系统盘默认改为 `cloud_essd_entry`（更省）。
- 模板列出热门多核规格（c7/g7 系列）供实际计算选用。

### 移除

- `job` 作业概念（固定项目目录后不再需要）。
- 固定 `results/` 结果目录概念（改原位写回）。
- `run` 的 `--command` / `--detach` 选项（后台用 `run.detach` 配置或 Ctrl-C 断开）。
- `--config` 选项（固定使用当前目录 `config.yaml`）。
- 文件路径自动转 `-m` 的猜测逻辑（改为参数原样转发）。

## [0.1.0] - 2026-11-19

### 新增

- 初始原型：基于 paramiko 的增量上传/下载、SSH 远程运行、结果拉回；阿里云自动供应雏形。
