"""裁判文书数据清洗和结构化处理器。

将爬取的原始裁判文书数据进行：
1. 文本清洗（去除HTML标签、特殊字符）
2. 信息提取（案号、法院、案由、当事人、判决结果等）
3. 关键词提取和分词
4. 向量嵌入（用于RAG检索）
5. 结构化入库（PostgreSQL + Milvus）
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import jieba
import jieba.analyse

from app.core.database import async_session_factory
from app.models.legal_knowledge import CourtCase
from app.services.model_registry import ModelRegistry

logger = logging.getLogger(__name__)


# =============================================================================
# 文本清洗
# =============================================================================

class TextCleaner:
    """文本清洗器"""

    @staticmethod
    def clean_html(text: str) -> str:
        """去除HTML标签"""
        # 去除HTML标签
        text = re.sub(r"<[^>]+>", "", text)
        # 去除HTML实体
        text = re.sub(r"&[a-zA-Z]+;", "", text)
        # 去除多余空白
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def clean_special_chars(text: str) -> str:
        """去除特殊字符"""
        # 保留中文、英文、数字、基本标点（含全角引号，注意用全角引号避免提前
        # 终止字符串字面量——原写法 r"...""''..." 已被 Python 解析为拼接，
        # 导致 \s 成为非法转义并丢失引号）
        text = re.sub(
            r"[^一-龥a-zA-Z0-9，。；：！？、“”‘’（）《》\s]",
            "", text, flags=re.UNICODE,
        )
        return text.strip()

    @staticmethod
    def normalize_whitespace(text: str) -> str:
        """规范化空白字符"""
        # 多个空格合并为一个
        text = re.sub(r" +", " ", text)
        # 多个换行合并为两个
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


# =============================================================================
# 关键词提取
# =============================================================================

class KeywordExtractor:
    """关键词提取器"""

    # 法律领域关键词
    LEGAL_KEYWORDS = {
        "劳动法": ["劳动合同", "劳动争议", "经济补偿", "工伤", "加班", "工资", "解雇"],
        "婚姻家庭": ["离婚", "财产分割", "抚养权", "赡养", "继承", "婚姻"],
        "合同法": ["合同违约", "合同纠纷", "买卖合同", "租赁合同", "借款合同"],
        "刑法": ["盗窃", "诈骗", "故意伤害", "抢劫", "贪污", "受贿", "故意杀人"],
        "侵权": ["侵权", "人身损害", "精神损害", "高空抛物", "赔偿"],
        "消费者权益": ["消费者", "欺诈", "退货", "赔偿", "产品质量"],
        "知识产权": ["著作权", "专利", "商标", "商业秘密", "侵权"],
        "房产": ["房屋买卖", "租赁", "物业", "拆迁", "产权"],
        "公司法": ["股权", "股东", "公司治理", "破产", "清算"],
    }

    @classmethod
    def extract_keywords(cls, text: str, top_k: int = 10) -> list[str]:
        """提取关键词"""
        # 使用 TF-IDF 提取关键词
        keywords = jieba.analyse.extract_tags(text, topK=top_k, withWeight=False)
        return keywords

    @classmethod
    def extract_legal_keywords(cls, text: str) -> list[str]:
        """提取法律领域关键词"""
        found_keywords = []
        for category, keywords in cls.LEGAL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    found_keywords.append(keyword)
        return list(set(found_keywords))

    @classmethod
    def generate_tags(cls, text: str, cause: str = "") -> str:
        """生成标签"""
        tags = []

        # 从案由生成标签
        if cause:
            tags.append(cause)
            # 根据案由添加相关标签
            for category, keywords in cls.LEGAL_KEYWORDS.items():
                if any(kw in cause for kw in keywords):
                    tags.append(category)

        # 从文本中提取法律关键词
        legal_keywords = cls.extract_legal_keywords(text)
        tags.extend(legal_keywords)

        # 去重
        tags = list(set(tags))
        return ",".join(tags[:10])  # 最多10个标签


# =============================================================================
# 向量嵌入
# =============================================================================

class DocumentEmbedder:
    """文档向量嵌入器"""

    def __init__(self):
        self.model = ModelRegistry.get_embedding_model()

    def embed_text(self, text: str) -> list[float]:
        """将文本转换为向量"""
        # 截取前 2000 字（避免超出模型限制）
        text = text[:2000] if len(text) > 2000 else text

        # 生成嵌入
        output = self.model.encode([text], return_dense=True)
        return output["dense_vecs"][0].tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入"""
        # 截取每个文本
        texts = [t[:2000] if len(t) > 2000 else t for t in texts]

        # 批量生成嵌入
        output = self.model.encode(texts, return_dense=True)
        return [e.tolist() for e in output["dense_vecs"]]


# =============================================================================
# 数据处理器
# =============================================================================

