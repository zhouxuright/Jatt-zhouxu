# -*- coding: utf-8 -*-
"""Full enumData dump + search/list with codeId-based payloads."""
import json

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}


def flatten(node, depth=0, out=None):
    if out is None:
        out = []
    out.append((depth, node.get("codeId"), node.get("name"), len(node.get("children") or [])))
    for ch in node.get("children") or []:
        flatten(ch, depth + 1, out)
    return out


def main() -> None:
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        r = client.get("https://flk.npc.gov.cn/law-search/search/enumData")
        data = r.json()["data"]
        print("enum keys:", list(data.keys()))
        # flfgfl = law categories
        root = data.get("flfgfl")
        if root:
            print("\nCategory tree (codeId | name | children):")
            for depth, code_id, name, n_children in flatten(root):
                print("  " * depth + f"{code_id} | {name} | {n_children}")

        # Try search/list with codeId
        payloads = [
            {"searchWord": "", "page": 1, "pageSize": 10, "flfgfl": [200]},
            {"searchWord": "", "page": 1, "pageSize": 10, "flfgflList": [200]},
            {"searchWord": "", "page": 1, "pageSize": 10, "codeIdList": [200]},
            {"searchWord": "", "page": 1, "pageSize": 10, "categoryCode": 200},
            {"searchWord": "", "page": 1, "pageSize": 10, "type": "flfg", "flfgfl": 200},
            {"keyword": "", "pageNum": 1, "pageSize": 10},
        ]
        for i, payload in enumerate(payloads):
            try:
                rr = client.post("https://flk.npc.gov.cn/law-search/search/list", json=payload)
                ok = rr.status_code == 200 and rr.text[:1] in "{["
                print(f"\npayload#{i} {json.dumps(payload, ensure_ascii=False)[:80]}")
                print(f"  -> {rr.status_code}: {rr.text[:300]}")
                if ok:
                    j = rr.json()
                    if j.get("code") == 200:
                        print("  *** WORKS ***")
                        return
            except Exception as exc:
                print(f"payload#{i} error: {str(exc)[:120]}")


if __name__ == "__main__":
    main()
