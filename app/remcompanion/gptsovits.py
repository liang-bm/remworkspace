"""GPT-SoVITS 官方数据预处理、训练与推理 API 适配层。"""

from __future__ import annotations

from pathlib import Path
import json
import os
import socket
import subprocess
import secrets
import threading

from .config import AppConfig
from .process import project_environment, run_command
from .platforms import native_module


def _require(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label}不存在: {path}")


def _pretrained_paths(config: AppConfig) -> dict[str, Path]:
    root = config.gpt_sovits_dir / "GPT_SoVITS" / "pretrained_models"
    return {
        "bert": root / "chinese-roberta-wwm-ext-large",
        "hubert": root / "chinese-hubert-base",
        "sv": root / "sv" / "pretrained_eres2netv2w24s4ep4.ckpt",
        "s1": root / "s1v3.ckpt",
        "s2g": root / "v2Pro" / "s2Gv2Pro.pth",
        "s2d": root / "v2Pro" / "s2Dv2Pro.pth",
    }


def _gpt_environment(config: AppConfig) -> dict[str, str]:
    env = project_environment(config.root)
    ffmpeg_dir = config.ffmpeg.parent
    env["PATH"] = str(ffmpeg_dir) + os.pathsep + env.get("PATH", "")
    # 官方预处理脚本同时使用两类顶层导入：
    # - ``text``、``feature_extractor``、``AR``、``module`` 位于 GPT_SoVITS；
    # - ``tools`` 位于仓库根目录。
    # 官方整合包通过其定制运行时隐式提供这些路径；普通 Windows venv
    # 不会自动加入它们，因此必须显式构造 PYTHONPATH。保留已有路径，
    # 以免覆盖控制中心或用户环境中的其他模块目录。
    python_paths = [
        str(config.gpt_sovits_dir / "GPT_SoVITS"),
        str(config.gpt_sovits_dir),
    ]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        python_paths.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    env["version"] = config.training.version
    env["is_half"] = str(config.training.use_fp16)
    env["_CUDA_VISIBLE_DEVICES"] = config.training.gpu_index
    env["CUDA_VISIBLE_DEVICES"] = config.training.gpu_index
    return env


def _run_gpt_script(
    config: AppConfig,
    relative_script: str,
    *,
    log,
    env_updates: dict[str, str] | None = None,
    args: list[str] | None = None,
) -> None:
    env = _gpt_environment(config)
    if env_updates:
        env.update(env_updates)
    run_command(
        [str(config.gpt_python), "-s", relative_script, *(args or [])],
        cwd=config.gpt_sovits_dir,
        log=log,
        env_updates=env,
    )


def _training_command(
    config: AppConfig,
    *,
    stage: str,
    relative_script: str,
    args: list[str],
) -> list[str]:
    """为训练阶段选择原生入口或 Windows 单卡兼容入口。

    PyTorch 官方 Windows CUDA 轮子可能编译 ``USE_GLOO=ON``，却没有注册
    Gloo 的 UV/TCP 传输设备。GPT-SoVITS 即使只有一张显卡也强制启动 DDP，
    因而会在创建进程组前失败。单卡本来不需要跨进程同步，所以 Windows
    单卡配置使用项目内的兼容包装器；多卡或非 Windows 仍走官方脚本。
    """

    return native_module("training").training_command(
        config, stage=stage, relative_script=relative_script, args=args)


def _run_training_script(
    config: AppConfig,
    *,
    stage: str,
    relative_script: str,
    args: list[str],
    log,
) -> None:
    command = _training_command(
        config,
        stage=stage,
        relative_script=relative_script,
        args=args,
    )
    if "-m" in command:
        log(f"[Windows 单卡] {stage} 使用无 Gloo 兼容入口")
    run_command(
        command,
        cwd=config.gpt_sovits_dir,
        log=log,
        env_updates=_gpt_environment(config),
    )


