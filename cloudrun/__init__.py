"""cloudrun：把本地 Python 计算提交到自租云服务器执行的轻量工具。

功能：init 生成配置；push 增量同步"建模代码 + 地震波 + 本地环境"到云端；
      run 自动 push 并运行，实时跟随日志，作业退出后下载结果；fetch 手动拉回结果。
      云端除 Python 与你的依赖外不装其它东西。
使用流程：cloudrun init -> 填 config.yaml -> cloudrun run。
输入：config.yaml（服务器/环境/运行配置）+ 本地工作目录。
输出：实时运行日志 + 本地 results/ 下的结果文件。
"""

__version__ = "0.2.0"
