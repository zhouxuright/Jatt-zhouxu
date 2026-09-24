"""跨层共享的常量（避免 core / models 之间的循环依赖）。"""

#: 默认租户 ID —— 系统初始化时创建，承接存量数据与单租户私有化部署。
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

#: 默认租户短代码
DEFAULT_TENANT_CODE = "default"
