# 企业级部署指南 | Legal Intelligent Assistance System

> 版本：1.0 | 更新日期：2026-09-08
> 适用环境：生产环境私有化部署（Offline / Air-Gapped）

---

## 目录

1. [硬件需求规划](#一硬件需求规划)
2. [离线部署方案](#二离线部署方案)
3. [安全加固清单](#三安全加固清单)
4. [多租户隔离](#四多租户隔离)
5. [监控告警](#五监控告警)
6. [AIGC合规备案](#六aigc合规备案)
7. [附录：常用运维命令速查](#七附录常用运维命令速查)

---

## 一、硬件需求规划

### 1.1 三级配置矩阵

系统支持三种规模的部署配置。请根据实际用户并发数、文档体量和推理负载选择合适的级别。

| 配置级别 | 适用场景 | 并发用户 | 文档存储量 | 日均推理请求 |
|---------|---------|---------|-----------|------------|
| 最小配置 | 开发 / 测试 / PoC验证 | ≤10 | ≤10万份 | ≤500 |
| 推荐配置 | 小型律所 / 部门级部署 | 10–100 | 10–100万份 | 500–5,000 |
| 企业配置 | 大型企业 / 集团级部署 | 100–1,000+ | 100万份+ | 5,000+ |

---

### 1.2 最小配置（开发 / 测试环境）

> 目标：验证功能、接口联调、小规模PoC。不推荐用于生产。

| 组件 | 规格 |
|------|------|
| **CPU** | 4 核（Intel Xeon / AMD EPYC 或同级） |
| **内存** | 16 GB DDR4 |
| **系统盘** | 200 GB SSD |
| **数据盘** | 500 GB SSD（PostgreSQL + Milvus + 模型文件） |
| **GPU** | 无（CPU推理，性能受限） |
| **网络** | 100 Mbps 内网 |
| **操作系统** | Ubuntu 22.04 LTS / CentOS 8 Stream |

**部署模式：** 单机 Docker Compose，所有服务容器化运行于同一节点。

**性能预期：**
- 单次 RAG 检索+生成响应时间：8–25 秒（CPU推理）
- 向量索引构建速度：约 5,000 文档/小时
- 语音转写（Whisper）：1 分钟音频约需 3–5 分钟处理

---

### 1.3 推荐配置（小型律所 / 部门级）

> 目标：50 人以下律所日常使用，支持完整 RAG、语音转写、文档生成流水线。

| 组件 | 规格 |
|------|------|
| **CPU** | 16 核（Intel Xeon Silver 4314 或同级） |
| **内存** | 64 GB DDR4 ECC |
| **系统盘** | 500 GB NVMe SSD |
| **数据盘** | 2 TB NVMe SSD（RAID-1 可选） |
| **GPU** | NVIDIA A10 (24GB VRAM) 或 RTX 4090 (24GB) |
| **网络** | 1 Gbps 内网 |
| **操作系统** | Ubuntu 22.04 LTS |

**部署模式：** 单机或双机 Docker Compose，GPU 节点独立承载推理服务。

**关键说明：**
- GPU 用于加速 BGE-M3 Embedding、Reranker 模型、Whisper 语音转写
- CUDA 版本要求：≥ 12.1，cuDNN ≥ 8.9
- 若使用 vLLM 部署本地 LLM，需额外 24GB+ VRAM

**性能预期：**
- 单次 RAG 响应时间：2–5 秒
- 向量索引构建速度：约 30,000 文档/小时
- Whisper 转写：1 分钟音频约 10–15 秒

---

### 1.4 企业配置（大型企业 / 集团级）

> 目标：500+ 用户、多部门/多租户、高可用集群、灾备方案。

| 节点角色 | 数量 | 单节点配置 |
|---------|------|-----------|
| **应用节点** (FastAPI) | 3–5 | 32C / 64GB / 500GB SSD |
| **数据库节点** (PostgreSQL) | 2 (主从热备) | 32C / 128GB / 4TB NVMe (RAID-10) |
| **向量库节点** (Milvus) | 3 (集群模式) | 16C / 64GB / 2TB SSD |
| **缓存节点** (Redis Cluster) | 3 (三主三从) | 8C / 32GB / 200GB SSD |
| **GPU推理节点** | 2–4 | 32C / 128GB / 1TB SSD + 2×NVIDIA A100 (80GB) |
| **对象存储** (MinIO) | 3 | 8C / 16GB / 10TB HDD×4 (JBOD) |
| **负载均衡** (Nginx/HAProxy) | 2 (主备) | 4C / 8GB / 100GB SSD |
| **监控节点** (Prometheus+Grafana) | 1 | 8C / 32GB / 2TB SSD |

**总存储规划（企业级）：**

| 数据类型 | 预估大小 | 增长速度 |
|---------|---------|---------|
| PostgreSQL 业务数据 | 200GB–1TB | ~5GB/月 |
| Milvus 向量数据 | 500GB–2TB | ~20GB/月 |
| MinIO 原始文档 | 1TB–10TB | ~50GB/月 |
| 模型文件 | 50–100GB | 按需更新 |
| 日志与审计 | 200GB–1TB | ~10GB/月 |

---

### 1.5 GPU 选型指南

| GPU 型号 | VRAM | 适用场景 | 参考单价（￥） |
|---------|------|---------|-------------|
| NVIDIA RTX 4090 | 24GB | 开发测试、小批量推理 | 12,000–15,000 |
| NVIDIA A10 | 24GB | 小型律所生产环境 | 8,000–12,000 |
| NVIDIA A30 | 24GB | 中型部署、多模型并行 | 15,000–20,000 |
| NVIDIA A100 (40GB) | 40GB | 大型部署、vLLM推理 | 40,000–60,000 |
| NVIDIA A100 (80GB) | 80GB | 企业级、大模型微调 | 70,000–100,000 |
| NVIDIA H100 | 80GB | 超大规模、训练+推理 | 150,000+ |

**VRAM 分配建议（A100 80GB）：**

| 模型 | VRAM 占用 | 说明 |
|------|----------|------|
| BGE-M3 (Embedding) | ~2 GB | 1024维向量编码 |
| BGE-Reranker-v2-M3 | ~2 GB | 跨编码器重排序 |
| Whisper large-v3 | ~3 GB | 语音转文字 |
| vLLM (Qwen2-7B) | ~16 GB | 本地LLM推理 |
| 系统预留 | ~4 GB | CUDA上下文、显存碎片 |
| **合计** | **~27 GB** | 单卡可承载全部模型 |

---

## 二、离线部署方案

### 2.1 离线部署总览

离线部署适用于政务、军工、金融等无法连接互联网的场景。整体流程分为四个阶段：

```
[在线环境准备] --> [介质传输] --> [离线环境安装] --> [验证上线]
```

**所需介质：**
- 移动硬盘 / 加密U盘（容量 ≥ 200GB）
- 或刻录 DVD 蓝光盘组（大容量场景）
- 或内网文件传输系统（网闸 / 光闸）

---

### 2.2 Docker 镜像离线导出/导入

#### 步骤一：在线环境拉取并保存镜像

```bash
#!/bin/bash
# save_docker_images.sh - 在有网络的机器上执行

# 定义所需镜像列表
IMAGES=(
    # === 基础设施 ===
    "postgres:16-alpine"
    "redis:7-alpine"
    "minio/minio:latest"
    "milvusdb/milvus:v2.4-lts"

    # === 应用服务 ===
    "legal-assistant-backend:latest"
    "legal-assistant-frontend:latest"

    # === 监控（可选） ===
    "prom/prometheus:v2.51.0"
    "grafana/grafana:10.4.0"
    "prom/node-exporter:v1.7.0"

    # === AI推理（可选） ===
    "vllm/vllm-openai:v0.4.0"
    "nvidia/cuda:12.1.1-runtime-ubuntu22.04"
)

# 创建导出目录
mkdir -p ./docker_images_offline

for img in "${IMAGES[@]}"; do
    echo ">>> Pulling: $img"
    docker pull "$img"

    # 用镜像名的安全形式作为文件名
    safe_name=$(echo "$img" | tr '/:' '__')
    echo ">>> Saving: $img -> docker_images_offline/${safe_name}.tar"
    docker save "$img" -o "docker_images_offline/${safe_name}.tar"
done

# 打包压缩
tar -czf docker_images_offline.tar.gz docker_images_offline/
echo ">>> Done. Total size:"
du -sh docker_images_offline.tar.gz
```

#### 步骤二：离线环境加载镜像

```bash
#!/bin/bash
# load_docker_images.sh - 在离线目标机器上执行

mkdir -p ./docker_images_offline
tar -xzf docker_images_offline.tar.gz -C ./docker_images_offline/

for tar_file in ./docker_images_offline/*.tar; do
    echo ">>> Loading: $tar_file"
    docker load -i "$tar_file"
done

# 验证
echo ">>> Loaded images:"
docker images
```

#### 步骤三：构建应用镜像（如需在离线环境本地构建）

```bash
# 如果需要在离线环境重新构建应用镜像，需先准备离线 pip 包（见 2.4 节）
# 然后修改 Dockerfile 使用本地 wheel 包

# 构建后端镜像
cd backend/
docker build \
    --build-arg PIP_INDEX_URL=file:///opt/offline_pip/simple \
    -t legal-assistant-backend:latest \
    .

# 构建前端镜像
cd frontend/
docker build -t legal-assistant-frontend:latest .
```

---

### 2.3 模型文件离线包

系统依赖以下 AI 模型文件，需提前下载并打包。

#### 模型清单

| 模型名称 | 用途 | 文件大小 | HuggingFace 地址 |
|---------|------|---------|-----------------|
| BAAI/bge-m3 | 文本向量化 (Embedding) | ~2.2 GB | https://huggingface.co/BAAI/bge-m3 |
| BAAI/bge-reranker-v2-m3 | 语义重排序 (Reranker) | ~1.1 GB | https://huggingface.co/BAAI/bge-reranker-v2-m3 |
| openai/whisper-large-v3 | 语音转文字 (ASR) | ~3.0 GB | https://huggingface.co/openai/whisper-large-v3 |
| Qwen/Qwen2-7B-Instruct (可选) | 本地LLM推理 | ~14 GB | https://huggingface.co/Qwen/Qwen2-7B-Instruct |

#### 在线环境下载脚本

```bash
#!/bin/bash
# download_models.sh - 在有网络的机器上执行

MODEL_DIR="./offline_models"
mkdir -p "$MODEL_DIR"

# 方法一：使用 huggingface-cli（推荐，支持断点续传）
pip install -U huggingface_hub[cli]

# 下载 BGE-M3
huggingface-cli download BAAI/bge-m3 \
    --local-dir "$MODEL_DIR/bge-m3" \
    --local-dir-use-symlinks False

# 下载 Reranker
huggingface-cli download BAAI/bge-reranker-v2-m3 \
    --local-dir "$MODEL_DIR/bge-reranker-v2-m3" \
    --local-dir-use-symlinks False

# 下载 Whisper
huggingface-cli download openai/whisper-large-v3 \
    --local-dir "$MODEL_DIR/whisper-large-v3" \
    --local-dir-use-symlinks False

# （可选）下载本地 LLM
huggingface-cli download Qwen/Qwen2-7B-Instruct \
    --local-dir "$MODEL_DIR/Qwen2-7B-Instruct" \
    --local-dir-use-symlinks False

# 打包
tar -czf offline_models.tar.gz "$MODEL_DIR/"
echo ">>> Model package size:"
du -sh offline_models.tar.gz
```

#### 离线环境部署配置

将模型文件放置到服务器指定目录后，修改 `docker-compose.prod.yml` 中的挂载路径：

```yaml
# docker-compose.prod.yml 片段

services:
  backend:
    image: legal-assistant-backend:latest
    volumes:
      - /opt/models/bge-m3:/app/models/bge-m3:ro
      - /opt/models/bge-reranker-v2-m3:/app/models/bge-reranker-v2-m3:ro
      - /opt/models/whisper-large-v3:/app/models/whisper-large-v3:ro
    environment:
      # 指向本地模型路径（不使用 HuggingFace Hub）
      - EMBEDDING_MODEL_PATH=/app/models/bge-m3
      - RERANKER_MODEL_PATH=/app/models/bge-reranker-v2-m3
      - WHISPER_MODEL_PATH=/app/models/whisper-large-v3
      - HF_HUB_OFFLINE=1          # 禁止访问 HuggingFace Hub
      - TRANSFORMERS_OFFLINE=1
```

---

### 2.4 离线 pip 包准备

```bash
#!/bin/bash
# download_pip_packages.sh - 在有网络且与目标机 OS/Python 版本一致的机器上执行

# 确认 Python 版本一致
python --version  # 需与目标机一致，推荐 Python 3.11

OFFLINE_DIR="./offline_pip_packages"
mkdir -p "$OFFLINE_DIR"

# 导出 requirements.txt 中所有依赖的 wheel 包
pip download \
    -r requirements.txt \
    -d "$OFFLINE_DIR" \
    --platform manylinux2014_x86_64 \
    --platform linux_x86_64 \
    --python-version 311 \
    --only-binary=:all:

# 如果目标机是 Windows
pip download \
    -r requirements.txt \
    -d "$OFFLINE_DIR" \
    --platform win_amd64 \
    --python-version 311 \
    --only-binary=:all:

# 打包
tar -czf offline_pip_packages.tar.gz "$OFFLINE_DIR/"
```

#### 离线环境安装

```bash
# 在离线目标机器上
mkdir -p /opt/offline_pip_packages
tar -xzf offline_pip_packages.tar.gz -C /opt/

# 安装（使用本地包源）
pip install --no-index \
    --find-links=/opt/offline_pip_packages \
    -r requirements.txt

# 或者搭建本地 PyPI 镜像（推荐用于多次安装场景）
pip install pypiserver
pypi-server run -p 8080 /opt/offline_pip_packages &

# 配置 pip 使用本地源
cat > /etc/pip.conf << 'EOF'
[global]
index-url = http://localhost:8080/simple
trusted-host = localhost
EOF
```

---

### 2.5 完整离线部署步骤

按以下顺序在离线环境执行：

```
步骤1: 操作系统初始化
  ├── 安装 Ubuntu 22.04 LTS Server
  ├── 配置静态 IP、DNS（内网）、NTP（内网时间源）
  ├── 创建部署用户（非 root）
  └── 配置 SSH 密钥登录，禁用密码登录

步骤2: 基础软件安装（需离线 deb 包）
  ├── Docker Engine 24.0+
  ├── Docker Compose v2.20+
  ├── NVIDIA Driver 535+（如需 GPU）
  ├── NVIDIA Container Toolkit
  └── 基础工具：curl, jq, htop, net-tools

步骤3: 加载离线资源
  ├── 导入 Docker 镜像        (2.2 节)
  ├── 部署模型文件             (2.3 节)
  └── 安装 Python 依赖         (2.4 节)

步骤4: 配置环境变量
  └── 复制并修改 .env 文件（详见安全加固章节）

步骤5: 启动服务
  ├── docker compose -f docker-compose.prod.yml up -d
  ├── 等待所有服务健康检查通过
  └── docker compose -f docker-compose.prod.yml ps

步骤6: 初始化数据
  ├── 执行数据库迁移：alembic upgrade head
  ├── 创建管理员账户
  ├── 初始化 Milvus 集合与索引
  └── 导入预置法律知识库（如有）

步骤7: 验证
  ├── 访问前端页面 https://<server-ip>:443
  ├── 调用 /api/v1/health 检查服务状态
  ├── 测试 Embedding 模型推理
  ├── 测试 RAG 检索链路
  └── 测试 LLM 对话生成
```

---

### 2.6 离线 deb 包准备清单

```bash
# 在在线环境下载依赖 deb 包
sudo apt install --download-only \
    docker-ce docker-ce-cli containerd.io docker-compose-plugin \
    curl jq htop net-tools ntp chrony

# 或直接下载 Docker 离线二进制
# https://download.docker.com/linux/ubuntu/dists/jammy/pool/stable/amd64/

# NVIDIA 驱动离线包
# https://www.nvidia.com/Download/index.php
# 下载 .run 安装包，约 300MB

# NVIDIA Container Toolkit
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
```

---

## 三、安全加固清单

### 3.1 安全加固总览

```
┌─────────────────────────────────────────────────────────┐
│                    安全加固分层模型                        │
├─────────────────────────────────────────────────────────┤
│ L7 应用层  │ JWT轮换 · IP白名单 · 速率限制 · 审计日志     │
│ L6 数据层  │ TDE加密 · 字段级加密 · 脱敏 · 备份加密        │
│ L5 传输层  │ TLS 1.3 · mTLS · Redis ACL+TLS             │
│ L4 网络层  │ 防火墙 · VPC · 安全组 · WAF                  │
│ L3 主机层  │ SSH加固 · 内核参数 · SELinux · 磁盘加密       │
└─────────────────────────────────────────────────────────┘
```

---

### 3.2 PostgreSQL TDE 透明数据加密

#### 方案选型

| 方案 | 说明 | 适用场景 |
|------|------|---------|
| **pgcrypto 扩展** | 字段级加密，应用层可控 | 中小规模、敏感字段有限 |
| **PostgreSQL + dm-crypt** | 操作系统级磁盘加密 | 全库加密、性能要求高 |
| **EnterpriseDB TDE** | 商业版原生 TDE | 有预算、需要合规认证 |
| **pgsqlium (开源TDE)** | 社区版 TDE 补丁 | 预算有限、需要 TDE |

**推荐方案：** pgcrypto 字段级加密 + dm-crypt 磁盘加密（双重保护）

#### pgcrypto 字段级加密配置

```sql
-- 安装 pgcrypto 扩展
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 创建加密函数（使用 AES-256-CBC）
CREATE OR REPLACE FUNCTION encrypt_field(plaintext TEXT, key TEXT)
RETURNS BYTEA AS $$
BEGIN
    RETURN pgp_sym_encrypt(plaintext, key, 'cipher-algo=aes256');
END;
$$ LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER;

CREATE OR REPLACE FUNCTION decrypt_field(ciphertext BYTEA, key TEXT)
RETURNS TEXT AS $$
BEGIN
    RETURN pgp_sym_decrypt(ciphertext, key);
END;
$$ LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER;

-- 示例：加密存储用户身份证号
ALTER TABLE users ADD COLUMN id_number_enc BYTEA;

-- 写入时加密（密钥从环境变量传入）
UPDATE users SET id_number_enc = encrypt_field('110101199001011234', current_setting('app.encryption_key'));

-- 读取时解密
SELECT decrypt_field(id_number_enc, current_setting('app.encryption_key')) AS id_number FROM users WHERE id = 1;
```

#### dm-crypt 磁盘加密（操作系统层）

```bash
# 在安装系统时选择全盘加密（LVM + LUKS）
# 或对数据盘单独加密

# 加密数据盘 /dev/sdb
sudo cryptsetup luksFormat --type luks2 /dev/sdb
sudo cryptsetup open /dev/sdb encrypted_data
sudo mkfs.ext4 /dev/mapper/encrypted_data

# /etc/fstab 挂载
# /dev/mapper/encrypted_data  /var/lib/postgresql  ext4  defaults  0  2

# 密钥文件（放在加密的系统盘上）
sudo dd if=/dev/urandom of=/root/pg_disk.key bs=512 count=8
sudo cryptsetup luksAddKey /dev/sdb /root/pg_disk.key
```

---

### 3.3 Redis ACL + TLS

#### Redis ACL 配置

编辑 `redis.conf` 或通过 Docker 环境变量注入：

```conf
# redis.conf 安全配置

# 禁用默认无密码访问
protected-mode yes

# 创建管理员账户
user admin on >AdminStr0ng!Passw0rd_2024 ~* &* +@all

# 创建应用专用账户（最小权限）
user legal_app on >AppSp3cific!Key_2024 ~cache:* ~session:* ~task:* &* +@read +@write +@connection -@admin -@dangerous

# 创建监控专用账户（只读）
user prometheus on >M0nitor!Key_2024 ~info:* &* +@read +@connection -@write -@admin -@dangerous

# 禁用危险命令
rename-command FLUSHALL ""
rename-command FLUSHDB ""
rename-command CONFIG ""
rename-command DEBUG ""
rename-command SHUTDOWN "SHUTDOWN_b8f2a91c"
```

#### Redis TLS 配置

```conf
# redis.conf TLS 配置

# 启用 TLS
tls-port 6380
port 0  # 禁用非TLS端口

# 证书配置
tls-cert-file /etc/redis/tls/redis.crt
tls-key-file /etc/redis/tls/redis.key
tls-ca-cert-file /etc/redis/tls/ca.crt

# 强制客户端认证
tls-auth-clients optional

# 内部通信 TLS（集群复制）
tls-replication yes
tls-cluster yes

# 最低 TLS 版本
tls-protocols "TLSv1.2 TLSv1.3"
```

#### 生成 Redis TLS 证书

```bash
#!/bin/bash
# generate_redis_certs.sh

CERT_DIR="./redis_tls_certs"
mkdir -p "$CERT_DIR"

# 生成 CA
openssl genrsa -out "$CERT_DIR/ca.key" 4096
openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" \
    -sha256 -days 1825 -out "$CERT_DIR/ca.crt" \
    -subj "/CN=Legal-Assistant-Redis-CA"

# 生成 Redis 服务端证书
openssl genrsa -out "$CERT_DIR/redis.key" 2048
openssl req -new -key "$CERT_DIR/redis.key" -out "$CERT_DIR/redis.csr" \
    -subj "/CN=redis-server"
openssl x509 -req -in "$CERT_DIR/redis.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial -out "$CERT_DIR/redis.crt" \
    -days 825 -sha256

# 设置权限
chmod 600 "$CERT_DIR"/*.key
chmod 644 "$CERT_DIR"/*.crt
```

#### 应用侧 Redis 连接配置

```env
# .env.production

REDIS_URL=rediss://legal_app:AppSp3cific!Key_2024@redis-host:6380/0
REDIS_TLS_CA_CERTS=/etc/ssl/certs/redis-ca.crt
REDIS_TLS_CERT_REQS=required
```

---

### 3.4 JWT 密钥轮换

#### 轮换策略

| 参数 | 推荐值 | 说明 |
|------|-------|------|
| 密钥长度 | 256 bit (32 byte) | HMAC-SHA256 |
| 轮换周期 | 30 天 | 定期轮换 |
| 重叠窗口 | 24 小时 | 新旧密钥同时有效 |
| 算法 | HS256 / RS256 | RS256 适合分布式 |

#### 实现方案

```python
# app/core/jwt_rotation.py

import secrets
import time
from typing import Optional
import jwt
from datetime import datetime, timedelta

class JWTKeyRotator:
    """JWT 密钥轮换管理器"""

    def __init__(self, redis_client):
        self.redis = redis_client
        self.current_key_id: Optional[str] = None
        self.keys: dict = {}
        self._load_keys()

    def _load_keys(self):
        """从 Redis 加载所有有效密钥"""
        keys_data = self.redis.hgetall("jwt:keys")
        for kid, key_data in keys_data.items():
            self.keys[kid.decode()] = key_data.decode()

    def rotate(self):
        """执行密钥轮换"""
        # 生成新密钥
        new_key_id = secrets.token_hex(8)
        new_key = secrets.token_hex(32)  # 256-bit

        # 存储新密钥
        self.redis.hset("jwt:keys", new_key_id, new_key)
        self.redis.hset("jwt:key_meta", new_key_id, str(int(time.time())))

        # 标记为当前密钥
        self.redis.set("jwt:current_key_id", new_key_id)
        self.current_key_id = new_key_id

        # 清理超过重叠窗口的旧密钥（48小时后删除）
        self._cleanup_old_keys(overlap_seconds=48 * 3600)

        return new_key_id

    def _cleanup_old_keys(self, overlap_seconds: int):
        """清理过期密钥"""
        now = int(time.time())
        meta = self.redis.hgetall("jwt:key_meta")
        current = self.redis.get("jwt:current_key_id").decode()

        for kid, created_at in meta.items():
            kid_str = kid.decode()
            if kid_str == current:
                continue
            if now - int(created_at.decode()) > overlap_seconds:
                self.redis.hdel("jwt:keys", kid)
                self.redis.hdel("jwt:key_meta", kid)

    def encode(self, payload: dict) -> str:
        """使用当前密钥编码 JWT"""
        if not self.current_key_id:
            self.current_key_id = self.redis.get("jwt:current_key_id").decode()

        key = self.keys.get(self.current_key_id)
        if not key:
            self._load_keys()
            key = self.keys[self.current_key_id]

        return jwt.encode(
            {**payload, "kid": self.current_key_id},
            key,
            algorithm="HS256"
        )

    def decode(self, token: str) -> dict:
        """解码 JWT（自动尝试所有有效密钥）"""
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        # 优先使用 kid 指定的密钥
        if kid and kid in self.keys:
            return jwt.decode(token, self.keys[kid], algorithms=["HS256"])

        # 回退：遍历所有密钥
        for key_id, key in self.keys.items():
            try:
                return jwt.decode(token, key, algorithms=["HS256"])
            except jwt.InvalidSignatureError:
                continue

        raise jwt.InvalidSignatureError("No valid key found for token")
```

#### 定时轮换任务

```python
# app/services/key_rotation_scheduler.py

from apscheduler.schedulers.background import BackgroundScheduler

def setup_key_rotation(redis_client, interval_days: int = 30):
    """设置定时密钥轮换"""
    rotator = JWTKeyRotator(redis_client)

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=rotator.rotate,
        trigger="interval",
        days=interval_days,
        id="jwt_key_rotation",
        replace_existing=True
    )
    scheduler.start()

    # 首次启动时如果没有密钥则初始化
    if not redis_client.exists("jwt:current_key_id"):
        rotator.rotate()
```

---

### 3.5 IP 白名单

#### Nginx 层 IP 白名单

```nginx
# /etc/nginx/conf.d/ip_whitelist.conf

# 管理后台 IP 白名单
geo $admin_allowed {
    default 0;

    # 内网段
    10.0.0.0/8        1;
    172.16.0.0/12     1;
    192.168.0.0/16    1;

    # 特定公网 IP（VPN 出口）
    203.0.113.50/32   1;
    198.51.100.10/32  1;
}

server {
    # 管理后台 - 仅白名单 IP 可访问
    location /admin/ {
        if ($admin_allowed = 0) {
            return 403;
        }
        proxy_pass http://backend:8000;
    }

    # API 接口 - 速率限制 + IP 白名单
    location /api/ {
        # 全局限速
        limit_req zone=api_limit burst=50 nodelay;

        # 管理接口额外限制
        location ~ ^/api/v1/admin/ {
            if ($admin_allowed = 0) {
                return 403;
            }
            proxy_pass http://backend:8000;
        }

        proxy_pass http://backend:8000;
    }
}

# 速率限制区域定义
http {
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=30r/s;
    limit_req_zone $binary_remote_addr zone=login_limit:10m rate=5r/m;
}
```

#### 应用层 IP 白名单中间件

```python
# app/middleware/ip_whitelist.py

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from ipaddress import ip_address, ip_network
import redis.asyncio as redis

class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """IP 白名单中间件（支持动态配置）"""

    # 不需要白名单检查的路径
    EXEMPT_PATHS = ["/api/v1/health", "/docs", "/openapi.json"]

    def __init__(self, app, redis_client: redis.Redis):
        super().__init__(app)
        self.redis = redis_client

    async def dispatch(self, request: Request, call_next):
        # 跳过豁免路径
        if any(request.url.path.startswith(p) for p in self.EXEMPT_PATHS):
            return await call_next(request)

        client_ip = self._get_client_ip(request)

        # 检查是否在白名单中
        allowed = await self._check_ip(client_ip)
        if not allowed:
            raise HTTPException(status_code=403, detail="IP not allowed")

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """获取真实客户端 IP（支持反向代理）"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host

    async def _check_ip(self, ip_str: str) -> bool:
        """检查 IP 是否在白名单中（从 Redis 动态加载）"""
        # 从 Redis 获取白名单列表
        whitelist = await self.redis.smembers("ip:whitelist")

        if not whitelist:
            # 如果白名单为空，允许所有（开发模式）
            return True

        client_ip = ip_address(ip_str)
        for entry in whitelist:
            network = ip_network(entry.decode(), strict=False)
            if client_ip in network:
                return True

        return False
```

#### 白名单管理 API

```python
# 管理白名单的 API 端点

from fastapi import APIRouter, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/admin/ip-whitelist", tags=["admin"])

class WhitelistEntry(BaseModel):
    cidr: str  # e.g. "10.0.0.0/8" or "203.0.113.50/32"
    description: str

@router.post("/")
async def add_whitelist_entry(entry: WhitelistEntry, admin=Depends(require_admin)):
    """添加 IP 白名单条目"""
    await redis.sadd("ip:whitelist", entry.cidr)
    await redis.hset("ip:whitelist:meta", entry.cidr, entry.description)
    return {"status": "ok", "entry": entry}

@router.delete("/{cidr}")
async def remove_whitelist_entry(cidr: str, admin=Depends(require_admin)):
    """移除 IP 白名单条目"""
    await redis.srem("ip:whitelist", cidr)
    await redis.hdel("ip:whitelist:meta", cidr)
    return {"status": "ok"}

@router.get("/")
async def list_whitelist(admin=Depends(require_admin)):
    """列出所有白名单条目"""
    entries = await redis.smembers("ip:whitelist")
    return {"entries": [e.decode() for e in entries]}
```

---

### 3.6 审计日志

#### 审计日志架构

```
[应用层] --> [审计中间件] --> [结构化日志] --> [写入通道]
                                                      │
                            ┌─────────────────────────┤
                            ▼                         ▼
                    [PostgreSQL审计表]          [文件日志 (JSON)]
                    (查询/合规审计)              (备份/归档/ELK)
```

#### 审计中间件实现

```python
# app/middleware/audit_log.py

import json
import time
from datetime import datetime, timezone
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

class AuditMiddleware(BaseHTTPMiddleware):
    """全量审计日志中间件"""

    # 不记录审计日志的路径
    SKIP_PATHS = ["/api/v1/health", "/metrics", "/favicon.ico"]

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in self.SKIP_PATHS):
            return await call_next(request)

        start_time = time.time()

        # 记录请求信息
        audit_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request.headers.get("X-Request-ID", ""),
            "method": request.method,
            "path": request.url.path,
            "query_params": str(request.query_params),
            "client_ip": self._get_client_ip(request),
            "user_agent": request.headers.get("User-Agent", ""),
            "user_id": None,  # 从认证中间件注入
        }

        # 尝试获取已认证用户信息
        if hasattr(request.state, "user_id"):
            audit_entry["user_id"] = request.state.user_id

        response = await call_next(request)

        # 记录响应信息
        audit_entry.update({
            "status_code": response.status_code,
            "response_time_ms": round((time.time() - start_time) * 1000, 2),
            "response_size": response.headers.get("Content-Length", 0),
        })

        # 敏感路径记录请求体（脱敏处理）
        if request.method in ("POST", "PUT", "PATCH"):
            audit_entry["has_body"] = True
            # 注意：不直接记录请求体，避免密码等敏感信息泄露

        # 异步写入审计日志
        await self._write_audit(audit_entry)

        # 在响应头中添加审计追踪 ID
        response.headers["X-Audit-Request-ID"] = audit_entry["request_id"]

        return response

    async def _write_audit(self, entry: dict):
        """写入审计日志（双写：数据库 + 文件）"""
        import asyncio

        # 1. 写入文件日志（JSON Lines 格式）
        await asyncio.to_thread(self._write_file_audit, entry)

        # 2. 异步写入数据库审计表
        await self._write_db_audit(entry)

    def _write_file_audit(self, entry: dict):
        """追加写入文件审计日志"""
        log_path = "/var/log/legal-assistant/audit.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def _write_db_audit(self, entry: dict):
        """写入数据库审计表"""
        # 使用独立数据库连接，不影响业务请求
        try:
            await audit_db.execute(
                audit_logs.insert().values(**entry)
            )
        except Exception:
            # 审计日志写入失败不应影响业务
            pass
```

#### 审计数据库表设计

```sql
-- 审计日志表（按月分区）
CREATE TABLE audit_logs (
    id BIGSERIAL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id VARCHAR(64),
    method VARCHAR(10) NOT NULL,
    path VARCHAR(512) NOT NULL,
    query_params TEXT,
    client_ip INET NOT NULL,
    user_id BIGINT,
    status_code INTEGER NOT NULL,
    response_time_ms REAL,
    response_size BIGINT,
    has_body BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- 按月创建分区
CREATE TABLE audit_logs_2026_09 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE audit_logs_2026_10 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

-- 索引
CREATE INDEX idx_audit_timestamp ON audit_logs (timestamp DESC);
CREATE INDEX idx_audit_user_id ON audit_logs (user_id);
CREATE INDEX idx_audit_client_ip ON audit_logs (client_ip);
CREATE INDEX idx_audit_path ON audit_logs (path);

-- 自动分区维护（pg_partman 扩展 或 cron 任务）
-- 建议保留最近 12 个月的审计日志
```

---

### 3.7 数据备份策略

#### 备份方案矩阵

| 组件 | 备份方式 | 频率 | 保留期 | 存储位置 |
|------|---------|------|-------|---------|
| PostgreSQL | pg_basebackup (全量) + WAL归档(增量) | 全量:每日 / 增量:实时 | 30天 | 异地备份存储 |
| Milvus | 元数据导出 + 数据快照 | 每日 | 14天 | 本地+异地 |
| Redis | RDB快照 + AOF日志 | RDB:每小时 / AOF:每秒 | 7天 | 本地 |
| MinIO | mc mirror 跨集群复制 | 实时 | 与源同步 | 异地机房 |
| 配置文件 | Git版本控制 | 每次变更 | 永久 | Git仓库 |
| 模型文件 | rsync增量同步 | 按需 | 最近3个版本 | 离线包仓库 |

#### PostgreSQL 自动备份脚本

```bash
#!/bin/bash
# /opt/scripts/pg_backup.sh

set -euo pipefail

BACKUP_DIR="/var/backups/postgresql"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DB_NAME="legal_assistant"
DB_USER="backup_user"

# 创建备份目录
mkdir -p "$BACKUP_DIR/full"
mkdir -p "$BACKUP_DIR/wal_archive"

# 全量备份
echo "[$(date)] Starting full backup..."
pg_basebackup \
    -h localhost \
    -U "$DB_USER" \
    -D "$BACKUP_DIR/full/backup_${TIMESTAMP}" \
    -Ft -z -Xs -P \
    --label="full_backup_${TIMESTAMP}"

# 验证备份完整性
echo "[$(date)] Verifying backup..."
pg_verifybackup "$BACKUP_DIR/full/backup_${TIMESTAMP}" || {
    echo "ERROR: Backup verification failed!"
    exit 1
}

# 清理过期备份
echo "[$(date)] Cleaning old backups (>${RETENTION_DAYS} days)..."
find "$BACKUP_DIR/full" -maxdepth 1 -type d -mtime +${RETENTION_DAYS} -exec rm -rf {} \;

# 上传到异地备份（S3/MinIO）
if command -v mc &>/dev/null; then
    mc cp --recursive \
        "$BACKUP_DIR/full/backup_${TIMESTAMP}/" \
        "remote-backup/pg-full/backup_${TIMESTAMP}/"
fi

echo "[$(date)] Backup completed: backup_${TIMESTAMP}"
echo "Size: $(du -sh "$BACKUP_DIR/full/backup_${TIMESTAMP}" | cut -f1)"
```

```bash
# crontab -e
# 每日凌晨 2:00 执行全量备份
0 2 * * * /opt/scripts/pg_backup.sh >> /var/log/pg_backup.log 2>&1
```

#### 备份恢复演练

```bash
# 恢复 PostgreSQL 到指定时间点（PITR）
# 1. 停止 PostgreSQL
systemctl stop postgresql

# 2. 恢复基础备份
rm -rf /var/lib/postgresql/16/main/*
tar -xzf "$BACKUP_DIR/full/backup_XXXXXXXX_XXXXXX/base.tar.gz" \
    -C /var/lib/postgresql/16/main/

# 3. 配置恢复目标
cat > /var/lib/postgresql/16/main/postgresql.auto.conf << 'EOF'
restore_command = 'cp /var/backups/postgresql/wal_archive/%f %p'
recovery_target_time = '2026-09-08 12:00:00+08'
recovery_target_action = 'promote'
EOF

# 4. 创建恢复信号文件
touch /var/lib/postgresql/16/main/recovery.signal

# 5. 启动 PostgreSQL（自动进入恢复模式）
systemctl start postgresql

# 6. 验证恢复结果
psql -c "SELECT pg_is_in_recovery();"  # 应返回 false（恢复完成后）
```

---

### 3.8 安全加固核查清单

| 序号 | 检查项 | 状态 | 责任人 |
|------|--------|------|-------|
| 1 | PostgreSQL 启用 SSL/TLS 连接 | [ ] | DBA |
| 2 | 敏感字段使用 pgcrypto 加密 | [ ] | DBA |
| 3 | Redis 禁用默认端口 / 启用 ACL | [ ] | 运维 |
| 4 | Redis TLS 证书已配置 | [ ] | 运维 |
| 5 | JWT 密钥使用 256-bit 随机值 | [ ] | 后端 |
| 6 | JWT 自动轮换已启用（30天周期） | [ ] | 后端 |
| 7 | 管理接口 IP 白名单已配置 | [ ] | 安全 |
| 8 | API 速率限制已启用 | [ ] | 后端 |
| 9 | 审计日志双写（DB+文件）已启用 | [ ] | 后端 |
| 10 | 数据库备份已配置并验证恢复 | [ ] | DBA |
| 11 | SSH 禁用密码登录 | [ ] | 运维 |
| 12 | 防火墙仅开放必要端口 | [ ] | 运维 |
| 13 | HTTPS 强制跳转 | [ ] | 运维 |
| 14 | 日志文件权限 640 / 属主 root:adm | [ ] | 运维 |
| 15 | 容器以非 root 用户运行 | [ ] | 运维 |

---

## 四、多租户隔离

### 4.1 隔离方案选型

| 方案 | 隔离级别 | 性能 | 复杂度 | 适用场景 |
|------|---------|------|-------|---------|
| **Schema-per-Tenant** | 高 | 高 | 中 | 企业客户、合规要求 |
| **Row-Level Security (RLS)** | 中 | 最高 | 低 | SaaS多租户、成本敏感 |
| **Database-per-Tenant** | 最高 | 最高 | 高 | 超大客户、严格合规 |
| **混合方案（推荐）** | 高 | 高 | 中高 | 灵活匹配不同客户需求 |

**推荐：** Schema-per-Tenant（默认） + RLS（轻量租户） 混合方案

---

### 4.2 Schema-per-Tenant 方案

#### 架构设计

```
┌─────────────────────────────────────────────────┐
│                  应用层 (FastAPI)                 │
│  TenantContext → 动态切换 search_path            │
├─────────────────────────────────────────────────┤
│              PostgreSQL                          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐  │
│  │ tenant_a   │ │ tenant_b   │ │ tenant_c   │  │
│  │ (schema)   │ │ (schema)   │ │ (schema)   │  │
│  │            │ │            │ │            │  │
│  │ users      │ │ users      │ │ users      │  │
│  │ documents  │ │ documents  │ │ documents  │  │
│  │ audit_logs │ │ audit_logs │ │ audit_logs │  │
│  └────────────┘ └────────────┘ └────────────┘  │
│  ┌────────────────────────────────────────┐     │
│  │ public (共享元数据)                      │     │
│  │ tenants │ plans │ system_config         │     │
│  └────────────────────────────────────────┘     │
└─────────────────────────────────────────────────┘
```

#### 租户上下文管理

```python
# app/core/tenant.py

from contextvars import ContextVar
from typing import Optional
from dataclasses import dataclass

# 使用 ContextVar 实现请求级别的租户隔离（async-safe）
current_tenant: ContextVar[Optional["TenantContext"]] = ContextVar("current_tenant", default=None)

@dataclass
class TenantContext:
    """租户上下文"""
    tenant_id: str
    tenant_name: str
    schema_name: str
    plan: str  # free / standard / enterprise
    max_users: int
    max_storage_gb: int
    features: list[str]

class TenantMiddleware:
    """从请求中提取租户信息的中间件"""

    async def __call__(self, request, call_next):
        # 方式1：从子域名提取（tenant-a.example.com）
        host = request.headers.get("host", "")
        tenant_subdomain = host.split(".")[0] if "." in host else None

        # 方式2：从 Header 提取（X-Tenant-ID）
        tenant_id = request.headers.get("X-Tenant-ID", tenant_subdomain)

        # 方式3：从 JWT Token 中提取
        if hasattr(request.state, "user") and request.state.user:
            tenant_id = request.state.user.get("tenant_id", tenant_id)

        if tenant_id:
            tenant = await get_tenant_context(tenant_id)
            if tenant:
                current_tenant.set(tenant)
                request.state.tenant = tenant

        response = await call_next(request)
        return response

async def get_tenant_context(tenant_id: str) -> Optional[TenantContext]:
    """从数据库加载租户信息（带缓存）"""
    cache_key = f"tenant:{tenant_id}"
    cached = await redis.get(cache_key)

    if cached:
        return TenantContext(**json.loads(cached))

    # 从 public schema 查询租户信息
    result = await db.execute(
        text("SELECT * FROM public.tenants WHERE id = :id AND status = 'active'"),
        {"id": tenant_id}
    )
    row = result.fetchone()

    if not row:
        return None

    ctx = TenantContext(
        tenant_id=row.id,
        tenant_name=row.name,
        schema_name=f"tenant_{row.id}",
        plan=row.plan,
        max_users=row.max_users,
        max_storage_gb=row.max_storage_gb,
        features=json.loads(row.features) if row.features else []
    )

    # 缓存 5 分钟
    await redis.setex(cache_key, 300, json.dumps(ctx.__dict__))
    return ctx
```

#### 动态 Schema 切换

```python
# app/database/tenant_session.py

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import current_tenant

async def get_tenant_db_session(base_session: AsyncSession) -> AsyncSession:
    """为当前请求设置正确的 search_path"""
    tenant = current_tenant.get()

    if tenant:
        # 设置当前连接的 search_path 到租户 schema
        await base_session.execute(
            text(f"SET search_path TO {tenant.schema_name}, public")
        )

    return base_session

# 在依赖注入中使用
async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        await get_tenant_db_session(session)
        yield session
```

#### 租户初始化（自动创建 Schema）

```python
# app/services/tenant_provisioning.py

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SCHEMA_TEMPLATE_SQL = """
-- 为新租户创建完整的 schema

CREATE SCHEMA IF NOT EXISTS tenant_{tenant_id};

-- 复制表结构到租户 schema
CREATE TABLE tenant_{tenant_id}.users (
    LIKE public.users_template INCLUDING ALL
);

CREATE TABLE tenant_{tenant_id}.documents (
    LIKE public.documents_template INCLUDING ALL
);

CREATE TABLE tenant_{tenant_id}.conversations (
    LIKE public.conversations_template INCLUDING ALL
);

CREATE TABLE tenant_{tenant_id}.audit_logs (
    LIKE public.audit_logs_template INCLUDING ALL
);

-- 授予应用账户权限
GRANT USAGE ON SCHEMA tenant_{tenant_id} TO legal_app;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA tenant_{tenant_id} TO legal_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA tenant_{tenant_id} TO legal_app;

-- 设置默认 search_path（租户用户连接时）
ALTER ROLE tenant_{tenant_id}_user SET search_path TO tenant_{tenant_id}, public;
"""

async def provision_tenant(db: AsyncSession, tenant_id: str, tenant_name: str):
    """初始化新租户的数据库 schema"""
    # 执行 schema 创建脚本
    sql = SCHEMA_TEMPLATE_SQL.format(tenant_id=tenant_id)
    await db.execute(text(sql))
    await db.commit()

    # 在 public.tenants 表注册租户
    await db.execute(text("""
        INSERT INTO public.tenants (id, name, schema_name, status, created_at)
        VALUES (:id, :name, :schema, 'active', NOW())
    """), {"id": tenant_id, "name": tenant_name, "schema": f"tenant_{tenant_id}"})
    await db.commit()
```

---

### 4.3 Row-Level Security (RLS)

#### 启用 RLS

```sql
-- 在共享表上启用 RLS（适用于轻量级多租户）

-- 1. 为 documents 表添加租户列
ALTER TABLE documents ADD COLUMN tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000';
CREATE INDEX idx_documents_tenant ON documents(tenant_id);

-- 2. 启用 RLS
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

-- 3. 创建策略：租户只能访问自己的数据
CREATE POLICY tenant_isolation ON documents
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- 4. 对其它表也启用 RLS
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON users
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON conversations
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- 5. 应用层在每次连接时设置租户 ID
-- SET app.current_tenant_id = '550e8400-e29b-41d4-a716-446655440000';
```

#### RLS 与 Schema 方案对比

| 维度 | Schema-per-Tenant | RLS |
|------|-------------------|-----|
| 隔离强度 | 高（物理隔离） | 中（逻辑隔离） |
| 运维复杂度 | 随租户数增长 | 固定 |
| 备份恢复 | 可按租户单独备份 | 需全库备份 |
| 性能 | 无额外开销 | 策略检查有微小开销 |
| 最大租户数 | 建议 <1,000 | 无限制 |
| 迁移难度 | 需动态DDL | 仅数据迁移 |

---

### 4.4 Milvus 分区隔离

```python
# app/rag/milvus_tenant.py

from pymilvus import Collection, Partition

class MilvusTenantManager:
    """Milvus 多租户分区管理器"""

    def __init__(self, milvus_client, collection_name: str = "legal_documents"):
        self.client = milvus_client
        self.collection = Collection(collection_name)
        self.collection.load()

    def _partition_name(self, tenant_id: str) -> str:
        """生成租户分区名（Milvus 分区名仅支持字母数字下划线）"""
        safe_id = tenant_id.replace("-", "_")
        return f"tenant_{safe_id}"

    def ensure_partition(self, tenant_id: str):
        """确保租户分区存在"""
        pname = self._partition_name(tenant_id)
        if not self.collection.has_partition(pname):
            self.collection.create_partition(pname)

    def insert_vectors(self, tenant_id: str, vectors: list, metadata: list[dict]):
        """向租户分区插入向量"""
        self.ensure_partition(tenant_id)
        pname = self._partition_name(tenant_id)

        self.collection.insert(
            data=[vectors, metadata],
            partition_name=pname
        )

    def search(self, tenant_id: str, query_vector: list, top_k: int = 10):
        """在租户分区内搜索（自动隔离）"""
        pname = self._partition_name(tenant_id)

        # 仅在指定分区内搜索
        results = self.collection.search(
            data=[query_vector],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            partition_names=[pname],  # 关键：限定分区
            output_fields=["doc_id", "title", "content"]
        )
        return results

    def delete_tenant_data(self, tenant_id: str):
        """删除租户所有向量数据"""
        pname = self._partition_name(tenant_id)
        if self.collection.has_partition(pname):
            self.collection.drop_partition(pname)

    def get_partition_stats(self, tenant_id: str) -> dict:
        """获取租户分区统计信息"""
        pname = self._partition_name(tenant_id)
        if not self.collection.has_partition(pname):
            return {"entity_count": 0}

        stats = self.collection.get_partition_stats(pname)
        return {
            "partition": pname,
            "entity_count": int(stats.get("row_count", 0))
        }
```

---

### 4.5 配额管理

#### 配额模型

```python
# app/models/quota.py

from dataclasses import dataclass
from enum import Enum

class QuotaType(str, Enum):
    DAILY_QUERIES = "daily_queries"          # 每日查询次数
    MONTHLY_QUERIES = "monthly_queries"      # 每月查询次数
    STORAGE_BYTES = "storage_bytes"          # 文档存储空间
    VECTOR_COUNT = "vector_count"            # 向量条目数
    CONCURRENT_REQUESTS = "concurrent"       # 并发请求数
    API_CALLS_PER_MINUTE = "api_rpm"         # 每分钟API调用

# 各套餐配额
QUOTA_LIMITS = {
    "free": {
        QuotaType.DAILY_QUERIES: 50,
        QuotaType.MONTHLY_QUERIES: 1000,
        QuotaType.STORAGE_BYTES: 500 * 1024 * 1024,       # 500 MB
        QuotaType.VECTOR_COUNT: 1000,
        QuotaType.CONCURRENT_REQUESTS: 2,
        QuotaType.API_CALLS_PER_MINUTE: 10,
    },
    "standard": {
        QuotaType.DAILY_QUERIES: 1000,
        QuotaType.MONTHLY_QUERIES: 30000,
        QuotaType.STORAGE_BYTES: 50 * 1024 * 1024 * 1024,  # 50 GB
        QuotaType.VECTOR_COUNT: 100000,
        QuotaType.CONCURRENT_REQUESTS: 10,
        QuotaType.API_CALLS_PER_MINUTE: 60,
    },
    "enterprise": {
        QuotaType.DAILY_QUERIES: 50000,
        QuotaType.MONTHLY_QUERIES: 1000000,
        QuotaType.STORAGE_BYTES: 500 * 1024 * 1024 * 1024, # 500 GB
        QuotaType.VECTOR_COUNT: 1000000,
        QuotaType.CONCURRENT_REQUESTS: 50,
        QuotaType.API_CALLS_PER_MINUTE: 300,
    }
}
```

#### 配额检查中间件

```python
# app/middleware/quota.py

import time
from fastapi import Request, HTTPException

class QuotaMiddleware:
    """租户配额检查中间件"""

    async def __call__(self, request: Request, call_next):
        tenant = getattr(request.state, "tenant", None)
        if not tenant:
            return await call_next(request)

        # 检查各项配额
        await self._check_daily_queries(tenant)
        await self._check_concurrent(tenant)
        await self._check_rate_limit(tenant)

        return await call_next(request)

    async def _check_daily_queries(self, tenant):
        """检查每日查询配额"""
        today = time.strftime("%Y-%m-%d")
        key = f"quota:{tenant.tenant_id}:queries:{today}"
        count = await redis.incr(key)

        if count == 1:
            # 第一次查询，设置过期时间到当天结束
            await redis.expireat(key, end_of_today())

        limit = QUOTA_LIMITS[tenant.plan][QuotaType.DAILY_QUERIES]
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Daily query limit exceeded ({count}/{limit})",
                headers={"X-Quota-Remaining": "0", "X-Quota-Limit": str(limit)}
            )

    async def _check_concurrent(self, tenant):
        """检查并发请求配额"""
        key = f"quota:{tenant.tenant_id}:concurrent"
        current = await redis.incr(key)

        if current == 1:
            await redis.expire(key, 60)  # 自动过期

        limit = QUOTA_LIMITS[tenant.plan][QuotaType.CONCURRENT_REQUESTS]
        if current > limit:
            await redis.decr(key)
            raise HTTPException(status_code=429, detail="Concurrent request limit exceeded")

        # 请求结束后减少计数（通过响应后回调）
        # 实际实现中应使用 finally 块确保释放

    async def _check_rate_limit(self, tenant):
        """检查每分钟速率限制（滑动窗口）"""
        key = f"quota:{tenant.tenant_id}:rpm"
        now = time.time()
        window_start = now - 60

        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        results = await pipe.execute()

        count = results[2]
        limit = QUOTA_LIMITS[tenant.plan][QuotaType.API_CALLS_PER_MINUTE]

        if count > limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

---

## 五、监控告警

### 5.1 监控架构总览

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│   FastAPI   │   │ PostgreSQL  │   │   Redis     │
│  (app)      │   │             │   │             │
│  /metrics   │   │ pg_exporter │   │  exporter   │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │                 │                 │
       ▼                 ▼                 ▼
┌─────────────────────────────────────────────────┐
│              Prometheus                          │
│  (采集间隔: 15s / 存储: 30天)                     │
└──────────────────────┬──────────────────────────┘
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
┌─────────────┐  ┌───────────┐  ┌───────────┐
│   Grafana   │  │ Alertmgr  │  │ Thanos    │
│  (可视化)    │  │ (告警)     │  │ (长期存储) │
└─────────────┘  └───────────┘  └───────────┘
```

---

### 5.2 Prometheus 指标

#### 应用层指标暴露

```python
# app/middleware/prometheus_metrics.py

from prometheus_client import (
    Counter, Histogram, Gauge, Info,
    generate_latest, CONTENT_TYPE_LATEST
)
from starlette.middleware.base import BaseHTTPMiddleware
import time

# ---- 指标定义 ----

# HTTP 请求计数器
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "tenant_id"]
)

# HTTP 请求延迟直方图
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
)

# RAG 检索延迟
RAG_RETRIEVAL_DURATION = Histogram(
    "rag_retrieval_duration_seconds",
    "RAG retrieval pipeline duration",
    ["stage"],  # embed, retrieve, rerank, generate
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# 活跃 WebSocket 连接数
WS_ACTIVE_CONNECTIONS = Gauge(
    "websocket_active_connections",
    "Number of active WebSocket connections"
)

# GPU 利用率
GPU_UTILIZATION = Gauge(
    "gpu_utilization_percent",
    "GPU utilization percentage",
    ["gpu_id", "metric"]  # metric: utilization, memory, temperature
)

# LLM Token 用量
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Total LLM tokens consumed",
    ["model", "direction"]  # direction: input, output
)

# 应用信息
APP_INFO = Info("app", "Application information")
APP_INFO.info({
    "version": "0.1.0",
    "environment": "production"
})


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Prometheus 指标采集中间件"""

    async def dispatch(self, request, call_next):
        # 跳过 metrics 端点自身
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.time()

        response = await call_next(request)

        duration = time.time() - start
        tenant_id = getattr(request.state, "tenant_id", "unknown")

        # 记录指标
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
            tenant_id=tenant_id
        ).inc()

        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            endpoint=request.url.path
        ).observe(duration)

        return response


# Metrics 端点
async def metrics_endpoint(request):
    from starlette.responses import Response
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
```

#### Prometheus 配置

```yaml
# prometheus.yml

global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s

# 告警规则文件
rule_files:
  - "alert_rules.yml"

# 告警管理器
alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093

# 采集目标
scrape_configs:
  # Prometheus 自身
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]

  # 应用服务
  - job_name: "legal-assistant-backend"
    metrics_path: "/metrics"
    scrape_interval: 15s
    static_configs:
      - targets:
          - "backend-1:8000"
          - "backend-2:8000"
          - "backend-3:8000"
        labels:
          service: "legal-assistant"

  # PostgreSQL
  - job_name: "postgresql"
    static_configs:
      - targets: ["postgres-exporter:9187"]
        labels:
          instance: "pg-primary"

  # Redis
  - job_name: "redis"
    static_configs:
      - targets:
          - "redis-exporter-1:9121"
          - "redis-exporter-2:9121"
          - "redis-exporter-3:9121"

  # Milvus
  - job_name: "milvus"
    static_configs:
      - targets:
          - "milvus-1:9091"
          - "milvus-2:9091"
          - "milvus-3:9091"

  # GPU 指标（NVIDIA DCGM Exporter）
  - job_name: "gpu"
    static_configs:
      - targets: ["dcgm-exporter:9400"]

  # 节点指标
  - job_name: "node"
    static_configs:
      - targets:
          - "node-exporter-1:9100"
          - "node-exporter-2:9100"
          - "node-exporter-3:9100"
```

---

### 5.3 Grafana 仪表板

#### 仪表板规划

| 仪表板名称 | 内容 | 刷新间隔 |
|-----------|------|---------|
| 系统总览 | CPU/内存/磁盘/网络，服务健康状态 | 30s |
| 应用性能 | QPS、延迟P50/P95/P99、错误率 | 15s |
| RAG 流水线 | 各阶段延迟、检索命中率、LLM Token用量 | 15s |
| 数据库 | 连接数、QPS、慢查询、缓存命中率、锁等待 | 30s |
| Redis | 命中率、内存使用、连接数、键数量 | 30s |
| Milvus | 查询延迟、索引大小、分区统计 | 30s |
| GPU | 利用率、显存、温度、功耗 | 15s |
| 多租户 | 各租户用量、配额使用率、Top-N 租户 | 1min |
| 安全审计 | 登录事件、异常IP、速率限制触发 | 1min |

#### 核心 Grafana 面板 JSON 示例

```json
{
  "dashboard": {
    "title": "Legal Assistant - 系统总览",
    "tags": ["legal-assistant", "production"],
    "timezone": "Asia/Shanghai",
    "panels": [
      {
        "title": "服务健康状态",
        "type": "stat",
        "targets": [
          {
            "expr": "up{job=~\"legal-assistant-backend|postgresql|redis|milvus\"}",
            "legendFormat": "{{job}}-{{instance}}"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "mappings": [
              {"options": {"0": {"text": "DOWN"}}, "type": "value"},
              {"options": {"1": {"text": "UP"}}, "type": "value"}
            ],
            "thresholds": {
              "steps": [
                {"color": "red", "value": null},
                {"color": "green", "value": 1}
              ]
            }
          }
        }
      },
      {
        "title": "API QPS (每秒请求数)",
        "type": "timeseries",
        "targets": [
          {
            "expr": "sum(rate(http_requests_total[1m])) by (method)",
            "legendFormat": "{{method}}"
          }
        ]
      },
      {
        "title": "API 延迟 P95",
        "type": "timeseries",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))",
            "legendFormat": "P95"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "s",
            "thresholds": {
              "steps": [
                {"color": "green", "value": null},
                {"color": "yellow", "value": 1.0},
                {"color": "red", "value": 5.0}
              ]
            }
          }
        }
      },
      {
        "title": "错误率 (5xx)",
        "type": "timeseries",
        "targets": [
          {
            "expr": "sum(rate(http_requests_total{status_code=~\"5..\"}[5m])) / sum(rate(http_requests_total[5m])) * 100",
            "legendFormat": "Error Rate %"
          }
        ]
      },
      {
        "title": "GPU 利用率",
        "type": "gauge",
        "targets": [
          {
            "expr": "DCGM_FI_DEV_GPU_UTIL{gpu_id=\"0\"}",
            "legendFormat": "GPU 0"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "percent",
            "max": 100,
            "min": 0
          }
        }
      }
    ]
  }
}
```

---

### 5.4 告警规则

```yaml
# alert_rules.yml

groups:
  # === 基础设施告警 ===
  - name: infrastructure
    rules:
      # 服务宕机
      - alert: ServiceDown
        expr: up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "服务 {{ $labels.job }} 宕机"
          description: "{{ $labels.instance }} 已持续不可达超过 1 分钟"

      # 节点 CPU 过高
      - alert: HighCPUUsage
        expr: 100 - (avg by(instance) (rate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100) > 85
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "节点 CPU 使用率 > 85%"
          description: "{{ $labels.instance }} CPU 使用率 {{ $value | printf \"%.1f\" }}%"

      # 节点内存过高
      - alert: HighMemoryUsage
        expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100 > 90
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "节点内存使用率 > 90%"

      # 磁盘空间不足
      - alert: DiskSpaceLow
        expr: (1 - node_filesystem_avail_bytes{fstype!~\"tmpfs|overlay\"} / node_filesystem_size_bytes{fstype!~\"tmpfs|overlay\"}) * 100 > 85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "磁盘使用率 > 85%"
          description: "{{ $labels.instance }}:{{ $labels.mountpoint }} 使用率 {{ $value | printf \"%.1f\" }}%"

      - alert: DiskSpaceCritical
        expr: (1 - node_filesystem_avail_bytes{fstype!~\"tmpfs|overlay\"} / node_filesystem_size_bytes{fstype!~\"tmpfs|overlay\"}) * 100 > 95
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "磁盘使用率 > 95%，紧急处理！"

  # === 应用层告警 ===
  - name: application
    rules:
      # API 高错误率
      - alert: HighErrorRate
        expr: sum(rate(http_requests_total{status_code=~\"5..\"}[5m])) / sum(rate(http_requests_total[5m])) > 0.05
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "API 错误率 > 5%"
          description: "过去 3 分钟错误率 {{ $value | printf \"%.2f\" }}%"

      # API 延迟过高
      - alert: HighLatency
        expr: histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le)) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "API P95 延迟 > 5秒"

      # RAG 流水线超时
      - alert: RAGPipelineSlow
        expr: histogram_quantile(0.95, sum(rate(rag_retrieval_duration_seconds_bucket{stage=\"generate\"}[5m])) by (le)) > 15
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "RAG 生成阶段 P95 延迟 > 15秒"

  # === 数据库告警 ===
  - name: database
    rules:
      # PostgreSQL 连接数过高
      - alert: PgHighConnections
        expr: pg_stat_activity_count > 400
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "PostgreSQL 连接数 > 400"

      # PostgreSQL 复制延迟
      - alert: PgReplicationLag
        expr: pg_replication_lag > 30
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "PostgreSQL 主从复制延迟 > 30秒"

      # Redis 内存使用率
      - alert: RedisMemoryHigh
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Redis 内存使用率 > 85%"

  # === GPU 告警 ===
  - name: gpu
    rules:
      - alert: GPUTemperatureHigh
        expr: DCGM_FI_DEV_GPU_TEMP > 85
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "GPU {{ $labels.gpu_id }} 温度 > 85°C"

      - alert: GPUMemoryHigh
        expr: DCGM_FI_DEV_MEM_USED / DCGM_FI_DEV_FB_FREE > 0.9
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "GPU {{ $labels.gpu_id }} 显存使用率 > 90%"
```

#### Alertmanager 配置

```yaml
# alertmanager.yml

global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'

  routes:
    # 严重告警 → 即时通知
    - match:
        severity: critical
      receiver: 'critical-alerts'
      repeat_interval: 1h

    # 普通告警 → 工作时间通知
    - match:
        severity: warning
      receiver: 'warning-alerts'
      repeat_interval: 4h

receivers:
  - name: 'default'
    webhook_configs:
      - url: 'http://webhook-relay:5001/alert'

  - name: 'critical-alerts'
    webhook_configs:
      - url: 'http://webhook-relay:5001/alert'
    # 企业微信/钉钉/飞书 Webhook
    # 短信通知（通过 Webhook 转发）

  - name: 'warning-alerts'
    webhook_configs:
      - url: 'http://webhook-relay:5001/alert'
    # 邮件通知
    email_configs:
      - to: 'ops-team@company.com'
        from: 'alertmanager@company.com'
        smarthost: 'smtp.company.com:587'
        auth_username: 'alertmanager@company.com'
        auth_password: 'smtp-password'

# 抑制规则
inhibit_rules:
  # 如果 critical 已触发，抑制同 alertname 的 warning
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname']
```

---

### 5.5 日志收集

#### 日志架构

```
[应用容器] --> [stdout/stderr] --> [Docker日志驱动]
                                              │
[文件日志]  --> [Promtail]  ──────────────────┤
                                              ▼
                                    ┌──────────────┐
                                    │    Loki      │
                                    │  (日志存储)   │
                                    └───────┬──────┘
                                            │
                                    ┌───────┴──────┐
                                    │   Grafana    │
                                    │ (日志查询)    │
                                    └──────────────┘
```

#### Docker 日志驱动配置

```json
// /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "5",
    "compress": "true",
    "labels": "service,tenant_id",
    "tag": "{{.Name}}/{{.ID}}"
  }
}
```

#### Promtail 配置（Loki 日志采集）

```yaml
# promtail.yml

server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push
    tenant_id: legal-assistant

scrape_configs:
  # 应用容器日志
  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      - source_labels: ['__meta_docker_container_name']
        target_label: 'container'
      - source_labels: ['__meta_docker_container_label_service']
        target_label: 'service'
    pipeline_stages:
      - json:
          expressions:
            level: level
            msg: msg
            tenant_id: tenant_id
            request_id: request_id
      - labels:
          level:
          tenant_id:
      - timestamp:
          source: timestamp
          format: RFC3339

  # 系统日志
  - job_name: system
    static_configs:
      - targets: [localhost]
        labels:
          job: syslog
          __path__: /var/log/syslog

  # Nginx 访问日志
  - job_name: nginx
    static_configs:
      - targets: [localhost]
        labels:
          job: nginx
          __path__: /var/log/nginx/access.log
    pipeline_stages:
      - regex:
          expression: '^(?P<remote_addr>\S+) - (?P<user>\S+) \[(?P<timestamp>[^\]]+)\] "(?P<method>\S+) (?P<path>\S+) \S+" (?P<status>\d+) (?P<bytes>\d+)'
      - labels:
          status:
      - timestamp:
          source: timestamp
          format: "02/Jan/2006:15:04:05 -0700"
```

#### 日志级别与保留策略

| 日志类型 | 级别 | 保留期 | 存储 |
|---------|------|-------|------|
| 应用日志 (INFO) | INFO | 30天 | Loki |
| 应用日志 (ERROR) | ERROR+ | 90天 | Loki + 文件 |
| 审计日志 | ALL | 3年 | PostgreSQL + 文件 |
| 访问日志 | ALL | 90天 | Loki + 文件 |
| 慢查询日志 | ALL | 90天 | PostgreSQL |
| GPU/推理日志 | INFO | 14天 | Loki |

---

## 六、AIGC合规备案

### 6.1 合规总览

根据《生成式人工智能服务管理暂行办法》（2023年8月15日施行）及相关法律法规，提供 AIGC 服务需完成以下合规工作：

```
┌──────────────────────────────────────────────────┐
│                 AIGC 合规四要素                    │
├──────────────────────────────────────────────────┤
│  1. 算法备案    →  国家网信办算法备案系统           │
│  2. 安全评估    →  内容安全 + 数据安全评估           │
│  3. 用户协议    →  服务协议 + 隐私政策               │
│  4. 数据保护    →  个人信息保护 + 数据出境评估        │
└──────────────────────────────────────────────────┘
```

---

### 6.2 算法备案流程

#### 备案要求

根据规定，具有以下特征的生成式AI服务需进行算法备案：

- 使用生成式AI技术（大语言模型、扩散模型等）
- 面向中国境内公众提供服务
- 具有舆论属性或社会动员能力

#### 备案流程

```
步骤1: 准备材料（2-4周）
  ├── 算法安全自评估报告
  ├── 算法公示信息
  ├── 拟公示信息说明
  ├── 算法备案系统账号注册
  └── 企业营业执照 / 法人证明

步骤2: 在线提交（国家网信办算法备案系统）
  ├── 填写算法基本信息
  ├── 上传安全评估报告
  ├── 填写数据信息来源说明
  └── 提交审核

步骤3: 审核阶段（20个工作日）
  ├── 形式审查（材料完整性）
  ├── 技术审查（算法安全性）
  └── 补充材料（如有要求）

步骤4: 获得备案编号
  └── 在产品中展示备案编号
```

#### 算法备案所需技术文档

```
1. 算法基本信息
   - 算法名称：法律智能辅助生成系统
   - 算法类型：生成合成类（文本生成）
   - 应用场景：法律文书生成、法律问答、合同审查
   - 算法机理：基于 RAG 架构，结合向量检索与大语言模型

2. 训练数据说明
   - 数据来源：公开法律法规、裁判文书、合同模板
   - 数据规模：XX 万条法律文本
   - 数据标注方式：人工审核 + 自动化清洗
   - 数据质量控制：三级审核机制

3. 模型信息
   - 基座模型：DeepSeek / Qwen（如使用本地部署）
   - 微调数据：法律领域专业语料
   - 推理框架：vLLM / HuggingFace Transformers

4. 安全机制
   - 内容安全：输入输出双重审核
   - 敏感信息过滤：个人信息脱敏
   - 生成内容标识：AI生成水印
```

---

### 6.3 内容安全审核

#### 内容安全架构

```
[用户输入] ──→ [输入审核] ──→ [RAG检索] ──→ [LLM生成] ──→ [输出审核] ──→ [用户]
                   │                                        │
                   ▼                                        ▼
            ┌──────────────┐                        ┌──────────────┐
            │  关键词过滤   │                        │  关键词过滤   │
            │  +分类模型   │                        │  +分类模型   │
            │  +LLM审核    │                        │  +LLM审核    │
            └──────────────┘                        └──────────────┘
```

#### 输入输出审核实现

```python
# app/services/content_safety.py

import re
from enum import Enum
from typing import Optional
from dataclasses import dataclass

class ContentRiskLevel(str, Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"

@dataclass
class SafetyResult:
    passed: bool
    risk_level: ContentRiskLevel
    categories: list[str]  # 触发的风险类别
    details: str
    original_text: str

# 敏感词库（示例，实际需加载完整词库）
SENSITIVE_PATTERNS = {
    "violence": [r"暴力", r"杀害", r"爆炸"],
    "politics": [r"政治敏感词1", r"政治敏感词2"],  # 需根据实际政策维护
    "illegal_advice": [r"如何伪造", r"如何逃避法律"],
    "personal_info": [r"\d{17}[\dXx]", r"1[3-9]\d{9}"],  # 身份证、手机号
}

class ContentSafetyChecker:
    """内容安全审核器"""

    def __init__(self):
        self.sensitive_patterns = self._compile_patterns()

    def _compile_patterns(self) -> dict:
        compiled = {}
        for category, patterns in SENSITIVE_PATTERNS.items():
            compiled[category] = [re.compile(p) for p in patterns]
        return compiled

    async def check_input(self, text: str) -> SafetyResult:
        """审核用户输入"""
        # 第一层：关键词匹配
        triggered = self._keyword_check(text)

        # 第二层：分类模型（如有部署）
        model_result = await self._model_check(text)
        if model_result:
            triggered.extend(model_result)

        # 综合判定
        if "blocked" in triggered:
            return SafetyResult(
                passed=False,
                risk_level=ContentRiskLevel.BLOCKED,
                categories=triggered,
                details="输入包含违规内容",
                original_text=text
            )

        if triggered:
            return SafetyResult(
                passed=True,  # 低风险可通过，但记录
                risk_level=ContentRiskLevel.MEDIUM,
                categories=triggered,
                details=f"输入包含敏感类别: {', '.join(triggered)}",
                original_text=text
            )

        return SafetyResult(
            passed=True,
            risk_level=ContentRiskLevel.SAFE,
            categories=[],
            details="输入安全",
            original_text=text
        )

    async def check_output(self, text: str) -> SafetyResult:
        """审核模型输出"""
        # 检查是否包含个人信息泄露
        info_leak = self._check_info_leak(text)

        # 检查生成内容合规性
        content_issues = self._keyword_check(text)

        # 检查是否包含虚假法律建议
        false_advice = await self._check_false_legal_advice(text)

        all_issues = info_leak + content_issues + false_advice

        if all_issues:
            return SafetyResult(
                passed=False,
                risk_level=ContentRiskLevel.HIGH,
                categories=all_issues,
                details="输出内容需要审核",
                original_text=text
            )

        return SafetyResult(
            passed=True,
            risk_level=ContentRiskLevel.SAFE,
            categories=[],
            details="输出安全",
            original_text=text
        )

    def _keyword_check(self, text: str) -> list[str]:
        triggered = []
        for category, patterns in self.sensitive_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    triggered.append(category)
                    break
        return triggered

    def _check_info_leak(self, text: str) -> list[str]:
        """检查输出是否泄露个人信息"""
        issues = []
        # 检查身份证模式
        if re.search(r"\d{17}[\dXx]", text):
            issues.append("personal_info_id_card")
        # 检查手机号模式
        if re.search(r"1[3-9]\d{9}", text):
            issues.append("personal_info_phone")
        return issues

    async def _model_check(self, text: str) -> Optional[list[str]]:
        """使用分类模型检测（可选）"""
        # 如有部署内容安全分类模型，在此调用
        return None

    async def _check_false_legal_advice(self, text: str) -> list[str]:
        """检查是否包含虚假法律建议"""
        # 使用 LLM-as-Judge 或规则检查
        return []


# 全局单例
safety_checker = ContentSafetyChecker()
```

#### 生成内容标识

```python
# 为 AI 生成内容添加标识（合规要求）

AI_GENERATED_WATERMARK = """
---
> **声明：** 以上内容由 AI 辅助生成，仅供参考，不构成正式法律意见。
> 具体法律问题请咨询专业律师。
> 生成时间：{timestamp} | 内容审核编号：{audit_id}
"""

def add_ai_watermark(content: str, audit_id: str) -> str:
    """为AI生成内容添加合规标识"""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return content + AI_GENERATED_WATERMARK.format(
        timestamp=timestamp,
        audit_id=audit_id
    )
```

---

### 6.4 用户协议模板

#### 服务协议核心条款

```markdown
# 法律智能辅助系统服务协议

## 第一条 服务说明
1.1 本系统是基于人工智能技术的法律辅助工具，提供法律信息检索、文书辅助生成、
    合同审查辅助等服务。
1.2 本系统生成的所有内容仅供参考，不构成正式法律意见。
1.3 用户应根据具体情况咨询持证律师获取专业法律建议。

## 第二条 免责声明
2.1 本系统不对生成内容的准确性、完整性、适用性做任何保证。
2.2 用户因使用本系统生成内容所做出的决策，由用户自行承担责任。
2.3 因技术限制，本系统可能存在以下情况：
    - 法律条文引用不完整或已失效
    - 案例分析存在偏差
    - 生成内容不符合最新法律变化
2.4 本系统不替代律师执业活动。

## 第三条 用户义务
3.1 用户应确保输入信息不违反法律法规。
3.2 用户不得利用本系统从事违法活动。
3.3 用户应对输出的使用承担审慎义务。
3.4 用户不得将本系统输出直接作为法律文书提交（除非经律师审核确认）。

## 第四条 数据处理
4.1 本系统对用户输入数据进行加密存储和处理。
4.2 用户数据不会用于训练模型（除非用户明确授权）。
4.3 用户有权请求删除其所有数据（被遗忘权）。
4.4 数据保留期限：服务终止后 30 天内完成数据清除。

## 第五条 AI 生成内容标识
5.1 本系统所有 AI 生成内容均会添加明确的 AI 生成标识。
5.2 用户不得移除或修改 AI 生成标识。

## 第六条 知识产权
6.1 本系统的算法、模型、软件归开发方所有。
6.2 用户输入的法律文档，知识产权归用户所有。
6.3 AI 辅助生成的内容，知识产权归属依法律规定处理。
```

#### 隐私政策核心条款

```markdown
# 隐私政策

## 收集的信息
- 账户信息：姓名、邮箱、手机号、所属单位
- 业务数据：上传的法律文档、对话记录
- 技术信息：IP地址、设备信息、浏览器类型

## 信息使用
- 提供和改善服务
- 生成法律分析结果
- 保障系统安全

## 信息存储与保护
- 数据存储于中国境内服务器
- 使用 AES-256 加密存储敏感数据
- 传输使用 TLS 1.3 加密
- 定期进行安全审计

## 信息共享
- 不会将用户数据分享给第三方
- 不会使用用户数据训练公共模型
- 法律法规要求的除外

## 用户权利
- 访问权：查阅个人信息
- 更正权：修正错误信息
- 删除权：请求删除数据
- 撤回权：撤回数据使用授权
```

---

### 6.5 数据保护合规

#### 合规检查清单

| 序号 | 检查项 | 法规依据 | 状态 |
|------|--------|---------|------|
| 1 | 个人信息收集已取得用户同意 | 《个人信息保护法》第13条 | [ ] |
| 2 | 隐私政策已公示且内容完整 | 《个人信息保护法》第17条 | [ ] |
| 3 | 数据存储于中国境内 | 《数据安全法》第31条 | [ ] |
| 4 | 敏感个人信息已单独同意 | 《个人信息保护法》第29条 | [ ] |
| 5 | 数据出境已通过安全评估 | 《数据出境安全评估办法》 | [ ] |
| 6 | 已指定个人信息保护负责人 | 《个人信息保护法》第52条 | [ ] |
| 7 | 已进行个人信息保护影响评估 | 《个人信息保护法》第55条 | [ ] |
| 8 | 用户删除权功能已实现 | 《个人信息保护法》第47条 | [ ] |
| 9 | 数据泄露应急预案已制定 | 《数据安全法》第29条 | [ ] |
| 10 | AI 生成内容已添加标识 | 《生成式AI管理暂行办法》第12条 | [ ] |

#### 数据分类分级

```
┌─────────────────────────────────────────────┐
│            数据分类分级矩阵                    │
├─────────────────────────────────────────────┤
│  级别   │ 类别     │ 加密要求  │ 访问控制     │
├─────────────────────────────────────────────┤
│  L4    │ 核心     │ AES-256  │ 最小授权+MFA  │
│  极高  │ 国家秘密  │ +硬件加密 │ 物理隔离      │
├─────────────────────────────────────────────┤
│  L3    │ 重要     │ AES-256  │ RBAC+审计     │
│  高    │ 案件材料  │ +字段加密 │ 按角色授权     │
├─────────────────────────────────────────────┤
│  L2    │ 内部     │ TLS传输  │ RBAC          │
│  中    │ 用户数据  │ 加密存储  │ 登录验证      │
├─────────────────────────────────────────────┤
│  L1    │ 公开     │ TLS传输  │ 无限制        │
│  低    │ 公开法规  │          │              │
└─────────────────────────────────────────────┘
```

---

## 七、附录：常用运维命令速查

### 7.1 Docker Compose 常用命令

```bash
# 启动所有服务
docker compose -f docker-compose.prod.yml up -d

# 查看服务状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f backend

# 重启单个服务
docker compose -f docker-compose.prod.yml restart backend

# 更新镜像并重建
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --force-recreate

# 停止所有服务
docker compose -f docker-compose.prod.yml down

# 进入容器内部
docker compose -f docker-compose.prod.yml exec backend bash
docker compose -f docker-compose.prod.yml exec postgres psql -U postgres -d legal_assistant
```

### 7.2 PostgreSQL 运维命令

```bash
# 查看连接数
psql -c "SELECT count(*) FROM pg_stat_activity;"

# 查看慢查询
psql -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query
         FROM pg_stat_activity
         WHERE (now() - pg_stat_activity.query_start) > interval '5 seconds'
         ORDER BY duration DESC;"

# 查看数据库大小
psql -c "SELECT pg_size_pretty(pg_database_size('legal_assistant'));"

# 查看表大小 Top 10
psql -c "SELECT schemaname, tablename,
         pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) AS size
         FROM pg_tables
         WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
         ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC
         LIMIT 10;"

# 查看锁等待
psql -c "SELECT blocked_locks.pid AS blocked_pid,
         blocking_locks.pid AS blocking_pid,
         blocked_activity.query AS blocked_statement
         FROM pg_catalog.pg_locks blocked_locks
         JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
         JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
         WHERE NOT blocked_locks.granted;"
```

### 7.3 Redis 运维命令

```bash
# 查看内存使用
redis-cli INFO memory | grep used_memory_human

# 查看连接数
redis-cli INFO clients | grep connected_clients

# 查看键数量
redis-cli DBSIZE

# 查看慢查询
redis-cli SLOWLOG GET 10

# 清理过期键统计
redis-cli INFO stats | grep expired_keys
```

### 7.4 Milvus 运维命令

```bash
# 查看集合列表
python -c "from pymilvus import connections, utility; connections.connect(); print(utility.list_collections())"

# 查看集合统计
python -c "
from pymilvus import connections, Collection
connections.connect()
c = Collection('legal_documents')
print(c.num_entities)
"

# 查看索引状态
python -c "
from pymilvus import connections, Collection
connections.connect()
c = Collection('legal_documents')
print(c.index())
"
```

### 7.5 紧急故障排查流程

```
服务不可用
  ├── 检查 Docker 容器状态
  │   └── docker compose ps → 查看异常容器
  ├── 检查端口监听
  │   └── ss -tlnp | grep :8000
  ├── 检查应用日志
  │   └── docker compose logs --tail=100 backend
  ├── 检查数据库连接
  │   └── psql -c "SELECT 1"
  ├── 检查 Redis 连接
  │   └── redis-cli ping
  ├── 检查 GPU 状态
  │   └── nvidia-smi
  └── 检查磁盘空间
      └── df -h

响应缓慢
  ├── 检查 CPU/内存使用
  │   └── htop / docker stats
  ├── 检查慢查询
  │   └── pg_stat_activity（见 7.2 节）
  ├── 检查 GPU 利用率
  │   └── nvidia-smi dmon -s u
  └── 检查 Milvus 查询延迟
      └── Grafana → Milvus 仪表板
```

---

## 文档版本历史

| 版本 | 日期 | 变更内容 | 作者 |
|------|------|---------|------|
| 1.0 | 2026-09-08 | 初始版本，完整企业部署指南 | 系统架构组 |

---

> **免责声明：** 本指南中的安全配置和合规建议仅供参考，实际部署前请咨询专业安全团队和法律顾问，确保符合您所在行业和地区的法律法规要求。
