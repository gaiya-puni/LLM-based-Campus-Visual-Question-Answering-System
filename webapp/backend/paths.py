"""统一的路径与环境引导：**必须在其它模块之前导入**。

拆分前这些计算散落在 `server.py` 顶部。代码一旦搬进 `api/` 子包，`__file__` 的相对层级
就变了（`parents[N]` 会差一级，`_BASE` 会指向 `api/`），所以集中到这里定义，
保证 `BASE` 恒等于 `webapp/backend`、`PROJECT_ROOT` 恒等于仓库根目录。

**顺序敏感**：`DB_CONFIG`、`_LLM_PROXY` 等在模块导入期就会读环境变量，因此 `.env`
必须在这里（也就是任何业务模块被导入之前）载入，否则密钥与数据库配置会静默为空。
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

#: webapp/backend
BASE = os.path.dirname(os.path.abspath(__file__))
#: 仓库根目录（tools/ 与 data/ 所在）
PROJECT_ROOT = str(Path(BASE).resolve().parents[1])

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(BASE, '..', '..', '.env'))
load_dotenv(os.path.join(BASE, '.env'))

# 保留拆分前的私有名字，让从 server.py 原样搬来的代码零改动。
_BASE = BASE
_PROJECT_ROOT = PROJECT_ROOT
