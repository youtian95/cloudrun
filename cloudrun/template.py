"""config.yaml 模板：cloudrun init 时写入用户工作目录。

功能：提供一份带注释的默认配置模板字符串。
使用流程：init 命令把 TEMPLATE 写成工作目录下的 config.yaml。
输入：无。
输出：TEMPLATE 字符串（YAML 文本）。
"""

TEMPLATE = """\
# cloudrun 配置（由 cloudrun init 生成）。填写 server 与 env.wheels 后即可使用。
# 本文件所在目录即"工作目录"，push/run 会同步这个目录。config.yaml 本身不会被上传。

# [二选一]
# 使用已经存在的云服务器
server:
  host: ""                    # 云服务器公网 IP
  port: 22
  user: "root"
  key_file: "~/.ssh/id_rsa"   # SSH 私钥（推荐）；不用密钥则填 password
  # password: ""

# [二选一]
# 自动使用云服务商权限开通新的服务器（每个服务商一段；enabled 为 true 时启用）
provider:
  # 使用阿里云，安装时需要 uv sync --extra aliyun
  aliyun:
    enabled: true
    # 凭证推荐用环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET
    access_key_id: ""
    access_key_secret: ""

    # [模版1 - 24核48G模版]
    # region_id: "cn-shanghai"
    # instance_type: "ecs.hfc7.6xlarge"   
    # [模版2 - 8核16G模版]
    # region_id: "cn-hangzhou"
    # instance_type: "ecs.c9i.2xlarge"   
    # [模版3 - 2核2G模版]
    region_id: "cn-hangzhou"
    instance_type: "ecs.e-c1m1.large"   

    image_id: "ubuntu_24_04_x64_20G_alibase_20260720.vhd"   # Ubuntu 24.04 64位 20G
    reuse: true                          # true=复用停机实例(默认)；false=每次新建用完释放
    internet_max_bandwidth_out: 5
    system_disk_category: "cloud_essd"  # cloud_essd / cloud_essd_entry
    system_disk_size: 40
    spot_strategy: "SpotAsPriceGo"       # 抢占式（默认，省钱，可能被回收）；普通按量设 NoSpot
    spot_duration: 1                     # 抢占保护期(小时)，期内不被回收；0=无保护
    spot_interruption_behavior: "Stop"   # 中断时停机保留(契合复用)；Terminate=释放
    # image_id / zone_id / key_pair_name / security_group_id / vswitch_id 留空则全自动创建

# 云端根目录与环境
remote:
  workdir: "/root/cloudrun"   # 云端根目录：项目镜像/venv/wheels 都放这下面
  python: "python3"           # 云端建 venv 的 Python（需含 venv 模块，版本尽量与本地一致）
  index_url: "https://mirrors.aliyun.com/pypi/simple/"   # pip 源
  setup_commands:           # 云端首次初始化命令，全部成功后同一工作根目录不再执行
    # Ubuntu 示例（使用 root 登录，其他用户按需加 sudo）：
    - apt-get update
    - apt-get install -y build-essential git curl python3-venv

# 环境同步：push 时把本地环境还原到云端
env:
  source: auto                # auto=自动检测 / uv=uv sync / requirements=指定文件 / freeze=冻结本地python
  requirements: ""            # source=requirements 时的 requirements.txt 路径
  freeze_python: ""           # source=freeze 时要冻结的本地 python/venv 解释器路径
  wheels: []                  # 自定义 wheel（uv sync 后强制覆盖，如你自己编译的 openseespy）
  # auto：工作目录有 pyproject.toml+uv.lock 就用 uv sync（还原锁定环境并安装项目）；
  #       否则回退 .venv/requirements.txt/pyproject 依赖检测

# 增量同步排除项
sync:
  exclude: ["__pycache__", "*.pyc", "*.egg-info", "build", "dist", ".git", ".venv", "venv", "wheels", "config.yaml", ".cloudrun", "*.db"]   # 不上传
  download_exclude: ["__pycache__", "*.pyc", "*.egg-info", "build", "dist", ".git", ".venv", "venv", ".cloudrun", ".cloudrun_run.*"]   # 不下载（切勿排除结果目录）

# 运行
run:
  command: "python run.py"    # 未指定脚本时的默认命令；run 脚本.py 会自动转 python -m 包.模块
  poll_interval: 5            # 运行日志轮询间隔（秒），结果在作业结束后下载
  detach: false               # true=启动后立即返回（不跟随）；false=实时跟随到算完

# 可选开关机钩子（本机执行的命令，如云 CLI；留空则手动）
power:
  on: ""
  off: ""
"""
