# 法律智能辅助系统 — API 使用指南

## 快速开始

### 基础信息
- **Base URL**: `https://your-domain.com/api/v1`
- **认证方式**: JWT Bearer Token
- **响应格式**: JSON

### 认证流程

```bash
# 1. 注册
curl -X POST https://your-domain.com/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "lawyer_zhang", "email": "zhang@lawfirm.com", "password": "SecurePass123!"}'

# 2. 登录获取 Token
curl -X POST https://your-domain.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "lawyer_zhang", "password": "SecurePass123!"}'
# 返回: {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer"}

# 3. 使用 Token 访问 API
curl https://your-domain.com/api/v1/auth/me \
  -H "Authorization: Bearer eyJ..."
```

---

## 核心 API

### 1. 智能法律咨询

```bash
# 流式对话（推荐）
curl -X POST https://your-domain.com/api/v1/chat/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "劳动合同到期公司不续签需要赔偿吗？", "conversation_id": null}'

# 语音输入
curl -X POST https://your-domain.com/api/v1/chat/voice \
  -H "Authorization: Bearer $TOKEN" \
  -F "audio=@question.wav"
```

### 2. 合同审查

```bash
# 上传合同文件审查
curl -X POST https://your-domain.com/api/v1/contract/review \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@contract.pdf"

# 批量审查（最多 10 份）
curl -X POST https://your-domain.com/api/v1/contract/batch-review \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@contract1.pdf" \
  -F "files=@contract2.docx"

# OCR 扫描件识别
curl -X POST https://your-domain.com/api/v1/contract/ocr \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@scanned_contract.jpg"
```

### 3. 法律文书生成

```bash
# 生成民事起诉状
curl -X POST https://your-domain.com/api/v1/document/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "document_type": "civil_complaint",
    "fields": {
      "plaintiff": "张三",
      "defendant": "李四",
      "claim": "请求判令被告支付欠款10万元",
      "facts": "被告于2024年1月向原告借款10万元..."
    }
  }'

# 批量上传文档
curl -X POST https://your-domain.com/api/v1/document/batch-upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@doc1.pdf" \
  -F "files=@doc2.docx" \
  -F "files=@doc3.txt" \
  -F "mode=analyze"
```

### 4. 法规检索

```bash
# 搜索法律条文
curl -X POST https://your-domain.com/api/v1/law/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "劳动合同解除经济补偿", "category": "劳动法", "top_k": 10}'

# 获取法律分类
curl https://your-domain.com/api/v1/law/categories \
  -H "Authorization: Bearer $TOKEN"
```

### 5. 案例检索

```bash
# 搜索案例
curl -X POST https://your-domain.com/api/v1/cases/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "劳动合同纠纷双倍工资", "case_type": "民事", "top_k": 5}'
```

### 6. 知识图谱

```bash
# 查询相关法条
curl https://your-domain.com/api/v1/knowledge/related/article_id_here \
  -H "Authorization: Bearer $TOKEN"

# 查询法律家族
curl "https://your-domain.com/api/v1/knowledge/law-family/中华人民共和国民法典" \
  -H "Authorization: Bearer $TOKEN"

# 查询引用链
curl "https://your-domain.com/api/v1/knowledge/citation-chain?from=宪法&to=民法典" \
  -H "Authorization: Bearer $TOKEN"

# 导出图谱数据
curl https://your-domain.com/api/v1/knowledge/export \
  -H "Authorization: Bearer $TOKEN"
```

### 7. 多智能体协作

```bash
# 复杂法律分析（多 Agent 协作）
curl -X POST https://your-domain.com/api/v1/collaboration/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "分析跨境电商平台知识产权侵权的法律风险和应对策略"}'

# 深度法律研究
curl -X POST https://your-domain.com/api/v1/collaboration/research \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "2024年个人信息保护的最新司法解释和实践趋势"}'

# 质量审查
curl -X POST https://your-domain.com/api/v1/collaboration/review \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "根据劳动合同法第47条...", "review_type": "legal_response"}'
```

### 8. 诉讼支持（P1.4）

```bash
# 类案检索与比对（本地 121 万裁判文书库语义检索）
curl -X POST https://your-domain.com/api/v1/litigation/similar-cases \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"case_description": "2024年3月签订建材购销合同，我方已交货，对方欠付货款38万元...", "top_k": 5}'
# 返回: similar_cases（案号/法院/判决结果/相似度）、statistics（代码统计的结果分布与金额）、comparison（LLM 比对分析）

# 裁判预测（未传类案时自动检索）
curl -X POST https://your-domain.com/api/v1/litigation/predict \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"case_description": "同上案情描述"}'

# 一键生成完整《诉讼分析报告》（六章节 markdown，约 20~60 秒）
curl -X POST https://your-domain.com/api/v1/litigation/report \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "case_description": "同上案情描述",
    "evidence_list": "购销合同原件、送货签收单、微信催款记录、银行流水",
    "claims": "请求支付剩余货款38万元及逾期违约金",
    "party": "plaintiff",
    "top_k": 5
  }'
# 返回: report_markdown（案件分析/证据清单/类案比对/裁判预测/策略风险/数据声明）

# 证据清单 / 庭审提纲 / 费用计算（P1.4 前已有）
curl -X POST https://your-domain.com/api/v1/litigation/evidence \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"case_description": "...", "cause_of_action": "买卖合同纠纷"}'
```

---

## 响应中的 AI 标识

所有 AI 生成的响应都包含以下标识字段：

```json
{
  "content": "...",
  "ai_generated": true,
  "disclaimer": "本内容由 AI 生成，仅供学习参考，不构成正式法律建议。",
  "watermark_info": {
    "model": "deepseek-chat",
    "generated_at": "2026-08-29T10:30:00Z",
    "content_type": "chat"
  }
}
```

---

## 错误码

| HTTP 状态码 | 说明 |
|------------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 401 | 未认证 / Token 过期 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 409 | 冲突（如重复注册） |
| 413 | 文件过大 |
| 422 | 内容安全过滤 |
| 429 | 请求频率超限 |
| 500 | 服务器内部错误 |
