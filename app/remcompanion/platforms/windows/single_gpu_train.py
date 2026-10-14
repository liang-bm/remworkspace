"""Windows 单显卡 GPT-SoVITS 训练兼容入口。

GPT-SoVITS 的官方训练脚本即使只有一张显卡也会使用 DDP。部分 PyTorch
官方 Windows CUDA 轮子没有可用的 Gloo 传输设备，导致模型尚未开始训练
就报 ``unsupported gloo device``。单显卡不需要进程间同步，本模块仅将
DDP 外壳替换为透明单进程包装器，模型、数据、优化器和训练循环仍完全使用
当前下载的 GPT-SoVITS 官方实现。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import runpy
import sys
from typing import Any


def _single_process_spawn(
    function: Callable[..., Any],
    args: tuple[Any, ...] = (),
    nprocs: int = 1,
    join: bool = True,
    daemon: bool = False,
    start_method: str = "spawn",
) -> Any:
    """以当前进程直接执行官方 ``run(rank=0, ...)``。

    这些参数与 ``torch.multiprocessing.spawn`` 保持一致。若未来配置了
    多张可见显卡则明确拒绝，避免悄悄退化成错误的单卡训练。
    """

    del join, daemon, start_method
    if nprocs != 1:
        raise RuntimeError(
            f"Windows 兼容入口仅支持单显卡，但检测到 {nprocs} 张可见显卡。"
        )
    return function(0, *args)


def _install_single_process_distributed_shims(*, wrap_ddp: bool) -> None:
    """安装 rank=0/world_size=1 的最小分布式兼容层。"""

    import torch
    import torch.distributed as distributed

    # 不创建网络进程组；单进程集合通信均为恒等操作。
    distributed.init_process_group = lambda *args, **kwargs: None
    distributed.is_initialized = lambda: False
    distributed.get_rank = lambda *args, **kwargs: 0
    distributed.get_world_size = lambda *args, **kwargs: 1
    distributed.broadcast = lambda tensor, *args, **kwargs: tensor
    distributed.all_reduce = lambda tensor, *args, **kwargs: tensor

    def all_gather(outputs, tensor, *args, **kwargs):
        del args, kwargs
        if outputs:
            outputs[0].copy_(tensor)
        return outputs

    distributed.all_gather = all_gather

    if not wrap_ddp:
        return

    import torch.multiprocessing as multiprocessing
    import torch.nn.parallel as parallel

    class SingleDeviceDDP(torch.nn.Module):
        """保留官方代码依赖的 ``.module`` 接口，但不进行梯度通信。"""

        def __init__(self, module: torch.nn.Module, *args, **kwargs) -> None:
            super().__init__()
            del args, kwargs
            self.module = module

        def forward(self, *args, **kwargs):
            return self.module(*args, **kwargs)

    multiprocessing.spawn = _single_process_spawn
    parallel.DistributedDataParallel = SingleDeviceDDP


def _run_s2(repository: Path, forwarded_args: list[str]) -> None:
    _install_single_process_distributed_shims(wrap_ddp=True)
    script = repository / "GPT_SoVITS" / "s2_train.py"
    sys.argv = [str(script), *forwarded_args]
    runpy.run_path(str(script), run_name="__main__")


def _run_s1(repository: Path, forwarded_args: list[str]) -> None:
    _install_single_process_distributed_shims(wrap_ddp=False)

    # 官方脚本构造 DDPStrategy；单卡时让 Lightning 自动选择
    # SingleDeviceStrategy，从而完全避开 Gloo。
    import pytorch_lightning.strategies as strategies

    strategies.DDPStrategy = lambda *args, **kwargs: "auto"
    script = repository / "GPT_SoVITS" / "s1_train.py"
    sys.argv = [str(script), *forwarded_args]
    runpy.run_path(str(script), run_name="__main__")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"s1", "s2"}:
        raise SystemExit("用法: python -m remcompanion.single_gpu_train {s1|s2} [训练参数]")

    stage = sys.argv[1]
    forwarded_args = sys.argv[2:]
    # 训练适配器始终把工作目录设置为 GPT-SoVITS 仓库根目录。
    repository = Path.cwd().resolve()
    if not (repository / "GPT_SoVITS").is_dir():
        raise FileNotFoundError(f"当前目录不是 GPT-SoVITS 仓库: {repository}")

    if stage == "s2":
        _run_s2(repository, forwarded_args)
    else:
        _run_s1(repository, forwarded_args)


if __name__ == "__main__":
    main()
