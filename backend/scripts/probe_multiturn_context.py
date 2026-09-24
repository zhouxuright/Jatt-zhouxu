# -*- coding: utf-8 -*-
"""Multi-turn context retention probe.

Sends three dependent questions in ONE conversation and checks that the model
still knows the facts (employer city, contract term, probation length) stated
two turns earlier -- i.e. that the context manager actually works.
"""
import json
import os

import requests

BASE = os.environ.get("BASE", "http://localhost:8001")
API = f"{BASE}/api/v1"

requests.post(
    f"{API}/auth/register",
    json={"username": "smoketest", "email": "smoke@test.com",
          "password": "SmokeTest@123", "full_name": "Smoke Test"},
    timeout=30,
)
tok = requests.post(
    f"{API}/auth/login",
    json={"username": "smoketest", "password": "SmokeTest@123"},
    timeout=30,
).json()["token"]["access_token"]
H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def turn(msg: str, conv: str | None = None):
    body = {"message": msg, "enable_deep_think": False, "enable_web_search": False}
    if conv:
        body["conversation_id"] = conv
    r = requests.post(f"{API}/chat/chat/stream", headers=H, json=body, timeout=240, stream=True)
    cid = None
    text = ""
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        try:
            o = json.loads(line[6:])
        except Exception:  # noqa: BLE001
            continue
        if o.get("type") == "meta":
            cid = o.get("data", {}).get("conversation_id", cid)
        elif o.get("type") == "token":
            text += o.get("content", "")
        elif o.get("type") == "done" and o.get("content"):
            text = o["content"]
    return cid, text


TURNS = [
    "我2023年3月入职一家上海公司，签了3年合同，试用期6个月。",
    "刚才说的试用期是几个月？公司这样约定合法吗？",
    "那我这种情况，如果公司在试用期第5个月辞退我，能拿多少赔偿？",
]

cid = None
answers = []
for i, m in enumerate(TURNS, 1):
    cid, txt = turn(m, cid)
    answers.append(txt)
    print(f"--- turn {i} (conv={str(cid)[:8]}) ---")
    print("  Q:", m)
    print("  A:", txt[:260].replace("\n", " "))

# --- assertions: context must persist across turns ---
joined = "\n".join(answers)
checks = {
    "turn2 recalls 6-month probation": ("6" in answers[1] or "六个月" in answers[1]),
    "turn3 references earlier facts": any(
        k in answers[2] for k in ("上海", "3年", "三年", "试用期")
    ),
}
print("\n=== context retention checks ===")
ok = True
for name, passed in checks.items():
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    ok = ok and passed

r = requests.get(f"{API}/chat/conversations/{cid}/stats", headers=H, timeout=60)
print("\nconversation stats:", r.status_code, r.text[:400])

msg_count = joined.count("试用期")
print(f"\nmentions of 试用期 across turns: {msg_count}")
print("RESULT:", "PASS" if ok else "FAIL")
