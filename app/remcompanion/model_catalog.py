"""Explicit model discovery: one configured URL, no key forwarding or silent endpoint probing."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .model_providers import ProviderConfig


MAX_CATALOG_BYTES = 2 * 1024 * 1024


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential to another host, or downgrade HTTPS.
        return None


def fetch_model_ids(provider: ProviderConfig, api_key: str | None = None) -> tuple[tuple[str, ...], float]:
    """GET /models is discovery, not a benchmark or proof of model quality."""
    provider.ensure_chat_supported()
    headers = {"Accept": "application/json"}
    if provider.requires_api_key:
        if not api_key:
            raise ValueError("请先填写 API Key，再获取模型列表")
        headers["Authorization"] = f"Bearer {api_key}"
    url = provider.base_url.rstrip("/") + provider.models_path
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.monotonic()
    opener = urllib.request.build_opener(_RejectRedirects)
    try:
        with opener.open(request, timeout=15) as response:
            body = response.read(MAX_CATALOG_BYTES + 1)
    except urllib.error.HTTPError as error:
        # Do not include server-controlled response bodies (which may echo credentials).
        raise RuntimeError(f"获取模型列表失败：HTTP {error.code}。请检查模型列表路径与认证设置。") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ConnectionError("无法连接模型列表接口。请检查地址、网络与代理设置。") from None
    if len(body) > MAX_CATALOG_BYTES:
        raise ValueError("模型列表响应过大，已停止读取")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("模型列表不是有效的 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("模型列表必须是 JSON 对象")
    rows = payload.get("data")
    if not isinstance(rows, list):
        rows = payload.get("models")
    if not isinstance(rows, list):
        raise ValueError("模型列表缺少 data 或 models 数组")
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("id") or row.get("slug")
        if isinstance(value, str) and value.strip() and len(value) <= 256 and not any(char.isspace() or ord(char) < 32 for char in value):
            ids.add(value)
    if not ids:
        raise ValueError("接口未返回可选模型；仍可手动填写模型名称")
    return tuple(sorted(ids)), time.monotonic() - started
