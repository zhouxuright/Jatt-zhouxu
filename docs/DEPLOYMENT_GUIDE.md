# 私有化部署方案

## 一、部署架构概览

本系统支持三种部署模式，满足不同规模企业的需求：

### 模式 A: 单机部署（中小企业）
适合 50 人以下团队，单台服务器承载全部服务。

### 模式 B: 标准集群（中型企业）
适合 50-500 人团队，多节点高可用部署。

### 模式 C: 企业级集群（大型企业/律所）
适合 500+ 人，完整的高可用 + 灾备方案。

---

## 二、硬件配置要求

### 模式 A — 单机部署

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 8 核 | 16 核 |
| 内存 | 32 GB | 64 GB |
| 存储 | 500 GB SSD | 1 TB NVMe SSD |
| 网络 | 100 Mbps | 1 Gbps |
| GPU | 无（使用 CPU 推理） | NVIDIA A10/A30（加速向量模型） |
| 系统 | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |

### 模式 B — 标准集群

| 节点 | 数量 | 配置 |
|------|------|------|
| 应用节点 | 2-3 | 16C/32G/500G SSD |
| 数据库节点 | 2 (主从) | 16C/64G/1TB NVMe |
| 向量库节点 | 2 | 16C/64G/500G SSD |
| 缓存节点 | 2 (Redis Sentinel) | 8C/16G/100G SSD |
| 负载均衡 | 1-2 | 4C/8G |

### 模式 C — 企业级

在模式 B 基础上增加：
- Kubernetes 编排
- 跨区域灾备
- 独立的安全审计节点
- 日志中心（ELK Stack）

---

## 三、部署步骤（模式 A 单机部署）

### 3.1 环境准备

```bash
# 系统更新
sudo apt update && sudo apt upgrade -y

# 安装 Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 安装 Docker Compose
sudo apt install docker-compose-plugin -y

# 安装 NVIDIA GPU 驱动（可选，用于加速）
# sudo apt install nvidia-driver-535 -y
# sudo apt install nvidia-container-toolkit -y
```

### 3.2 获取代码

```bash
# 克隆项目
git clone <repository-url> legal-assistant
cd legal-assistant

# 配置环境变量
cp backend/.env.example backend/.env
# 编辑 .env 文件，设置：
# - LLM API KEY
# - 数据库密码
# - JWT 密钥
```

### 3.3 初始化数据库

```bash
# 启动数据库
docker compose up -d postgres redis

# 等待数据库就绪
sleep 10

# 运行数据库迁移
docker compose exec backend alembic upgrade head

# 导入法律法规数据
docker compose exec backend python -m app.rag.mass_import --skip-milvus
```

### 3.4 配置 SSL 证书

```bash
# 方式一：Let's Encrypt（需要域名）
# 参考 nginx/nginx-ssl.conf 中的说明

# 方式二：自签名证书（内网部署）
mkdir -p nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/privkey.pem \
  -out nginx/ssl/fullchain.pem \
  -subj "/CN=legal-assistant.local"
```

### 3.5 启动服务

```bash
# 开发环境
docker compose up -d

# 生产环境（含 HTTPS + 监控）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 验证服务状态
docker compose ps
curl -k https://localhost/health
```

---

## 四、关键配置说明

### 4.1 LLM 供应商选择

```bash
# .env 配置
# 方案一：DeepSeek（国内推荐，性价比高）
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxx

# 方案二：OpenAI（需要代理）
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxxxx

# 方案三：私有部署模型（完全离线）
# 使用 vLLM 或 Ollama 部署开源模型
LLM_PROVIDER=local
LOCAL_MODEL_URL=http://localhost:11434/v1
```

### 4.2 向量模型部署

```bash
# 方式一：使用 HuggingFace 模型（默认）
# 首次启动自动下载 BGE-M3 模型（约 2.2GB）
EMBEDDING_MODEL=BAAI/bge-m3

# 方式二：预下载模型到本地
mkdir -p models/bge-m3
# 将模型文件放入 models/bge-m3 目录
# 设置环境变量
BGE_M3_MODEL_PATH=/app/models/bge-m3
```

### 4.3 数据备份配置

```bash
# 设置定时备份（每天凌晨 2 点）
crontab -e
# 添加：
0 2 * * * cd /path/to/legal-assistant && ./scripts/backup_db.sh --compress

# 手动备份
./scripts/backup_db.sh --compress

# 查看备份列表
./scripts/backup_db.sh --list
```

---

## 五、运维监控

### 5.1 健康检查

```bash
# 系统健康
curl -s https://localhost/health | python -m json.tool

# 数据库连接
docker compose exec postgres pg_isready

# Redis 连接
docker compose exec redis redis-cli ping

# Milvus 连接（如已部署）
docker compose exec milvus curl -s http://localhost:9091/healthz
```

### 5.2 日志查看

```bash
# 查看后端日志
docker compose logs -f backend

# 查看 Nginx 访问日志
docker compose logs -f nginx

# 查看结构化日志（JSON 格式）
docker compose logs backend | jq .

# 搜索错误日志
docker compose logs backend | grep ERROR
```

### 5.3 性能监控

```bash
# Prometheus 指标
curl -s https://localhost/metrics | head -20

# Grafana 面板
# 访问 https://localhost:3001（默认 admin/admin）
```

---

## 六、安全加固清单

| 项目 | 措施 | 状态 |
|------|------|------|
| HTTPS | TLS 1.2+ with Let's Encrypt | ✅ 已实现 |
| 认证 | JWT + Refresh Token | ✅ 已实现 |
| 密码 | bcrypt 哈希 + 强度要求 | ✅ 已实现 |
| 限流 | 滑动窗口 + IP/用户级 | ✅ 已实现 |
| 输入验证 | 防注入 + 文件深度扫描 | ✅ 已实现 |
| 内容安全 | AI 输入/输出过滤 | ✅ 已实现 |
| 审计日志 | 全 API 请求记录 | ✅ 已实现 |
| 数据备份 | 自动定时 + 压缩 | ✅ 已实现 |
| 容器安全 | 非 root 运行 + 资源限制 | ✅ 已实现 |
| 网络隔离 | Docker 内部网络 | ✅ 已实现 |

---

## 七、常见问题

### Q1: Milvus 启动失败？
```bash
# Milvus 需要至少 8GB 内存
# 如果内存不足，可使用 ChromaDB 作为替代（开发模式）
# 在 .env 中设置：VECTOR_STORE=chroma
```

### Q2: 模型下载很慢？
```bash
# 使用国内镜像
export HF_ENDPOINT=https://hf-mirror.com
# 或在 .env 中设置
HF_ENDPOINT=https://hf-mirror.com
```

### Q3: 如何完全离线部署？
```bash
# 1. 在有网络的环境中提前下载所有依赖
docker compose pull

# 2. 下载模型文件
pip download sentence-transformers FlagEmbedding

# 3. 打包传输到离线环境
# 4. 使用本地模型路径配置
```

### Q4: 如何扩展法律数据？
```bash
# 导入新的法律法规
docker compose exec backend python -m app.rag.batch_import --source json --file /data/new_laws.json

# 重新导入全量数据
docker compose exec backend python -m app.rag.mass_import --resume
```
