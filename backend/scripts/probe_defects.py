# -*- coding: utf-8 -*-
"""Targeted defect probes: upload MIME handling + web search engine egress."""
import json
import os

import requests

BASE = os.environ.get("BASE", "http://localhost:8001")
API = f"{BASE}/api/v1"


def login():
    requests.post(f"{API}/auth/register", json={
        "username": "smoketest", "email": "smoke@test.com",
        "password": "SmokeTest@123", "full_name": "Smoke Test"}, timeout=30)
    r = requests.post(f"{API}/auth/login", json={"username": "smoketest", "password": "SmokeTest@123"}, timeout=30)
    return r.json()["token"]["access_token"]


def main():
    tok = login()
    H = {"Authorization": f"Bearer {tok}"}
    md = "# 合同要点\n\n- 试用期\n".encode()
    docx_sig = b"PK\x03\x04" + b"\x00" * 200

    cases = [
        ("md / text/markdown", "a.md", md, "text/markdown"),
        ("md / text/plain", "a.md", md, "text/plain"),
        ("md / text/x-markdown", "a.md", md, "text/x-markdown"),
        ("md / empty ctype", "a.md", md, ""),
        ("md / octet-stream", "a.md", md, "application/octet-stream"),
        ("docx / correct", "a.docx", docx_sig, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("docx / octet-stream", "a.docx", docx_sig, "application/octet-stream"),
        ("txt / octet-stream", "a.txt", b"hello", "application/octet-stream"),
        ("doc / octet-stream", "a.doc", b"\xd0\xcf\x11\xe0" + b"\x00" * 200, "application/octet-stream"),
    ]
    print("=" * 64)
    print("UPLOAD MIME MATRIX")
    print("=" * 64)
    for label, name, content, ctype in cases:
        files = {"files": (name, content, ctype or None)}
        try:
            r = requests.post(f"{API}/document/batch-upload", headers=H, files=files,
                              data={"mode": "extract"}, timeout=60)
            detail = ""
            if r.status_code >= 400:
                detail = r.text[:150]
            print(f"{'OK ' if r.status_code in (200,202) else 'ERR'} {r.status_code}  {label:26s} {detail}")
        except Exception as e:
            print(f"EXC ---  {label:26s} {e}")

    # web search engines reachable?
    print()
    print("=" * 64)
    print("WEB SEARCH PROBE")
    print("=" * 64)
    payload = {"query": "劳动合同法", "max_results": 3}
    r = requests.post(f"{API}/search/web", headers=H, json=payload, timeout=60)
    print("api /search/web ->", r.status_code, r.text[:300])

    # direct serper egress test
    try:
        import app.core.config as _c  # noqa
    except Exception:
        pass
    print("\n--- direct serper egress ---")
    try:
        rr = requests.post("https://google.serper.dev/search",
                           headers={"X-API-KEY": os.environ.get("SERPER_API_KEY", ""),
                                    "Content-Type": "application/json"},
                           json={"q": "劳动合同法"}, timeout=25)
        print("serper status:", rr.status_code, rr.text[:200])
    except Exception as e:
        print("serper EXC:", type(e).__name__, e)

    print("\n--- direct baidu scrape ---")
    try:
        rr = requests.get("https://www.baidu.com/s?wd=test", timeout=20,
                          headers={"User-Agent": "Mozilla/5.0"})
        print("baidu status:", rr.status_code, "len", len(rr.text))
    except Exception as e:
        print("baidu EXC:", type(e).__name__, e)

    print("\n--- generic egress (example.com) ---")
    try:
        rr = requests.get("https://example.com", timeout=20)
        print("example status:", rr.status_code)
    except Exception as e:
        print("example EXC:", type(e).__name__, e)


if __name__ == "__main__":
    main()