def _merge_parts(paths: list[Path], destination: Path, header: str | None = None) -> None:
    lines: list[str] = [header] if header else []
    for path in paths:
        _require(path, "预处理分片")
        content = path.read_text(encoding="utf-8").strip()
        if content:
            lines.extend(content.splitlines())
        path.unlink()
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manifest_audio_names(manifest: Path) -> set[str]:
    """读取官方清单中的音频基名，并在预处理前验证路径。

    GPT-SoVITS 的特征文件全部使用音频基名关联。如果不同目录存在同名音频，
    官方脚本会静默覆盖特征，因此这里必须提前拒绝，不能等到训练阶段才得到
    难以理解的空数据集或错配样本。
    """

    names: set[str] = set()
    normalized_names: set[str] = set()
    missing_paths: list[str] = []
    duplicates: list[str] = []
    for line_number, raw_line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        fields = raw_line.split("|", 3)
        if len(fields) != 4:
            raise ValueError(f"训练清单第 {line_number} 行不是 path|speaker|language|text 格式")
        audio_path = Path(fields[0])
        if not audio_path.is_file():
            missing_paths.append(str(audio_path))
        name = audio_path.name
        normalized = name.casefold()
        if normalized in normalized_names:
            duplicates.append(name)
        names.add(name)
        normalized_names.add(normalized)

    if not names:
        raise ValueError(f"训练清单为空: {manifest}")
    if missing_paths:
        preview = ", ".join(missing_paths[:3])
        raise FileNotFoundError(f"训练清单中有 {len(missing_paths)} 个音频不存在，例如: {preview}")
    if duplicates:
        preview = ", ".join(duplicates[:3])
        raise ValueError(f"训练清单中有重复音频基名，会覆盖特征，例如: {preview}")
    return names


def _name_column(path: Path, *, delimiter: str, has_header: bool = False) -> set[str]:
    """读取 GPT-SoVITS 中间清单第一列；缺失文件按空产物处理。"""

    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    if has_header and lines:
        lines = lines[1:]
    return {
        line.split(delimiter, 1)[0]
        for line in lines
        if line.strip() and delimiter in line
    }


def _feature_stage_status(
    experiment: Path,
    step_name: str,
    expected_names: set[str],
) -> tuple[bool, str]:
    """确认阶段产物覆盖全部清单，而不是只相信子脚本的退出码。

    GPT-SoVITS 的预处理脚本会捕获单条音频异常、打印堆栈后仍以 0 退出。
    因此必须检查文件集合，否则空目录也会被错误写成“已完成”。
    """

    if step_name == "1a-text":
        actual = _name_column(experiment / "2-name2text.txt", delimiter="\t")
    elif step_name == "1b-hubert":
        hubert = {
            path.name[:-3]
            for path in (experiment / "4-cnhubert").glob("*.pt")
        }
        wav32k = {
            path.name
            for path in (experiment / "5-wav32k").glob("*.wav")
        }
        actual = hubert & wav32k
    elif step_name == "1b-speaker-vector":
        actual = {
            path.name[:-3]
            for path in (experiment / "7-sv_cn").glob("*.pt")
        }
    elif step_name == "1c-semantic":
        actual = _name_column(
            experiment / "6-name2semantic.tsv",
            delimiter="\t",
            has_header=True,
        )
    else:
        raise ValueError(f"未知预处理阶段: {step_name}")

    missing = sorted(expected_names - actual)
    if missing:
        preview = ", ".join(missing[:3])
        return False, f"缺少 {len(missing)}/{len(expected_names)} 个样本，例如: {preview}"
    return True, f"{len(expected_names)} 个样本完整"


def _preprocessing_common_environment(config: AppConfig, experiment: Path) -> dict[str, str]:
    """构造官方预处理环境。

    清单已经保存每个切片的绝对路径，因此 ``inp_wav_dir`` 必须为空。若同时
    设置切片根目录，官方脚本会对路径调用 ``basename``，从而丢失
    ``gentle/neutral`` 等子目录并把全部音频误判为不存在。
    """

    return {
        "inp_text": str(config.dataset_manifest),
        "inp_wav_dir": "",
        "exp_name": config.training.experiment_name,
        "opt_dir": str(experiment),
        "i_part": "0",
        "all_parts": "1",
        "_CUDA_VISIBLE_DEVICES": config.training.gpu_index,
        "is_half": str(config.training.use_fp16),
        "version": config.training.version,
    }


