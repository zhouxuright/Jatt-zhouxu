# -*- coding: utf-8 -*-
"""校验容器内后端代码与本地是否一致：输出关键文件 md5。"""
import hashlib
import sys
from pathlib import Path

FILES = [
    "/app/app/api/v1/lifecycle.py",
    "/app/app/api/v1/compliance.py",
    "/app/app/api/v1/router.py",
    "/app/app/models/contract.py",
    "/app/app/models/compliance.py",
    "/app/app/models/__init__.py",
    "/app/app/services/regulation_monitor.py",
    "/app/app/agents/contract_lifecycle_agent.py",
    "/app/app/core/config.py",
    "/app/prompts/legal_prompts.py",
]

for f in FILES:
    p = Path(f)
    if not p.exists():
        print(f"MISSING {f}")
        continue
    h = hashlib.md5(p.read_bytes()).hexdigest()[:12]
    print(f"{h} {f}")
