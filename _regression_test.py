# -*- coding: utf-8 -*-
"""全功能回归验证：登录 → 法条查询（含新补录条文）→ 批量上传端到端 → 语音往返。

用法: python _regression_test.py
通过生产 nginx (localhost:80) 访问，验证整条链路。
"""
import io
import json
import sys
import time
import uuid
import urllib.request
import urllib.error

BASE = "http://localhost"
TOKEN = None

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def http(method, path, body=None, headers=None, timeout=60):
    url = BASE + path
    data = None
    hdrs = {"Accept": "application/json"}
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif isinstance(body, bytes):
        data = body
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_text(method, path, body=None, headers=None, timeout=60):
    st, raw = http(method, path, body, headers, timeout)
    return st, raw.decode("utf-8", errors="replace")


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def wait_healthy(max_wait=300):
    """Wait for backend containers to finish model warm-up."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            st, body = http_text("GET", "/health", timeout=15)
            if st == 200 and '"healthy"' in body:
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def main():
    global TOKEN

    # ---- 0. 等待后端预热完成（模型加载 ~90s） ----
    if not wait_healthy():
        check("等待后端 healthy", False, "超时 300s")
        return
    check("等待后端 healthy", True)

    # ---- 1. 基础连通性 ----
    st, body = http_text("GET", "/", timeout=15)
    check("nginx 首页 (localhost:80)", st == 200, f"HTTP {st}")

    st, body = http_text("GET", "/health", timeout=15)
    check("后端健康检查 (/health)", st == 200 and '"healthy"' in body, f"HTTP {st}")

    # ---- 2. 登录（注册测试账号兜底） ----
    username = f"regress_{uuid.uuid4().hex[:8]}"
    email = f"{username}@test.local"
    st, body = http("POST", "/api/v1/auth/register", {
        "username": username, "email": email, "password": "Regression#2026",
    })
    if st != 201:
        check("注册测试账号", False, f"HTTP {st}: {body[:120]}")
        return
    check("注册测试账号", True)

    st, body = http("POST", "/api/v1/auth/login", {
        "username": username, "password": "Regression#2026",
    })
    if st != 200:
        check("登录获取 token", False, f"HTTP {st}: {body[:120]}")
        return
    data = json.loads(body)
    TOKEN = (data.get("token") or data).get("access_token")
    check("登录获取 token", True)

    # ---- 3. 法条查询：新补录的 3 条 + 既有条文 ----
    for q, expect in [
        ("民法典第三百四十条 土地经营权", "土地经营权人有权在合同约定的期限内占有农村土地"),
        ("民法典第342条 招标拍卖公开协商承包农村土地", "通过招标、拍卖、公开协商等方式承包农村土地"),
        ("民法典第九百六十六条 中介合同", "本章没有规定的，参照适用委托合同的有关规定"),
        ("民法典第1079条 诉讼离婚", "夫妻一方要求离婚"),
    ]:
        st, body = http_text("POST", "/api/v1/law/search", {"query": q, "top_k": 5},
                        headers=auth_headers(), timeout=120)
        ok = st == 200 and expect in body
        detail = f"HTTP {st}"
        if st == 200:
            data = json.loads(body)
            detail = f"HTTP {st}, {data.get('total', 0)} 条结果"
        check(f"法条查询: {q[:22]}", ok, detail)

    # ---- 4. 批量上传端到端 ----
    files = {
        "劳动合同法条款.txt": ("text/plain",
            "《中华人民共和国劳动合同法》第十条：建立劳动关系，应当订立书面劳动合同。\n"
            "第八十二条：用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，"
            "应当向劳动者每月支付二倍的工资。"),
        "违约情形.md": ("text/markdown",
            "# 合同违约情形\n\n## 根本违约\n当事人一方迟延履行债务或者有其他违约行为"
            "致使不能实现合同目的，对方可以解除合同。\n\n## 违约金\n约定的违约金低于造成的损失的，"
            "人民法院可以予以增加。"),
        "民间借贷要点.txt": ("text/plain",
            "民间借贷利率司法保护上限为一年期贷款市场报价利率（LPR）的四倍。"
            "借款合同对支付利息没有约定的，视为没有利息。"),
    }
    boundary = "----regression" + uuid.uuid4().hex
    parts = []
    for fname, (ctype, content) in files.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"files\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode("utf-8") + content.encode("utf-8") + b"\r\n"
        )
    payload = b"".join(parts) + f"--{boundary}--\r\n".encode("utf-8")
    st, body = http_text("POST", "/api/v1/document/batch-upload", payload, headers={
        **auth_headers(),
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }, timeout=300)
    # 202 Accepted is the designed response for async batch processing;
    # 200 would be a synchronous (legacy) response. Both mean "accepted".
    batch_ok = st in (200, 202)
    batch_id = None
    if batch_ok:
        data = json.loads(body)
        batch_id = data.get("batch_id")
        detail = f"HTTP {st}, batch_id={batch_id}, total_files={data.get('total_files')}"
    else:
        detail = f"HTTP {st}: {body[:200]}"
    check("批量上传 3 个文件 (中文文件名)", batch_ok, detail)

    # 轮询批量处理结果
    if batch_id:
        final = None
        for _ in range(45):
            time.sleep(4)
            st, body = http_text("GET", f"/api/v1/document/batch-status/{batch_id}",
                            headers=auth_headers(), timeout=30)
            if st == 200:
                data = json.loads(body)
                status = data.get("status")
                if status in ("completed", "failed"):
                    final = data
                    break
        if final:
            n_done = sum(1 for f in final.get("results", [])
                         if f.get("status") == "completed")
            n_summary = sum(1 for f in final.get("results", [])
                            if f.get("summary"))
            check("批量解析完成（含摘要/要点）", n_done >= 2 and n_summary >= 2,
                  f"{n_done}/3 完成, {n_summary}/3 含摘要, status={final.get('status')}, "
                  f"failed={final.get('failed_files')}")
        else:
            check("批量解析完成（含摘要/要点）", False, "轮询超时未到终态")

    # ---- 5. 语音服务配置（TTS 引擎与预设） ----
    st, body = http_text("GET", "/api/v1/chat/voice/presets", headers=auth_headers(), timeout=30)
    check("语音 TTS 配置接口", st == 200, f"HTTP {st}" + (f", {body[:80]}" if st == 200 else f": {body[:120]}"))

    # ---- 6. 语音往返：edge-tts 合成 → Whisper 转写 ----
    tts_text = "根据民法典第三百四十条，土地经营权人有权自主开展农业生产经营并取得收益。"
    st, audio = http("POST", "/api/v1/chat/voice/tts",
                     {"text": tts_text, "preset": "formal_female"},
                     headers=auth_headers(), timeout=120)
    if st != 200 or len(audio) < 1000:
        check("TTS 合成语音 (edge-tts)", False, f"HTTP {st}, {len(audio)} bytes")
    else:
        check("TTS 合成语音 (edge-tts)", True, f"{len(audio)} bytes MP3")

        # Upload the synthesized MP3 to the STT endpoint (SSE stream response)
        boundary = "----voice" + uuid.uuid4().hex
        mp3_part = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="tts_roundtrip.mp3"\r\n'
            f"Content-Type: audio/mpeg\r\n\r\n"
        ).encode("utf-8")
        payload = mp3_part + audio + f"\r\n--{boundary}--\r\n".encode("utf-8")
        st, sse = http_text("POST", "/api/v1/chat/voice", payload, headers={
            **auth_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "text/event-stream",
        }, timeout=300)
        if st != 200:
            check("Whisper 语音转写往返", False, f"HTTP {st}: {sse[:200]}")
        else:
            transcribed = ""
            for line in sse.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[5:].strip())
                except (ValueError, json.JSONDecodeError):
                    continue
                # meta event: {"type": "meta", "data": {"transcribed_text": ...}}
                if isinstance(evt, dict) and evt.get("type") == "meta":
                    transcribed = (evt.get("data") or {}).get("transcribed_text", "")
                    if transcribed:
                        break
            # Whisper may transcribe numerals differently (第三百四十条 vs
            # 第340条); require a meaningful overlap with the source text.
            key_chars = [c for c in "土地经营权自主开展农业生产经营" if c in transcribed]
            ok = len(transcribed) >= 10 and len(key_chars) >= 6
            check("Whisper 语音转写往返", ok,
                  f"转写: {transcribed[:60]}" if transcribed else "SSE 中未找到 transcribed_text")

    # ---- 汇总 ----
    passed = sum(1 for _, ok, _ in results if ok)
    print("\n" + "=" * 50)
    print(f"回归结果: {passed}/{len(results)} 通过")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