def prepare_official_features(config: AppConfig, log=print) -> None:
    """执行官方 1A/1B/1C 流水线；每个阶段可通过标记断点恢复。"""

    from .secure_resources import ResourceVault
    ResourceVault(config.root).require('training')
    required = {
        "GPT-SoVITS Python": config.gpt_python,
        "训练清单": config.dataset_manifest,
        "切片目录": config.dataset_segments,
        "FFmpeg": config.ffmpeg,
    }
    required.update({f"预训练模型 {key}": value for key, value in _pretrained_paths(config).items()})
    for label, path in required.items():
        _require(path, label)

    experiment = config.experiment_dir
    experiment.mkdir(parents=True, exist_ok=True)
    pretrained = _pretrained_paths(config)
    expected_names = _manifest_audio_names(config.dataset_manifest)
    common = _preprocessing_common_environment(config, experiment)

    steps: list[tuple[str, str, dict[str, str]]] = [
        (
            "1a-text",
            "GPT_SoVITS/prepare_datasets/1-get-text.py",
            {**common, "bert_pretrained_dir": str(pretrained["bert"])},
        ),
        (
            "1b-hubert",
            "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py",
            {**common, "cnhubert_base_dir": str(pretrained["hubert"])},
        ),
        (
            "1b-speaker-vector",
            "GPT_SoVITS/prepare_datasets/2-get-sv.py",
            {**common, "sv_path": str(pretrained["sv"])},
        ),
        (
            "1c-semantic",
            "GPT_SoVITS/prepare_datasets/3-get-semantic.py",
            {
                **common,
                "pretrained_s2G": str(pretrained["s2g"]),
                "s2config_path": "GPT_SoVITS/configs/s2v2Pro.json",
            },
        ),
    ]

    for step_name, script, environment in steps:
        marker = experiment / f".{step_name}.done"
        if marker.exists():
            complete, detail = _feature_stage_status(experiment, step_name, expected_names)
            if complete:
                log(f"[跳过] {step_name} 已完成（{detail}）")
                continue
            # 标记与实际产物不一致时，以磁盘产物为准。先删除错误标记，
            # 确保脚本或进程中断后下次仍会自动恢复。
            marker.unlink()
            log(f"[重做] {step_name} 的完成标记无效：{detail}")
        log(f"[开始] {step_name}")
        _run_gpt_script(config, script, log=log, env_updates=environment)
        if step_name == "1a-text":
            _merge_parts([experiment / "2-name2text-0.txt"], experiment / "2-name2text.txt")
        elif step_name == "1c-semantic":
            _merge_parts(
                [experiment / "6-name2semantic-0.tsv"],
                experiment / "6-name2semantic.tsv",
                header="item_name\tsemantic_audio",
            )
        complete, detail = _feature_stage_status(experiment, step_name, expected_names)
        if not complete:
            raise RuntimeError(f"{step_name} 子脚本退出但产物不完整：{detail}")
        marker.write_text("done\n", encoding="utf-8")
        log(f"[完成] {step_name}（{detail}）")


def _prepare_sovits_output_directories(config: AppConfig) -> Path:
    """创建官方 SoVITS 脚本不会主动创建的输出目录。

    ``s2_train.py`` 直到保存周期到达后才访问 ``logs_s2_<version>``，
    且直接把检查点移动进去。目录缺失会导致已经完成若干 epoch 后才失败，
    所以必须在训练启动前创建并由测试覆盖。
    """

    weights = config.root / "models" / "tts" / "rem" / "sovits"
    weights.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = config.experiment_dir / f"logs_s2_{config.training.version}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    return weights


def _write_sovits_config(config: AppConfig) -> Path:
    pretrained = _pretrained_paths(config)
    template = config.gpt_sovits_dir / "GPT_SoVITS" / "configs" / "s2v2Pro.json"
    with template.open("r", encoding="utf-8") as stream:
        data = json.load(stream)

    weights = _prepare_sovits_output_directories(config)
    data["train"].update(
        {
            "batch_size": config.training.sovits_batch_size,
            "epochs": config.training.sovits_epochs,
            "pretrained_s2G": str(pretrained["s2g"]),
            "pretrained_s2D": str(pretrained["s2d"]),
            "if_save_latest": True,
            "if_save_every_weights": True,
            "save_every_epoch": config.training.sovits_save_every_epoch,
            "gpu_numbers": config.training.gpu_index,
            "grad_ckpt": config.training.sovits_gradient_checkpointing,
            "fp16_run": config.training.use_fp16,
            "text_low_lr_rate": 0.4,
            "lora_rank": 32,
        }
    )
    data["model"]["version"] = config.training.version
    data["data"]["exp_dir"] = str(config.experiment_dir)
    data["s2_ckpt_dir"] = str(config.experiment_dir)
    data["save_weight_dir"] = str(weights)
    data["name"] = config.training.experiment_name
    data["version"] = config.training.version

    output = config.root / "tmp" / "rem-s2-v2pro.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _yaml_string(value: str | Path) -> str:
    # JSON 双引号字符串也是合法 YAML，且能正确处理 Windows 反斜杠。
    return json.dumps(str(value), ensure_ascii=False)


