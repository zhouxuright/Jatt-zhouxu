"""合同审查链路回归验证（合同审查此前无任何自动化覆盖，故问题长期潜伏）。

覆盖的真实缺陷
--------------
用户上传《中华人民共和国国防动员法》（10,150 字，.docx）时，接口返回 **400**、
前端只弹一句"请求失败"。根因是 ``ContentSafetyFilter.check_input`` 里的长度上限
（10,000 字符，本是给聊天提问用的）被文档接口复用，超长被当成"内容不安全"拒绝；
且 400 不在前端拦截器的处理分支内，真实原因被吞掉。

本脚本断言：
1. **长文档不再被误拒** —— 生成 ~12,000 字的 .docx（正常合同结构，含 8 类风险条款），
   走异步接口必须能审完，并返回非空的结构化风险条目、非 0 评分、以及带「修改建议」
   的条目（修复前此步必 400）。
2. **真的超长时给可操作提示** —— 60,000 字返回 413 且 detail 说明长度与上限。
3. **同步接口仍可用**（短文本）。
4. **非法扩展名仍被拒**（400），安全防线未被放宽。

用法::
    docker exec legalintelligentassistancesystem-backend-5 \
        python scripts/verify_contract_review.py

    # 只跑快速用例（跳过分钟级的长文档审查）
    docker exec ... python scripts/verify_contract_review.py --skip-slow
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

BASE = os.environ.get("BASE", "http://localhost:8001")
API = f"{BASE}/api/v1"
USERNAME = "contract_verify"
PASSWORD = "Verify@12345"

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


def make_docx(paragraphs: list[str]) -> bytes:
    """用 python-docx 生成真正的 .docx（避免手工拼 zip 造成伪失败）。"""
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def login() -> str:
    requests.post(
        f"{API}/auth/register",
        json={"username": USERNAME, "email": f"{USERNAME}@example.com", "password": PASSWORD},
        timeout=60,
    )
    r = requests.post(
        f"{API}/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["token"]["access_token"]


def long_contract_paragraphs(target_chars: int) -> list[str]:
    """拼一份 >10,000 字的服务合同，且前 8,000 字内覆盖全部 8 类风险条款。

    注意：正文必须真实包含风险条款。此前用「100 段同一句话」来凑长度，结果 LLM
    的条款抽取会把 8 个风险类别全部判为「不存在」，risk_items 恒为空 —— 那是
    测试夹具的假象，不是链路缺陷。这里改为正常的合同结构：基本信息 + 8 类风险条款
    + 模板化补充条款（用于把总字数顶过旧的 10,000 上限）。
    """
    header = [
        "技术服务合同",
        "甲方：某某科技有限公司    乙方：某某信息技术服务有限公司",
        "鉴于甲方拟委托乙方提供信息系统技术服务，双方经充分协商，订立本合同。",
        "第一条 乙方应按照附件一的技术方案为甲方提供系统开发与运维服务。",
        "第二条 服务期限为三年，自双方签署之日起计算。",
        "第三条 合同总金额为人民币壹仟万元整。",
        "第四条 乙方应指派不少于十名工程师常驻甲方现场。",
        "第五条 甲方应提供必要的办公场地与设备。",
        "第六条 乙方交付的成果应通过甲方组织的验收。",
        "第七条 双方应各自指定项目负责人并保持沟通。",
        "第八条 本合同附件与正文具有同等效力。",
    ]
    risk_clauses = [
        "第九条 违约责任：任何一方违反本合同约定的，应当向守约方支付合同总额百分之二百的违约金；"
        "守约方因此遭受的损失超过违约金的，不足部分不再另行赔偿。",
        "第十条 知识产权：乙方在履行本合同过程中产生的全部知识产权，包括但不限于源代码、"
        "算法、文档及衍生成果，均无偿归甲方单独所有，乙方不得自行使用或授权第三方使用。",
        "第十一条 保密条款：双方对履行本合同过程中知悉的对方商业秘密、技术秘密及其他保密信息"
        "承担保密义务，保密期限为永久，不受本合同终止或解除的影响。",
        "第十二条 竞业限制：乙方及其项目人员在合同期内及合同终止后三年内，不得从事与本项目"
        "相同或类似的业务，亦不得为甲方的竞争对方提供同类服务。",
        "第十三条 管辖权：因本合同产生的或与本合同有关的一切争议，双方应提交甲方所在地的"
        "仲裁委员会仲裁，仲裁裁决为终局的，对双方均有约束力。",
        "第十四条 付款条款：甲方在项目验收合格并经内部审批通过后三百六十日内支付全部服务款项，"
        "付款前乙方应先行开具全额发票。",
        "第十五条 终止条款：甲方有权在任何时候以书面通知方式单方解除本合同而无需承担任何责任，"
        "乙方不得就此主张任何赔偿或补偿。",
        "第十六条 免责条款：甲方对乙方因履行本合同产生的任何间接损失、可得利益损失及数据丢失"
        "均不承担任何赔偿责任，即使甲方已被告知该等损失的可能性。",
    ]
    filler_unit = (
        "第十七条之补充约定（第{n}项）：双方确认，本合同的履行应遵守国家有关法律法规的规定，"
        "任何一方不得以格式条款方式免除自身法定责任、排除对方主要权利，"
        "与本合同有关的通知应以书面形式送达对方指定的联系地址。"
    )
    paras = header + risk_clauses
    total = sum(len(p) for p in paras)
    i = 1
    while total < target_chars:
        p = filler_unit.format(n=i)
        paras.append(p)
        total += len(p)
        i += 1
    return paras


def main(skip_slow: bool = False) -> int:
    print("=" * 72)
    print("合同审查链路回归验证")
    print("=" * 72)
    token = login()
    H = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # 用例 1：长文档（12,000 字）走异步接口 —— 修复前必 400
    # ------------------------------------------------------------------
    print("\n[用例 1] 长文档异步审查（~12,000 字 .docx）")
    if skip_slow:
        print("  [SKIP] --skip-slow")
    else:
        paras = long_contract_paragraphs(12000)
        text_len = sum(len(p) for p in paras)
        docx = make_docx(paras)
        print(f"  生成 .docx：{len(docx):,} 字节 / 正文 {text_len:,} 字")
        r = requests.post(
            f"{API}/contract/review-async", headers=H,
            files={"file": ("long_contract.docx", docx,
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            timeout=180,
        )
        check("长文档提交未被拒（202）", r.status_code == 202,
              f"status={r.status_code} body={r.text[:160]}")
        if r.status_code == 202:
            task_id = r.json()["task_id"]
            started = time.time()
            status, result = "pending", None
            while time.time() - started < 900:
                time.sleep(5)
                t = requests.get(f"{API}/tasks/{task_id}", headers=H, timeout=60).json()
                status = t.get("status")
                if status in ("completed", "failed"):
                    result = t
                    break
            print(f"  任务耗时 {time.time() - started:.0f}s，最终状态={status}")
            check("长文档审查完成", status == "completed",
                  (result or {}).get("error") or "")
            if status == "completed":
                res = (result or {}).get("result") or {}
                items = res.get("risk_items") or []
                check("返回结构化风险条目", bool(items),
                      f"risk_items={len(items)} score={res.get('risk_score')}")
                check("风险评分不为 0", (res.get("risk_score") or 0) > 0,
                      f"score={res.get('risk_score')}")
                with_sugg = [it for it in items if (it.get("suggestion") or "").strip()]
                check("风险条目含修改建议", bool(with_sugg),
                      f"带建议条数={len(with_sugg)}/{len(items)}")
                check("返回审查报告摘要", bool(res.get("summary")),
                      f"summary 长度={len(res.get('summary') or '')}")

    # ------------------------------------------------------------------
    # 用例 2：真正超长（60,000 字）→ 413 + 可操作提示
    # ------------------------------------------------------------------
    print("\n[用例 2] 超长文本（60,000 字）应返回 413 且提示可操作")
    r = requests.post(
        f"{API}/contract/review-async", headers=H,
        data={"contract_text": "合同条款" * 15000},
        timeout=120,
    )
    body = r.text
    check("超长返回 413", r.status_code == 413, f"status={r.status_code}")
    check("提示含实际长度与上限", ("过长" in body) and ("超过上限" in body),
          body[:160])
    check("不再伪装成‘内容不安全’", "未通过安全检查" not in body)

    # ------------------------------------------------------------------
    # 用例 3：同步接口对短文本仍可用
    # ------------------------------------------------------------------
    print("\n[用例 3] 短文本走同步接口（/contract/review）")
    short = "劳动合同\n第一条 用人单位应当与劳动者订立书面劳动合同。\n第二条 用人单位可以随时解除合同，无需任何理由。\n"
    r = requests.post(
        f"{API}/contract/review", headers=H,
        files={"file": ("short.txt", short.encode("utf-8"), "text/plain")},
        timeout=600,
    )
    check("同步审查返回 201", r.status_code == 201, f"status={r.status_code} {r.text[:120]}")
    if r.status_code == 201:
        d = r.json()
        check("同步响应含 overall_score", d.get("overall_score") is not None,
              f"overall_score={d.get('overall_score')}")

    # ------------------------------------------------------------------
    # 用例 4：非法扩展名仍被拒（安全防线未放宽）
    # ------------------------------------------------------------------
    print("\n[用例 4] 非法扩展名仍应被拒")
    r = requests.post(
        f"{API}/contract/review", headers=H,
        files={"file": ("evil.exe", b"MZ\x90\x00" + b"\x00" * 100, "application/octet-stream")},
        timeout=60,
    )
    check("非法扩展名返回 400", r.status_code == 400, f"status={r.status_code} {r.text[:120]}")

    print("\n" + "=" * 72)
    print(f"结果：{len(PASS)} 项通过，{len(FAIL)} 项失败")
    for name in FAIL:
        print(f"  [FAIL] {name}")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-slow", action="store_true", help="跳过分钟级的长文档审查用例")
    args = ap.parse_args()
    sys.exit(main(args.skip_slow))
