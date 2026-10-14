"""Linux installers are isolated from Windows assets and dependency pins."""


def _not_validated(component: str) -> None:
    raise NotImplementedError(
        f"Linux {component}一键安装尚未完成验证；不会下载或安装 Windows 包。"
        "请使用 platforms/linux 的独立安装入口；本地聊天模型可在模型设置页安装，"
        "已配置的远程 API 聊天不受影响。")


def install_llama_release(**options) -> None:
    raise ValueError("Linux 不接受 Windows 的 release/asset ZIP 参数；请调用本地模型安装入口。")


def setup_gpt_environment(config, log=print) -> None:
    _not_validated("GPT-SoVITS GPU 环境")


def download_language_assets(config, log=print, progress=None) -> None:
    _not_validated("GPT-SoVITS 语言依赖")


def download_components(config, *, log=print, progress=None) -> None:
    _not_validated("TTS/本地模型组件")


def download_local_llm(config, *, log=print, progress=None) -> None:
    from .llama_install import download_local_llm as install
    install(config, log=log, progress=progress)


def close_active_downloads() -> None:
    from .download_processes import ACTIVE_DOWNLOADS
    ACTIVE_DOWNLOADS.close()