def _write_gpt_config(config: AppConfig) -> Path:
    pretrained = _pretrained_paths(config)
    weights = config.root / "models" / "tts" / "rem" / "gpt"
    weights.mkdir(parents=True, exist_ok=True)
    output_dir = config.experiment_dir / f"logs_s1_{config.training.version}"
    precision = "16-mixed" if config.training.use_fp16 else "32"
    content = f"""\
train:
  seed: 1234
  epochs: {config.training.gpt_epochs}
  batch_size: {config.training.gpt_batch_size}
  save_every_n_epoch: {config.training.gpt_save_every_epoch}
  precision: {precision}
  gradient_clip: 1.0
  if_save_latest: true
  if_save_every_weights: true
  if_dpo: {str(config.training.gpt_use_dpo).lower()}
  half_weights_save_dir: {_yaml_string(weights)}
  exp_name: {_yaml_string(config.training.experiment_name)}
optimizer:
  lr: 0.01
  lr_init: 0.00001
  lr_end: 0.0001
  warmup_steps: 2000
  decay_steps: 40000
data:
  max_eval_sample: 8
  max_sec: 54
  num_workers: 2
  pad_val: 1024
model:
  vocab_size: 1025
  phoneme_vocab_size: 732
  embedding_dim: 512
  hidden_dim: 512
  head: 16
  linear_units: 2048
  n_layer: 24
  dropout: 0
  EOS: 1024
  random_bert: 0
inference:
  top_k: 15
pretrained_s1: {_yaml_string(pretrained["s1"])}
train_semantic_path: {_yaml_string(config.experiment_dir / "6-name2semantic.tsv")}
train_phoneme_path: {_yaml_string(config.experiment_dir / "2-name2text.txt")}
output_dir: {_yaml_string(output_dir)}
"""
    output = config.root / "tmp" / "rem-s1-v2pro.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return output


def _warn_gpu_competitors(log) -> None:
    native_module("training").warn_gpu_competitors(log)


def train_models(config: AppConfig, log=print) -> None:
    """先生成官方特征，再依次训练 SoVITS 与 GPT。"""

    from .secure_resources import ResourceVault
    ResourceVault(config.root).require('training')
    _warn_gpu_competitors(log)
    prepare_official_features(config, log)
    sovits_marker = config.experiment_dir / ".train-sovits.done"
    if not sovits_marker.exists():
        sovits_config = _write_sovits_config(config)
        log("[开始] SoVITS v2Pro 微调")
        _run_training_script(
            config,
            stage="s2",
            relative_script="GPT_SoVITS/s2_train.py",
            log=log,
            args=["--config", str(sovits_config)],
        )
        _latest_weight(config.root / "models" / "tts" / "rem" / "sovits", ".pth")
        sovits_marker.write_text("done\n", encoding="utf-8")
        log("[完成] SoVITS 微调")
    else:
        log("[跳过] SoVITS 训练已有完成标记")

    gpt_marker = config.experiment_dir / ".train-gpt.done"
    if not gpt_marker.exists():
        gpt_config = _write_gpt_config(config)
        log("[开始] GPT 语义模型微调")
        _run_training_script(
            config,
            stage="s1",
            relative_script="GPT_SoVITS/s1_train.py",
            log=log,
            args=["--config_file", str(gpt_config)],
        )
        _latest_weight(config.root / "models" / "tts" / "rem" / "gpt", ".ckpt")
        gpt_marker.write_text("done\n", encoding="utf-8")
        log("[完成] GPT 语义模型微调")
    else:
        log("[跳过] GPT 训练已有完成标记")


def _latest_weight(directory: Path, suffix: str) -> Path:
    candidates = list(directory.glob(f"*{suffix}"))
    if not candidates:
        raise FileNotFoundError(f"{directory} 中没有 {suffix} 权重")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _write_tts_config(config: AppConfig, gpt_weight: Path, sovits_weight: Path, *, output: Path | None = None) -> Path:
    pretrained = _pretrained_paths(config)
    from .voice_setup import selected_voice_device
    device = selected_voice_device(config)
    if device == "cuda":
        probe = subprocess.run(
            [str(config.gpt_python), "-I", "-c",
             "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)"],
            capture_output=True, timeout=30, **_background_process_options(),
        )
        if probe.returncode:
            raise RuntimeError("CUDA 语音环境不可用，请重新安装并选择 CPU")
    content = f"""\
custom:
  bert_base_path: {_yaml_string(pretrained["bert"])}
  cnhuhbert_base_path: {_yaml_string(pretrained["hubert"])}
  device: {device}
  is_half: {str(device == "cuda").lower()}
  t2s_weights_path: {_yaml_string(gpt_weight)}
  version: v2Pro
  vits_weights_path: {_yaml_string(sovits_weight)}
"""
    output = output or config.root / "config" / "tts_infer_rem.yaml"
    output.write_text(content, encoding="utf-8")
    return output


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as client:
        client.settimeout(0.5)
        return client.connect_ex((host, port)) == 0


