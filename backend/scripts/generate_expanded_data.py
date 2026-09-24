"""从现有 laws.json 生成扩展法律知识数据。

生成三类数据：
1. 法律知识 Q&A 对（用于 RAG 检索）
2. 法律知识条目（法律元信息 + 章节结构）
3. 法律交叉引用关系

执行: python scripts/generate_expanded_data.py
"""

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data" / "expanded_datasets"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LAWS_JSON_PATH = PROJECT_ROOT / "data" / "datasets" / "laws.json"


def generate_qa_pairs(laws: list[dict]) -> list[dict]:
    """从法律条文生成 Q&A 对。"""
    qa_pairs = []

    for law in laws:
        title = law.get("title", "")
        content = law.get("content", "")
        law_type = law.get("type", "")

        if not content or len(content) < 50:
            continue

        short_law = title.replace("中华人民共和国", "")
        parts = re.split(r'(第[一二三四五六七八九十百零千\d]+条[ \s])', content)

        if len(parts) <= 1:
            qa_pairs.append({
                "source": "laws_json_qa",
                "question": f"{title}的主要内容是什么？",
                "answer": content[:500],
                "law_name": title,
                "law_type": law_type,
                "category": "法律知识",
            })
            continue

        for i in range(1, min(len(parts) - 1, 200), 2):
            article_num = parts[i].strip()
            raw_content = parts[i + 1] if i + 1 < len(parts) else ""
            cleaned = re.sub(r'\s+', '', raw_content).strip()
            if not cleaned or len(cleaned) < 10:
                continue

            qa_pairs.append({
                "source": "laws_json_qa",
                "question": f"{short_law}{article_num}规定了什么内容？",
                "answer": f"{title}{article_num}规定：{cleaned[:400]}",
                "law_name": title,
                "article_number": article_num,
                "law_type": law_type,
                "content": cleaned,
                "category": "法律知识",
            })

    return qa_pairs


def generate_knowledge_entries(laws: list[dict]) -> list[dict]:
    """生成法律知识条目（元信息 + 章节结构）。"""
    entries = []

    for law in laws:
        title = law.get("title", "")
        content = law.get("content", "")
        law_type = law.get("type", "")
        status = law.get("status", "")
        office = law.get("office", "")

        if not content:
            continue

        entries.append({
            "source": "law_metadata",
            "type": "law_info",
            "content": f"{title}，由{office}颁布，状态：{status}，类型：{law_type}。",
            "law_name": title,
            "law_type": law_type,
            "category": "法律信息",
            "metadata": {
                "office": office,
                "status": status,
                "publish": law.get("publish", ""),
            },
        })

        chapters = re.findall(r'第[一二三四五六七八九十百零]+[编章节][ \s][^\n]+', content)
        for ch in chapters:
            entries.append({
                "source": "law_chapters",
                "type": "chapter",
                "content": f"{title} {ch.strip()}",
                "law_name": title,
                "category": "法律结构",
            })

    return entries


def generate_cross_references(laws: list[dict]) -> list[dict]:
    """生成法律交叉引用关系。"""
    refs = []

    for law in laws:
        title = law.get("title", "")
        content = law.get("content", "")
        if not content or len(content) < 100:
            continue

        cited = re.findall(r'《([^》]+)》', content)
        unique_cited = list(set(cited))[:5]

        if unique_cited:
            refs.append({
                "source": "law_cross_refs",
                "type": "cross_reference",
                "content": f"{title}引用了以下法律法规：" + "、".join(unique_cited) + "。",
                "law_name": title,
                "referenced_laws": unique_cited,
                "category": "法律关联",
            })

    return refs


def main():
    print("Loading laws.json...")
    with open(LAWS_JSON_PATH, "r", encoding="utf-8") as f:
        laws = json.load(f)
    print(f"Loaded {len(laws):,} laws")

    # 1. Q&A pairs
    print("\nGenerating Q&A pairs...")
    qa_pairs = generate_qa_pairs(laws)
    qa_path = DATA_DIR / "laws_qa_pairs.jsonl"
    with open(qa_path, "w", encoding="utf-8") as f:
        for qa in qa_pairs:
            f.write(json.dumps(qa, ensure_ascii=False) + "\n")
    print(f"  {len(qa_pairs):,} Q&A pairs -> {qa_path.name} ({os.path.getsize(qa_path)/1024/1024:.1f} MB)")

    # 2. Knowledge entries
    print("\nGenerating knowledge entries...")
    ke = generate_knowledge_entries(laws)
    ke_path = DATA_DIR / "law_knowledge_entries.jsonl"
    with open(ke_path, "w", encoding="utf-8") as f:
        for entry in ke:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"  {len(ke):,} entries -> {ke_path.name} ({os.path.getsize(ke_path)/1024/1024:.1f} MB)")

    # 3. Cross references
    print("\nGenerating cross-references...")
    cr = generate_cross_references(laws)
    cr_path = DATA_DIR / "law_cross_references.jsonl"
    with open(cr_path, "w", encoding="utf-8") as f:
        for ref in cr:
            f.write(json.dumps(ref, ensure_ascii=False) + "\n")
    print(f"  {len(cr):,} references -> {cr_path.name} ({os.path.getsize(cr_path)/1024:.1f} MB)")

    # Summary
    total_new = len(qa_pairs) + len(ke) + len(cr)
    existing = 665841
    grand = existing + total_new

    print(f"\n{'='*60}")
    print(f"数据扩展统计")
    print(f"{'='*60}")
    print(f"  现有法律条文:          {existing:>12,}")
    print(f"  Q&A 对:               {len(qa_pairs):>12,}")
    print(f"  知识条目:              {len(ke):>12,}")
    print(f"  交叉引用:              {len(cr):>12,}")
    print(f"  新数据合计:            {total_new:>12,}")
    print(f"  总数据量:              {grand:>12,}")
    print(f"  约 {grand/10000:.0f} 万条")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
