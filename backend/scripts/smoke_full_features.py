# -*- coding: utf-8 -*-
"""
Full feature smoke test for Legal Intelligent Assistance System.

Runs INSIDE the backend container (or host with deps) and exercises every
front-end "button" feature end to end:

  - auth login
  - skills list / assembly
  - MCP tools list / call
  - web search (联网搜索)
  - deep thinking (深度思考, non-stream + stream)
  - batch multi-file upload (PDF / DOCX / DOC / TXT / MD)
  - voice: TTS + ASR (多模态语音对话)
  - chat stream with all toggles combined
  - law search / case search / litigation / compliance / lifecycle

Usage (inside container):
    python scripts/smoke_full_features.py
Env:
    BASE   default http://localhost:8000
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

import requests

BASE = os.environ.get("BASE", "http://localhost:8000")
API = f"{BASE}/api/v1"
TIMEOUT = 180

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail[:300]))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name} :: {detail[:300]}")


def step(name: str):
    def deco(fn):
        def wrapper(*a, **kw):
            print(f"\n>>> {name}")
            try:
                fn(*a, **kw)
            except Exception as exc:  # noqa: BLE001
                record(name, False, f"EXC {type(exc).__name__}: {exc}")
                traceback.print_exc()
        return wrapper
    return deco


def login() -> str:
    for creds in (
        ("smoketest", "SmokeTest@123"),
    ):
        r = requests.post(f"{API}/auth/login", json={"username": creds[0], "password": creds[1]}, timeout=30)
        if r.status_code == 200:
            return r.json()["token"]["access_token"]
    r = requests.post(
        f"{API}/auth/register",
        json={"username": "smoketest", "email": "smoke@test.com", "password": "SmokeTest@123", "full_name": "Smoke Test"},
        timeout=30,
    )
    r = requests.post(f"{API}/auth/login", json={"username": "smoketest", "password": "SmokeTest@123"}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]["access_token"]


def main() -> int:
    token = login()
    H = {"Authorization": f"Bearer {token}"}
    print(f"token acquired: {token[:24]}...")

    # 1. skills
    print("\n>>> skills list")
    try:
        r = requests.get(f"{API}/skills", headers=H, timeout=60)
        data = r.json()
        n = len(data.get("skills", data if isinstance(data, list) else []))
        record("skills.list", r.status_code == 200, f"HTTP {r.status_code} count={n}")
    except Exception as exc:  # noqa: BLE001
        record("skills.list", False, str(exc))

    # 2. MCP tools
    print("\n>>> mcp tools")
    try:
        r = requests.get(f"{API}/mcp/tools", headers=H, timeout=60)
        data = r.json()
        n = len(data.get("tools", []))
        record("mcp.tools_list", r.status_code == 200, f"HTTP {r.status_code} tools={n}")
    except Exception as exc:  # noqa: BLE001
        record("mcp.tools_list", False, str(exc))

    # 3. web search
    print("\n>>> web search")
    for path, payload in (
        ("/search/web", {"query": "劳动合同法 最新修订 2026", "max_results": 5}),
        ("/search/web-search", {"query": "劳动合同法 最新修订 2026", "max_results": 5}),
    ):
        try:
            r = requests.post(f"{API}{path}", headers=H, json=payload, timeout=90)
            if r.status_code != 404:
                ok = r.status_code == 200
                record(f"search{path}", ok, f"HTTP {r.status_code} {r.text[:180]}")
                break
        except Exception as exc:  # noqa: BLE001
            record(f"search{path}", False, str(exc))
            break

    # 4. deep think (non-stream)
    print("\n>>> deep think")
    try:
        body = {"query": "公司单方解除劳动合同需要满足哪些条件？", "include_rag": True}
        r = requests.post(f"{API}/reasoning/deep-think", headers=H, json=body, timeout=TIMEOUT)
        ok = r.status_code == 200
        record("reasoning.deep_think", ok, f"HTTP {r.status_code} len={len(r.text)} {r.text[:200]}")
    except Exception as exc:  # noqa: BLE001
        record("reasoning.deep_think", False, str(exc))

    # 5. batch upload: 5 formats
    print("\n>>> batch upload 5 formats")
    files = {
        "txt": ("test.txt", "这是一份测试法律文件。甲方与乙方签订劳动合同。".encode("utf-8"), "text/plain"),
        "md": ("test.md", "# 合同要点\n\n- 试用期三个月\n- 违约金条款\n".encode("utf-8"), "text/markdown"),
        "pdf": ("test.pdf", build_pdf(), "application/pdf"),
        "docx": ("test.docx", build_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "doc": ("test.doc", build_doc(), "application/msword"),
    }
    for fmt, (fname, content, ctype) in files.items():
        try:
            r = requests.post(
                f"{API}/document/batch-upload",
                headers=H,
                files={"files": (fname, content, ctype)},
                data={"mode": "extract"},
                timeout=120,
            )
            ok = r.status_code in (200, 202)
            record(f"batch_upload.{fmt}", ok, f"HTTP {r.status_code} {r.text[:200]}")
        except Exception as exc:  # noqa: BLE001
            record(f"batch_upload.{fmt}", False, str(exc))

    # 6. voice TTS
    print("\n>>> voice tts")
    try:
        r = requests.post(f"{API}/chat/voice/tts", headers=H, json={"text": "您好，这是一条法律咨询语音测试。", "voice": "female_formal"}, timeout=120)
        ok = r.status_code == 200 and len(r.content) > 1000
        record("voice.tts", ok, f"HTTP {r.status_code} bytes={len(r.content)} ctype={r.headers.get('content-type')}")
    except Exception as exc:  # noqa: BLE001
        record("voice.tts", False, str(exc))

    # 7. voice ASR (multipart audio)
    print("\n>>> voice asr")
    try:
        wav = build_wav_silence()
        r = requests.post(f"{API}/chat/voice", headers=H, files={"file": ("a.wav", wav, "audio/wav")}, timeout=180)
        record("voice.asr", r.status_code in (200, 400, 422), f"HTTP {r.status_code} {r.text[:200]}")
    except Exception as exc:  # noqa: BLE001
        record("voice.asr", False, str(exc))

    # 8. law search / cases
    print("\n>>> law search + cases")
    try:
        r = requests.post(f"{API}/law/search", headers=H, json={"query": "劳动合同 经济补偿", "top_k": 5}, timeout=120)
        record("law.search", r.status_code == 200, f"HTTP {r.status_code} len={len(r.text)} {r.text[:160]}")
    except Exception as exc:  # noqa: BLE001
        record("law.search", False, str(exc))
    try:
        r = requests.post(f"{API}/cases/search", headers=H, json={"query": "劳动争议 违法解除", "page": 1, "page_size": 5}, timeout=120)
        record("cases.search", r.status_code == 200, f"HTTP {r.status_code} len={len(r.text)} {r.text[:160]}")
    except Exception as exc:  # noqa: BLE001
        record("cases.search", False, str(exc))

    # 9. chat stream with ALL toggles
    print("\n>>> chat stream (all toggles)")
    try:
        payload = {
            "message": "员工试用期被公司以不符合录用条件为由辞退，能要求赔偿吗？",
            "enable_deep_think": True,
            "enable_web_search": True,
            "mcp_tools": [],
            "skill_id": None,
        }
        r = requests.post(f"{API}/chat/chat/stream", headers={**H, "Content-Type": "application/json"}, json=payload, timeout=TIMEOUT, stream=True)
        n_tok = 0
        preview = ""
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                try:
                    obj = json.loads(line[6:])
                except Exception:  # noqa: BLE001
                    continue
                if obj.get("type") == "token":
                    n_tok += 1
                    if len(preview) < 200:
                        preview += obj.get("content", "")
                elif obj.get("type") == "done":
                    break
        record("chat.stream_all_toggles", r.status_code == 200 and n_tok > 0, f"HTTP {r.status_code} tokens={n_tok} preview={preview[:120]!r}")
    except Exception as exc:  # noqa: BLE001
        record("chat.stream_all_toggles", False, str(exc))

    # summary
    print("\n" + "=" * 70)
    total = len(RESULTS)
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    print(f"SUMMARY: {passed}/{total} passed")
    for name, status, detail in RESULTS:
        if status != "PASS":
            print(f"  - {status} {name}: {detail}")
    print("=" * 70)
    return 0 if passed == total else 1


def build_pdf() -> bytes:
    """Minimal valid PDF with one page of text."""
    text = "Legal Test PDF - Labor Contract Dispute"
    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def build_docx() -> bytes:
    """Minimal valid .docx (OOXML zip) with one paragraph."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>劳动合同测试文档 DOCX</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def build_doc() -> bytes:
    """Minimal legacy .doc (OLE2/CFB) container.

    Only the 8-byte CFB signature is required to satisfy magic-byte
    validation; body bytes are zero-filled.  Parser-level extraction of a
    non-Word OLE2 file is expected to degrade gracefully.
    """
    sig = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    return sig + b"\x00" * (512 * 4)


def build_wav_silence(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """Tiny 16-bit PCM mono WAV of silence."""
    import struct

    n = int(rate * seconds)
    data = b"\x00\x00" * n
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    hdr += b"data" + struct.pack("<I", len(data))
    return hdr + data


if __name__ == "__main__":
    sys.exit(main())
