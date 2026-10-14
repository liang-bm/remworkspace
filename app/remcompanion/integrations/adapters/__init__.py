"""可导出的外部工具适配器；导出不等于安装或启用。"""

from .registry import adapter_catalog, export_adapter

__all__ = ["adapter_catalog", "export_adapter"]
