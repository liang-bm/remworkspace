"""Linux GPT-SoVITS official training entry; no Windows single-GPU workaround."""


def training_command(config, *, stage: str, relative_script: str, args: list[str]) -> list[str]:
    return [str(config.gpt_python), "-s", relative_script, *args]


def warn_gpu_competitors(log) -> None:
    return None
