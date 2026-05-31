"""
config.py — config.toml の読み書きと CLI 引数とのマージ

優先順位: CLI 引数 > config.toml > ハードコードされたデフォルト値
設定ファイルの場所: カレントディレクトリの config.toml
"""

from __future__ import annotations

import logging
import math
import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Python 3.11+ は stdlib tomllib、それ以前は tomli (pip install tomli)
try:
    import tomllib  # type: ignore
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

CONFIG_FILENAME = "config.toml"

DEFAULT_CONFIG_CONTENT = """\
# rebocap-osc-bridge 設定ファイル
# このファイルを編集して設定を保存してください。
# CLI 引数が指定された場合は CLI 引数が優先されます。

[rebocap]
host = "127.0.0.1"      # Rebocap アプリの IP アドレス
port = 9527             # Rebocap WebSocket ポート

[vrchat]
osc_ip   = "127.0.0.1" # VRChat OSC 送信先 IP
osc_port = 9000         # VRChat OSC ポート

[tracking]
# 使用するトラッカースロット
# 選択肢: hip, chest, left_foot, right_foot,
#         left_knee, right_knee, left_elbow, right_elbow
trackers  = ["hip", "left_foot", "right_foot"]
send_head = false       # true にすると head も送信 (HMD/VR モード専用)
height_m  = 1.70        # 身長 (メートル)。スケール補正に使用。

[output]
# VMC と OSCQuery は安全な実装が完成するまで利用できません。
vmc_enabled = false
vmc_ip      = "127.0.0.1"
vmc_port    = 39539

oscquery_enabled = false
oscquery_port    = 9001

[logging]
verbose  = false        # true にするとデバッグログを表示
log_file = ""           # ファイルパスを指定するとログをファイルにも書き出す
osc_debug = false       # true にすると送信中の OSC パケットをターミナルに表示
"""


@dataclass
class BridgeConfig:
    # Rebocap
    rebocap_host: str = "127.0.0.1"
    rebocap_port: int = 9527
    # VRChat
    osc_ip: str = "127.0.0.1"
    osc_port: int = 9000
    # Tracking
    trackers: List[str] = field(default_factory=lambda: ["hip", "left_foot", "right_foot"])
    send_head: bool = False
    height_m: float = 1.70
    # VMC
    vmc_enabled: bool = False
    vmc_ip: str = "127.0.0.1"
    vmc_port: int = 39539
    # OSCQuery
    oscquery_enabled: bool = False
    oscquery_port: int = 9001
    # Logging
    verbose: bool = False
    log_file: str = ""
    osc_debug: bool = False


def _config_path() -> Path:
    return Path(os.getcwd()) / CONFIG_FILENAME


def load_config(path: Optional[Path] = None) -> BridgeConfig:
    """
    config.toml を読み込んで BridgeConfig を返す。
    ファイルが存在しない場合はデフォルト値を返す（エラーにしない）。
    """
    cfg = BridgeConfig()
    p = path or _config_path()

    if not p.exists():
        return cfg

    if tomllib is None:
        logging.warning(
            "config.toml が見つかりましたが TOML パーサーがありません。\n"
            "  pip install tomli   (Python 3.9/3.10)\n"
            "  Python 3.11+ では標準 tomllib が使われます。\n"
            "デフォルト設定で起動します。"
        )
        return cfg

    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        logging.warning("config.toml の読み込みに失敗しました: %s", e)
        return cfg

    rb = data.get("rebocap", {})
    cfg.rebocap_host = rb.get("host", cfg.rebocap_host)
    cfg.rebocap_port = rb.get("port", cfg.rebocap_port)

    vrc = data.get("vrchat", {})
    cfg.osc_ip   = vrc.get("osc_ip",   cfg.osc_ip)
    cfg.osc_port = vrc.get("osc_port", cfg.osc_port)

    tr = data.get("tracking", {})
    cfg.trackers  = tr.get("trackers",  cfg.trackers)
    cfg.send_head = tr.get("send_head", cfg.send_head)
    cfg.height_m  = tr.get("height_m",  cfg.height_m)

    out = data.get("output", {})
    cfg.vmc_enabled      = out.get("vmc_enabled",      cfg.vmc_enabled)
    cfg.vmc_ip           = out.get("vmc_ip",           cfg.vmc_ip)
    cfg.vmc_port         = out.get("vmc_port",         cfg.vmc_port)
    cfg.oscquery_enabled = out.get("oscquery_enabled", cfg.oscquery_enabled)
    cfg.oscquery_port    = out.get("oscquery_port",    cfg.oscquery_port)

    lg = data.get("logging", {})
    cfg.verbose   = lg.get("verbose",   cfg.verbose)
    cfg.log_file  = lg.get("log_file",  cfg.log_file)
    cfg.osc_debug = lg.get("osc_debug", cfg.osc_debug)

    logging.debug("config.toml を読み込みました: %s", p)
    return cfg


