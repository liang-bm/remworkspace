"""Compatibility CLI for the Windows single-GPU training implementation."""

from .platforms import native_module


def main() -> None:
    native_module("single_gpu_train").main()


if __name__ == "__main__":
    main()
