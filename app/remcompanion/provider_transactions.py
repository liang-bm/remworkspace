"""Failure-safe pairing of public endpoints and private credentials.

The lock covers only local snapshots/writes, never inference or network work.
Credentials are isolated before changing an endpoint. Recovery restores the
public origin first and its old secret last. Failed public recovery leaves no
key; failed secret cleanup keeps the new public origin, never restores the old.
"""

from threading import RLock


PROVIDER_CREDENTIAL_LOCK = RLock()


def active_provider_credentials(repository, secret_store):
    with PROVIDER_CREDENTIAL_LOCK:
        provider = repository.load().active_provider
        provider.ensure_chat_supported()
        key = secret_store.get(provider.provider_id) if provider.requires_api_key else None
        return provider, key


def save_provider_credentials(repository, secret_store, previous, updated, provider, pending_key):
    """Save public configuration and optionally replace a secret, without leaking errors."""
    with PROVIDER_CREDENTIAL_LOCK:
        if repository.load() != previous:
            raise ValueError("配置已在其他位置更改，请重新选择提供商后保存")
        if not (provider.requires_api_key and pending_key):
            repository.save(updated)
            return
        old_key = secret_store.get(provider.provider_id)
        try:
            secret_store.delete(provider.provider_id)
        except Exception:
            raise ValueError("密钥隔离失败，未更改配置；请检查凭据存储") from None
        try:
            repository.save(updated)
            secret_store.set(provider.provider_id, pending_key)
        except Exception:
            # A protector may have failed after replacing its file. Never put
            # the old origin back until any possible new-origin key is removed.
            try:
                secret_store.delete(provider.provider_id)
            except Exception:
                raise ValueError("保存未完成且密钥清理失败；配置未回滚，请检查凭据存储") from None
            try:
                repository.save(previous)
            except Exception:
                raise ValueError("配置恢复失败；原密钥未恢复，需重新输入密钥") from None
            if old_key:
                try:
                    secret_store.set(provider.provider_id, old_key)
                except Exception:
                    raise ValueError("原配置已恢复，但原密钥恢复失败；请重新输入密钥") from None
            raise ValueError("保存未完成，已恢复原配置与密钥") from None