def save_default_config(path: Optional[Path] = None) -> Path:
    """
    デフォルトの config.toml をカレントディレクトリに生成する。
    既にファイルが存在する場合は上書きしない。
    """
    p = path or _config_path()
    if p.exists():
        logging.info("config.toml はすでに存在します: %s", p)
        return p
    p.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")
    logging.info("config.toml を生成しました: %s", p)
    return p


def save_config(cfg: BridgeConfig, path: Optional[Path] = None) -> Path:
    """現在の設定を config.toml に保存する。"""
    p = path or _config_path()
    trackers = ", ".join(json.dumps(tracker, ensure_ascii=False) for tracker in cfg.trackers)
    content = f"""\
# rebocap-osc-bridge 設定ファイル
[rebocap]
host = {json.dumps(cfg.rebocap_host, ensure_ascii=False)}
port = {cfg.rebocap_port}

[vrchat]
osc_ip   = {json.dumps(cfg.osc_ip, ensure_ascii=False)}
osc_port = {cfg.osc_port}

[tracking]
trackers  = [{trackers}]
send_head = {str(cfg.send_head).lower()}
height_m  = {cfg.height_m}

[output]
# VMC と OSCQuery は安全な実装が完成するまで利用できません。
vmc_enabled      = false
vmc_ip           = {json.dumps(cfg.vmc_ip, ensure_ascii=False)}
vmc_port         = {cfg.vmc_port}
oscquery_enabled = false
oscquery_port    = {cfg.oscquery_port}

[logging]
verbose   = {str(cfg.verbose).lower()}
log_file  = {json.dumps(cfg.log_file, ensure_ascii=False)}
osc_debug = {str(cfg.osc_debug).lower()}
"""
    p.write_text(content, encoding="utf-8")
    logging.info("config.toml を保存しました: %s", p)
    return p


def validate_config(cfg: BridgeConfig) -> BridgeConfig:
    """利用者入力を検証し、安全に実行できない設定を拒否する。"""
    if not cfg.rebocap_host.strip():
        raise ValueError("Rebocap ホストを入力してください。")
    if not cfg.osc_ip.strip():
        raise ValueError("VRChat OSC IP を入力してください。")

    for label, port in (
        ("Rebocap ポート", cfg.rebocap_port),
        ("VRChat OSC ポート", cfg.osc_port),
        ("VMC ポート", cfg.vmc_port),
        ("OSCQuery ポート", cfg.oscquery_port),
    ):
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError(f"{label}は 1 から 65535 の範囲で指定してください。")

    if not math.isfinite(cfg.height_m) or cfg.height_m <= 0:
        raise ValueError("身長は 0 より大きい有限の値で指定してください。")
    if not cfg.trackers:
        raise ValueError("トラッカースロットを1つ以上選択してください。")
    if cfg.vmc_enabled:
        raise ValueError("VMC 出力は正しいローカル姿勢を取得できるまで一時停止中です。")
    if cfg.oscquery_enabled:
        raise ValueError("OSCQuery は送信側の自動検出を正しく実装するまで一時停止中です。")
    return cfg


def merge_cli_into_config(cfg: BridgeConfig, args) -> BridgeConfig:
    """
    argparse の Namespace を BridgeConfig にマージする。
    CLI で明示的に指定された値のみ上書きする（None / デフォルト値は無視）。
    """
    # 各フィールドを明示的にチェック（None = CLI未指定）
    if args.rebocap_host is not None:
        cfg.rebocap_host = args.rebocap_host
    if args.rebocap_port is not None:
        cfg.rebocap_port = args.rebocap_port
    if args.osc_ip is not None:
        cfg.osc_ip = args.osc_ip
    if args.osc_port is not None:
        cfg.osc_port = args.osc_port
    if args.trackers is not None:
        cfg.trackers = args.trackers
    if getattr(args, "send_head", False):
        cfg.send_head = True
    if getattr(args, "height", None) is not None:
        cfg.height_m = args.height
    if getattr(args, "vmc_ip", None) is not None:
        cfg.vmc_ip = args.vmc_ip
    if getattr(args, "vmc_port", None) is not None:
        cfg.vmc_port = args.vmc_port
    if getattr(args, "vmc", False):
        cfg.vmc_enabled = True
    if getattr(args, "oscquery", False):
        cfg.oscquery_enabled = True
    if getattr(args, "verbose", False):
        cfg.verbose = True
    if getattr(args, "log_file", None):
        cfg.log_file = args.log_file
    if getattr(args, "osc_debug", False):
        cfg.osc_debug = True
    return cfg
