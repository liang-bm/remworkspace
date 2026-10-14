"""Windows GPT-SoVITS single-GPU entry and read-only GPU competition warning."""

import subprocess


def training_command(config, *, stage: str, relative_script: str, args: list[str]) -> list[str]:
    if config.training.gpu_index.strip().isdigit():
        return [str(config.gpt_python), "-s", "-m", "remcompanion.single_gpu_train", stage, *args]
    return [str(config.gpt_python), "-s", relative_script, *args]


def warn_gpu_competitors(log) -> None:
    result = subprocess.run(
        ["tasklist"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lower = result.stdout.lower()
    competitors = [name for name in ("llama-server.exe", "ollama.exe") if name in lower]
    if competitors:
        log("[警告] 训练前建议关闭这些显存占用进程: " + ", ".join(competitors))
