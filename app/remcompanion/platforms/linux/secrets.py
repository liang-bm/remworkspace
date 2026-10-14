"""Linux Secret Service storage scoped to this install, never plaintext files."""
from __future__ import annotations
import os
import hashlib
from contextlib import closing
from pathlib import Path

class UnavailableSecretStore:
    """非 Windows 只读环境变量；绝不把凭据降级保存为明文。"""

    def get(self, provider_id: str) -> str | None:
        from ...secret_store import PROVIDER_ID_PATTERN
        if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ValueError("provider ID 非法")
        key = "REMCHAT_API_KEY_" + provider_id.upper().replace("-", "_")
        return os.environ.get(key, "").strip() or None

    def set(self, _provider_id: str, _secret: str) -> Path:
        raise OSError("此平台不保存 API Key；请设置 REMCHAT_API_KEY_<提供商ID大写，下划线替代连字符> 环境变量")

    def delete(self, _provider_id: str) -> None:
        return



def create_secret_store(root: Path):
    return SecretServiceStore(root)


class SecretServiceStore:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.scope=hashlib.sha256(os.fsencode(str(self.root))).hexdigest()

    def _attributes(self,provider_id):
        from ...secret_store import PROVIDER_ID_PATTERN
        if not isinstance(provider_id,str) or not PROVIDER_ID_PATTERN.fullmatch(provider_id):raise ValueError('provider ID 非法')
        return {'application':'remchat','installation':self.scope,'provider':provider_id}

    @staticmethod
    def _service():
        try:import secretstorage
        except ImportError:raise OSError('Linux 密钥库组件缺失；请使用完整 Linux 包或运行 Linux 环境安装脚本') from None
        return secretstorage

    def get(self,provider_id):
        attributes=self._attributes(provider_id)
        def environment():
            key='REMCHAT_API_KEY_'+provider_id.upper().replace('-','_')
            return os.environ.get(key,'').strip() or None
        try:service=self._service()
        except OSError:return environment()
        try:
            with closing(service.dbus_init()) as connection:
                collection=service.get_collection_by_alias(connection,'default')
                if collection.is_locked():raise OSError('系统密钥库已锁定，请先解锁')
                item=next(collection.search_items(attributes),None)
                if item is not None:
                    if item.is_locked():raise OSError('系统密钥已锁定，请先解锁')
                    return item.get_secret().decode('utf-8')
        except (service.exceptions.SecretServiceNotAvailableException,service.exceptions.ItemNotFoundException):
            pass
        except Exception:raise OSError('无法读取系统密钥库；请在系统“密码与密钥”中检查或解锁，不会改用明文') from None
        return environment()

    def set(self,provider_id,secret):
        attributes=self._attributes(provider_id);value=secret.strip()
        if not value or '\0' in value or len(value.encode('utf-8'))>16384:raise ValueError('API Key 为空或格式无效')
        try:
            service=self._service()
            with closing(service.dbus_init()) as connection:
                # Do not create/unlock collections silently from background
                # inference. The user's normal desktop login owns this vault.
                collection=service.get_collection_by_alias(connection,'default')
                if collection.is_locked():raise OSError('系统密钥库已锁定')
                collection.create_item('RemChat · '+provider_id,attributes,value.encode('utf-8'),replace=True)
        except Exception:raise OSError('密钥未保存；请在系统“密码与密钥”中建立或解锁默认密钥库，不会保存明文') from None

    def delete(self,provider_id):
        attributes=self._attributes(provider_id)
        service=self._service()
        try:
            with closing(service.dbus_init()) as connection:
                collection=service.get_collection_by_alias(connection,'default')
                if collection.is_locked():raise OSError('系统密钥库已锁定')
                for item in collection.search_items(attributes):item.delete()
        except service.exceptions.ItemNotFoundException:return
        except Exception:raise OSError('密钥未清除；请检查系统密钥库，不会修改其他安装或账户的密钥') from None


def storage_description() -> str:
    return "API Key 使用系统 Secret Service 密钥库；与 Windows 和其他安装隔离，不保存明文。也可使用环境变量。"
