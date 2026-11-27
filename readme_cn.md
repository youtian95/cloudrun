# cloudrun

[English](README.md)

把本地计算放到自租云服务器上跑，增量同步项目与环境，实时查看日志，作业结束后自动取回结果。

| 云服务商 | 阿里云 | AWS | Azure | Google Cloud | 腾讯云 | 华为云 | DigitalOcean |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 实例自动管理 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

## 快速使用

1. 安装`cloudrun`

    ```powershell
    uv tool install cloudrun              # 全局命令（发布到 PyPI 后）
    ```

    标准安装包含阿里云 SDK。若要从源码可编辑安装，在仓库根目录运行 `uv tool install --editable .`。

1. 初始化项目目录，生成 `config_cloudrun.yaml`。

    ```powershell
    cd 你的项目目录
    cloudrun init          # 生成 config_cloudrun.yaml
    ```

1. 填写 `config_cloudrun.yaml`。

    以阿里云为例，凭证用环境变量：

      - `provider.aliyun.enabled: true`
      - 凭证设环境变量 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
        - 访问[RAM 控制台](https://ram.console.aliyun.com/overview) ，快速开始-创建程序用户-用于程序访问的超级用户-执行配置（默认的配置）
        - 下载`AccessKey ID`和`AccessKey Secret`的文件
        - 填入 `config_cloudrun.yaml` 中

1. 运行脚本

    ```powershell
    cloudrun run -m single_building.ifc_ida_analysis   # 等同本地 python -m single_building.ifc_ida_analysis
    ```


## 命令

| 命令 | 作用 |
|---|---|
| `init [目录] [--force]` | 生成 `config_cloudrun.yaml` 模板 |
| `size` | 按 `sync.exclude` 统计项目文件，单独计入 `env.wheels`，显示全量上传的文件数与大小，无需连接服务器 |
| `push` | 同步项目 + 环境到云端（不运行） |
| `run [python 参数…]` | 同步并运行，参数原样转发给 Python，实时跟随日志，作业结束后取回结果（后台用 `run.detach`） |
| `fetch` | 增量拉回新/变动文件 |
| `logs [--tail N] [--follow]` | 看日志 |
| `status` | 查作业是否仍在运行 |
| `up` | 云服务商：自动创建/开机 + 分配公网 |
| `down` | 云服务商：停机不收费 |
| `destroy [--cleanup]` | 云服务商：释放实例（`--cleanup` 一并清网络/密钥） |
