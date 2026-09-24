#!/usr/bin/env python3
"""
简单的代理测试脚本
"""

import urllib.request
import ssl

# Bright Data 原生代理配置
proxy = 'http://brd-customer-hl_90ac3ad2-zone-serp_api2:2xvlhq5c1661@brd.superproxy.io:44445'

# 测试 URL
url = 'https://www.google.com/search?q=pizza'

print("=" * 60)
print("Bright Data 原生代理测试")
print("=" * 60)
print(f"\n代理配置: {proxy[:50]}...")
print(f"测试 URL: {url}")

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({'https': proxy, 'http': proxy}),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
)

try:
    print("\n正在请求...")
    response = opener.open(url, timeout=30)
    content = response.read().decode()
    print(f"✓ 请求成功!")
    print(f"  状态码: {response.status}")
    print(f"  响应长度: {len(content)} 字符")
    print(f"  前 200 字符: {content[:200]}")
except Exception as e:
    print(f"✗ 请求失败: {e}")

print("\n" + "=" * 60)
