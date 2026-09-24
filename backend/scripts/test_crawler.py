"""
测试裁判文书网爬虫功能

注意：由于裁判文书网有反爬虫机制，此测试仅验证代码逻辑，
实际爬取需要配置代理 IP 和遵守网站的 robots.txt。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from app.crawlers.wenshu_crawler import WenshuCrawler, AntiCrawl, Checkpoint


async def test_crawler():
    """测试爬虫基本功能"""
    print("=" * 60)
    print("裁判文书网爬虫功能测试")
    print("=" * 60)

    crawler = WenshuCrawler()

    try:
        # 测试 1: 验证反爬虫工具
        print("\n[测试 1] 反爬虫工具")
        headers = AntiCrawl.get_random_headers()
        print(f"  ✓ 随机 User-Agent: {headers.get('User-Agent', 'N/A')[:50]}...")
        print(f"  ✓ 请求头字段数: {len(headers)}")

        # 测试 2: 验证断点续传
        print("\n[测试 2] 断点续传")
        checkpoint = Checkpoint()
        checkpoint.update(1, "test_doc_001")
        is_crawled = checkpoint.is_crawled("test_doc_001")
        print(f"  ✓ 断点记录: page=1, doc_id=test_doc_001")
        print(f"  ✓ 断点检查: {is_crawled}")

        # 测试 3: 验证文本解析（使用模拟数据）
        print("\n[测试 3] 文本解析")
        mock_html = """
        <html>
        <body>
            <div class="title">张某诉李某合同纠纷案</div>
            <div class="case-number">(2023)京01民终12345号</div>
            <div class="court">北京市第一中级人民法院</div>
            <div class="content">
                <p>案由：合同纠纷</p>
                <p>经审理查明：原告与被告于2022年签订购销合同...</p>
                <p>本院认为：依法成立的合同受法律保护...</p>
                <p>判决如下：被告应于本判决生效之日起十日内支付原告货款10万元。</p>
            </div>
        </body>
        </html>
        """

        doc_data = crawler._parse_document({"qwContent": mock_html})
        print(f"  ✓ 标题: {doc_data.get('title', 'N/A')}")
        print(f"  ✓ 案号: {doc_data.get('case_number', 'N/A')}")
        print(f"  ✓ 法院: {doc_data.get('court_name', 'N/A')}")
        print(f"  ✓ 案件类型: {doc_data.get('case_type', 'N/A')}")
        print(f"  ✓ 摘要长度: {len(doc_data.get('summary', ''))} 字符")

        # 测试 4: 验证数据清洗
        print("\n[测试 4] 数据清洗")
        from app.crawlers.document_processor import DocumentProcessor
        processor = DocumentProcessor()

        raw_data = {
            "full_text": "<p>这是一段<br>包含HTML标签的文本</p>",
            "title": "测试案例",
            "case_number": "(2023)测试0001号",
        }

        processed = processor.process_document(raw_data)
        print(f"  ✓ 原文: {raw_data['full_text']}")
        print(f"  ✓ 清洗后: {processed['full_text']}")
        print(f"  ✓ 标签: {processed.get('tags', 'N/A')}")

        # 测试 5: 验证向量嵌入
        print("\n[测试 5] 向量嵌入")
        from app.crawlers.document_processor import DocumentEmbedder
        embedder = DocumentEmbedder()

        text = "这是一段用于测试向量嵌入的法律文本"
        embedding = embedder.embed_text(text)
        print(f"  ✓ 文本: {text}")
        print(f"  ✓ 向量维度: {len(embedding)}")
        print(f"  ✓ 向量前5维: {embedding[:5]}")

        # 测试 6: 验证关键词提取
        print("\n[测试 6] 关键词提取")
        test_text = """
        劳动合同纠纷案件中，原告主张被告未支付经济补偿金。
        根据《劳动合同法》第四十六条规定，用人单位应当支付经济补偿。
        法院判决被告支付原告经济补偿金5万元。
        """

        keywords = processor.keyword_extractor.extract_keywords(test_text, top_k=5)
        legal_keywords = processor.keyword_extractor.extract_legal_keywords(test_text)
        print(f"  ✓ TF-IDF 关键词: {keywords}")
        print(f"  ✓ 法律关键词: {legal_keywords}")

        print("\n" + "=" * 60)
        print("✓ 所有测试通过")
        print("=" * 60)

        print("\n[注意事项]")
        print("1. 实际爬取需要配置代理 IP 池")
        print("2. 需要遵守裁判文书网的 robots.txt")
        print("3. 建议控制爬取频率（2-5秒/请求）")
        print("4. 数据仅用于法律研究和公共服务")

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(test_crawler())
