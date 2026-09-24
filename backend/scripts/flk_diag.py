# -*- coding: utf-8 -*-
"""诊断 FLK 下载失败：查看 checkpoint 状态 + 实际响应内容。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import httpx

CP = Path("/tmp/flk_download_checkpoint.json")
TEXT_DIR = Path("/tmp/flk_texts")

if CP.exists():
    cp = json.load(open(CP, encoding="utf-8"))
    done = cp.get("done", {})
    failed = cp.get("failed", {})
    print(f"checkpoint: done={len(done)} failed={len(failed)}")
    reasons: dict[str, int] = {}
    for info in failed.values():
        r = (info.get("reason") or "")[:40]
        reasons[r] = reasons.get(r, 0) + 1
    for r, n in sorted(reasons.items(), key=lambda x: -x[1])[:10]:
        print(f"  fail[{n:4d}] {r}")
else:
    print("no checkpoint file")

n_texts = len(list(TEXT_DIR.glob("*.json"))) if TEXT_DIR.exists() else 0
print(f"texts on disk: {n_texts}")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
}

if CP.exists():
    cp = json.load(open(CP, encoding="utf-8"))
    failed = list(cp.get("failed", {}).items())[:3]
    print("\n--- probing 3 failed bbbs ---")
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for bbbs, info in failed:
            r = client.get(
                "https://flk.npc.gov.cn/law-search/download/pc",
                params={"format": "docx", "bbbs": bbbs},
            )
            ct = r.headers.get("content-type", "?")
            body = r.text[:300].replace("\n", " ")
            print(f"bbbs={bbbs[:12]}... title={info['title'][:20]}")
            print(f"  status={r.status_code} content-type={ct}")
            print(f"  body[:300]={body}")
            print()