def _tts_api_python(config: AppConfig) -> Path:
    """Select the native background-service interpreter without changing training."""
    return native_module("processes").background_python(config.gpt_python)


def _background_process_options() -> dict[str, object]:
    """Native background launch options, isolated from shared TTS orchestration."""
    return native_module("processes").background_process_options()


def start_tts_api(config: AppConfig, log=print) -> int:
    from .secure_resources import ResourceVault
    vault=ResourceVault(config.root);vault.require('inference')
    if vault.enabled and config.services.tts_host not in ('127.0.0.1','localhost','::1'):
        raise PermissionError('受保护声线不能绑定外部网络')
    if _port_open(config.services.tts_host, config.services.tts_port):
        if vault.enabled:
            token=vault.store.get('protected-tts-session')
            if not token:raise PermissionError('此端口不是已授权的受保护TTS；不会借用未知服务')
            from .protected_tts import request as protected_request
            protected_request(config,token,timeout=3)
        log(f"[跳过] TTS API 已在 {config.services.tts_host}:{config.services.tts_port} 运行")
        return 0

    _require(config.fast_langdetect_model, "TTS 语言检测模型")
    lease=None;token=None
    if vault.enabled:
        original_gpt=vault.latest_weight('models/tts/rem/gpt','.ckpt')
        original_sovits=vault.latest_weight('models/tts/rem/sovits','.pth')
        lease=vault.lease([original_gpt,original_sovits])
        gpt_weight=lease.paths[original_gpt.relative_to(vault.root).as_posix()]
        sovits_weight=lease.paths[original_sovits.relative_to(vault.root).as_posix()]
        try:
            token=secrets.token_urlsafe(32);vault.store.set('protected-tts-session',token)
            infer_config=_write_tts_config(config,gpt_weight,sovits_weight,output=lease.directory/'tts-infer.yaml')
        except BaseException:lease.close();raise
    else:
        gpt_weight=_latest_weight(config.root/'models/tts/rem/gpt','.ckpt')
        sovits_weight=_latest_weight(config.root/'models/tts/rem/sovits','.pth')
        infer_config=_write_tts_config(config,gpt_weight,sovits_weight)
    log_file = config.root / "logs" / "tts-api.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    env = _gpt_environment(config)
    from .voice_setup import selected_voice_device
    env["is_half"] = str(selected_voice_device(config) == "cuda")
    language = config.root / "runtime" / "voice-language"
    if (language / "nltk_data").is_dir():
        env["NLTK_DATA"] = str(language / "nltk_data")
    if (language / "open_jtalk_dic_utf_8-1.11").is_dir():
        env["OPEN_JTALK_DICT_DIR"] = str(language / "open_jtalk_dic_utf_8-1.11")
    api_python = _tts_api_python(config)
    process_options = _background_process_options()
    try:
      with log_file.open("a", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [
                str(api_python),
                "-s",
                str(Path(__file__).with_name('tts_guard_entry.py')) if vault.enabled else "api_v2.py",
                "-a",
                config.services.tts_host,
                "-p",
                str(config.services.tts_port),
                "-c",
                str(infer_config),
            ],
            cwd=config.gpt_sovits_dir,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if vault.enabled else subprocess.DEVNULL,
            **process_options,
        )
        if vault.enabled:
            process.stdin.write(json.dumps({'token':token,'reference_directory':str(vault.directory/'runtime'),'model_lease_directory':str(lease.directory)}).encode('utf-8')+b'\n')
            process.stdin.close()
    except BaseException:
        if 'process' in locals() and process.poll() is None:process.terminate();process.wait(timeout=10)
        if lease:lease.close()
        raise
    state = config.root / "data" / "tts-api.pid"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(str(process.pid), encoding="utf-8")
    from .backend_processes import record_process_identity
    try:
        if record_process_identity(config.root, "tts-api", process.pid, api_python) is None:
            log("[提示] 未取得 TTS 进程身份；退出时不会仅按 PID 终止它")
    except (OSError, ValueError):
        log("[提示] 无法保存 TTS 进程身份；退出时保留该进程")
    if lease:
        def reap_private_resources():
            process.wait()
            try:lease.close()
            except OSError:log('[资源] 私有运行目录暂未释放；仍为当前用户专用目录')
            try:
                if vault.store.get('protected-tts-session')==token:vault.store.delete('protected-tts-session')
            except OSError:log('[资源] 已结束的TTS会话凭据暂未清除')
        threading.Thread(target=reap_private_resources,name='private-tts-resources',daemon=True).start()
    log(
        f"[启动] TTS API PID={process.pid}, 地址 "
        f"http://{config.services.tts_host}:{config.services.tts_port}"
    )
    return process.pid
