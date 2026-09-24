# -*- coding: utf-8 -*-
"""Benchmark BGE-M3 embedding throughput with different batch sizes / threads."""
import time

import torch

torch.set_num_threads(12)

from app.services.model_registry import ModelRegistry

model = ModelRegistry.get_embedding_model()

# warmup
model.encode(["测试文本第一条"] * 4, return_dense=True)

for batch in (32, 64, 128):
    texts = [
        "中华人民共和国民法典第一千二百五十四条 禁止从建筑物中抛掷物品。"
        "从建筑物中抛掷物品或者从建筑物上坠落的物品造成他人损害的，由侵权人依法承担侵权责任。"
        f"(样本{i})"
        for i in range(batch)
    ]
    t0 = time.time()
    out = model.encode(texts, return_dense=True)
    dt = time.time() - t0
    print(f"batch={batch}: {dt:.2f}s -> {batch/dt:.1f} texts/s")
