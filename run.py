# -*- coding: utf-8 -*-
"""开发入口：python run.py [端口]（默认 8614）

**这个入口不经过登录网关**：明文 HTTP、免登录，任何能访问这个端口的人都能读写数据。
正式使用请运行「启动看板-带登录.bat」（auth_gate/run_gate.py：HTTPS + 登录 + 按项目/表授权）。
确实要本地调试，显式设置环境变量 KB_ALLOW_NOAUTH=1 再运行。
"""
import os
import sys

if os.environ.get("KB_ALLOW_NOAUTH") != "1":
    sys.exit("拒绝启动：run.py 会跳过登录网关（明文、免登录，谁都能改数据）。\n"
             "请改用「启动看板-带登录.bat」；本地调试必须显式设置 KB_ALLOW_NOAUTH=1。")

import uvicorn  # noqa: E402

if len(sys.argv) > 1:
    port = int(sys.argv[1])
else:
    port = 8614

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"牧马人线上多维表 → http://127.0.0.1:{port}  （KB_ALLOW_NOAUTH=1：无登录，仅本机调试）")
    uvicorn.run("backend.app:app", host="127.0.0.1", port=port, log_level="warning")