class DocumentProcessor:
    """裁判文书数据处理器"""

    def __init__(self):
        self.cleaner = TextCleaner()
        self.keyword_extractor = KeywordExtractor()
        self.embedder = DocumentEmbedder()

    def process_document(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """处理单个文书"""
        # 1. 文本清洗
        full_text = raw_data.get("full_text", "")
        full_text = self.cleaner.clean_html(full_text)
        full_text = self.cleaner.clean_special_chars(full_text)
        full_text = self.cleaner.normalize_whitespace(full_text)

        # 2. 信息提取（已经在爬虫中完成，这里可以进一步优化）
        title = raw_data.get("title", "")
        case_number = raw_data.get("case_number", "")
        court_name = raw_data.get("court_name", "")
        case_type = raw_data.get("case_type", "")
        cause = raw_data.get("cause_of_action", "")
        decision_date = raw_data.get("decision_date", "")
        parties = raw_data.get("parties", "")
        summary = raw_data.get("summary", "")
        key_points = raw_data.get("key_points", "")
        referenced_laws = raw_data.get("referenced_laws", "")
        judgment_result = raw_data.get("judgment_result", "")

        # 3. 关键词提取和标签生成
        tags = self.keyword_extractor.generate_tags(full_text, cause)

        # 4. 生成摘要（如果没有）
        if not summary:
            summary = self._generate_summary(full_text)

        # 5. 生成裁判要旨（如果没有）
        if not key_points:
            key_points = self._extract_key_points(full_text)

        return {
            "title": title,
            "case_number": case_number,
            "court_name": court_name,
            "case_type": case_type,
            "cause_of_action": cause,
            "decision_date": decision_date,
            "parties": parties,
            "summary": summary,
            "full_text": full_text,
            "key_points": key_points,
            "referenced_laws": referenced_laws,
            "judgment_result": judgment_result,
            "tags": tags,
            "crawl_time": raw_data.get("crawl_time"),
        }

    def _generate_summary(self, text: str, max_length: int = 500) -> str:
        """生成摘要"""
        # 简单的摘要生成：提取前 N 个句子
        sentences = re.split(r"[。；]", text)
        summary_sentences = []
        total_length = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # 跳过太短的句子
            if len(sentence) < 20:
                continue

            summary_sentences.append(sentence)
            total_length += len(sentence)

            if total_length >= max_length:
                break

        return "。".join(summary_sentences) + "。" if summary_sentences else text[:max_length]

    def _extract_key_points(self, text: str) -> str:
        """提取裁判要旨"""
        # 寻找"本院认为"段落
        match = re.search(r"本院认为[：:](.+?)(?=判决如下|裁定如下)", text, re.DOTALL)
        if match:
            key_points = match.group(1).strip()
            # 提取关键句
            sentences = re.split(r"[。；]", key_points)
            key_sentences = [s.strip() for s in sentences if len(s.strip()) > 20][:5]
            return "。".join(key_sentences) + "。" if key_sentences else ""
        return ""

    async def save_to_database(self, doc_data: dict[str, Any]) -> bool:
        """保存到数据库"""
        try:
            async with async_session_factory() as session:
                # 检查是否已存在
                if doc_data.get("case_number"):
                    from sqlalchemy import select
                    result = await session.execute(
                        select(CourtCase).where(CourtCase.case_number == doc_data["case_number"])
                    )
                    if result.scalar_one_or_none():
                        logger.debug(f"文书已存在，跳过: {doc_data['case_number']}")
                        return False

                # 创建新记录
                case = CourtCase(
                    case_number=doc_data.get("case_number", ""),
                    title=doc_data.get("title", ""),
                    court_name=doc_data.get("court_name", ""),
                    case_type=doc_data.get("case_type", ""),
                    cause_of_action=doc_data.get("cause_of_action", ""),
                    decision_date=doc_data.get("decision_date", ""),
                    parties=doc_data.get("parties", ""),
                    summary=doc_data.get("summary", ""),
                    full_text=doc_data.get("full_text", ""),
                    key_points=doc_data.get("key_points", ""),
                    referenced_laws=doc_data.get("referenced_laws", ""),
                    judgment_result=doc_data.get("judgment_result", ""),
                    tags=doc_data.get("tags", ""),
                )
                session.add(case)
                await session.commit()

                logger.info(f"保存文书成功: {case.case_number}")
                return True

        except Exception as e:
            logger.error(f"保存文书失败: {e}")
            return False


# =============================================================================
# 批量处理
# =============================================================================

class BatchProcessor:
    """批量处理器"""

    def __init__(self):
        self.processor = DocumentProcessor()
        self.stats = {
            "total_processed": 0,
            "total_saved": 0,
            "errors": 0,
        }

    async def process_directory(self, input_dir: str, output_dir: str = None):
        """处理目录中的所有文书"""
        output_dir = output_dir or f"{input_dir}_processed"
        os.makedirs(output_dir, exist_ok=True)

        # 获取所有 JSON 文件
        json_files = [f for f in os.listdir(input_dir) if f.endswith(".json")]
        logger.info(f"找到 {len(json_files)} 个文书文件")

        for i, filename in enumerate(json_files, 1):
            filepath = os.path.join(input_dir, filename)

            try:
                # 读取原始数据
                with open(filepath, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)

                # 处理文书
                processed_data = self.processor.process_document(raw_data)
                self.stats["total_processed"] += 1

                # 保存处理后的数据
                output_filepath = os.path.join(output_dir, filename)
                with open(output_filepath, "w", encoding="utf-8") as f:
                    json.dump(processed_data, f, ensure_ascii=False, indent=2)

                # 保存到数据库
                saved = await self.processor.save_to_database(processed_data)
                if saved:
                    self.stats["total_saved"] += 1

                # 打印进度
                if i % 10 == 0:
                    logger.info(f"处理进度: {i}/{len(json_files)} | "
                              f"已保存: {self.stats['total_saved']}")

            except Exception as e:
                logger.error(f"处理文件失败 ({filename}): {e}")
                self.stats["errors"] += 1

        logger.info(f"批量处理完成: {self.stats}")
        return self.stats


# =============================================================================
# 主函数
# =============================================================================

async def main():
    """主函数"""
    processor = BatchProcessor()

    # 处理爬虫数据目录
    input_dir = "./crawler_data"
    if not os.path.exists(input_dir):
        logger.error(f"数据目录不存在: {input_dir}")
        return

    stats = await processor.process_directory(input_dir)
    logger.info(f"处理统计: {stats}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
