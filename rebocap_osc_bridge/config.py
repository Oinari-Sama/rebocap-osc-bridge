"""
rebocap-osc-bridge: 設定管理モジュール

NOTE: Rebocap Python SDK v2 は sdk.open(port) のみを受け付け、
ホストアドレスの指定には対応していません。
そのため rebocap_host フィールドは設定ファイルへの記載・CLI 引数のパースは
継続するものの、実際の接続には使用されません。
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# BridgeConfig
# ---------------------------------------------------------------------------

@dataclass
class BridgeConfig:
    # --- Rebocap 接続 ---
    # NOTE: rebocap_host は SDK v2 で未使用。ドキュメント・GUI 互換のため残す。
    rebocap_host: str = "127.0.0.1"   # 未使用: SDK v2 は host 指定非対応
    rebocap_port: int = 7690           # SDK v2 デフォルトポート

    # --- VRChat OSC ---
    osc_ip: str = "127.0.0.1"
    osc_port: int = 9000
    osc_enabled: bool = False

    # --- トラッキング ---
    trackers: List[str] = field(
        default_factory=lambda: ["hip", "left_foot", "right_foot"]
    )
    send_head: bool = False
    align_head_position: bool = False
    height_m: float = 1.70

    # --- VMC (現在停止中) ---
    vmc_enabled: bool = False
    vmc_ip: str = "127.0.0.1"
    vmc_port: int = 39539

    # --- OSCQuery (現在停止中) ---
    oscquery_enabled: bool = False
    oscquery_port: int = 9001

    # --- ログ ---
    verbose: bool = False
    log_file: str = ""
    osc_debug: bool = False


# ---------------------------------------------------------------------------
# バリデーション
# ---------------------------------------------------------------------------

def validate_config(cfg: BridgeConfig) -> None:
    """設定値の妥当性を検査する。不正な値は ValueError を送出する。"""
    if isinstance(cfg.height_m, bool) or not isinstance(cfg.height_m, (int, float)):
        raise ValueError(f"height_m は数値でなければなりません: {cfg.height_m!r}")
    if not math.isfinite(cfg.height_m) or cfg.height_m <= 0:
        raise ValueError(f"height_m は正の有限値でなければなりません: {cfg.height_m!r}")
    for port_name, port_val in [
        ("rebocap_port", cfg.rebocap_port),
        ("osc_port", cfg.osc_port),
        ("vmc_port", cfg.vmc_port),
        ("oscquery_port", cfg.oscquery_port),
    ]:
        if isinstance(port_val, bool) or not isinstance(port_val, int):
            raise ValueError(f"{port_name} は整数でなければなりません: {port_val!r}")
        if not (1 <= port_val <= 65535):
            raise ValueError(f"{port_name} は 1..65535 の範囲でなければなりません: {port_val}")
    if cfg.vmc_enabled:
        raise ValueError(
            "VMC 出力は現在停止中です。vmc_enabled = false のままにしてください。"
        )
    if cfg.oscquery_enabled:
        raise ValueError(
            "OSCQuery は現在停止中です。oscquery_enabled = false のままにしてください。"
        )


# ---------------------------------------------------------------------------
# TOML I/O
# ---------------------------------------------------------------------------

_DEFAULT_TOML = """\
[rebocap]
# NOTE: host はSDK v2 では使用されません (sdk.open(port) のみ対応)
host = "127.0.0.1"
port = 7690

[vrchat]
osc_ip   = "127.0.0.1"
osc_port = 9000
osc_enabled = false       # 近似 FK を試す場合のみ明示的に true にする

[tracking]
trackers  = ["hip", "left_foot", "right_foot"]
send_head = false
align_head_position = false  # OSC tracking-space の平行移動補正
height_m  = 1.70        # 身長(m) — アバタースケール補正に使用

[output]
vmc_enabled = false     # 現在利用不可
vmc_ip      = "127.0.0.1"
vmc_port    = 39539
oscquery_enabled = false  # 現在利用不可
oscquery_port    = 9001

[logging]
verbose   = false
log_file  = ""          # ファイルパスを指定するとログをファイルにも保存
osc_debug = false       # OSC パケットをターミナルに表示
"""


def _try_import_tomllib():
    """Python 3.11+ は tomllib 標準搭載。3.9/3.10 は tomli を試みる。"""
    try:
        import tomllib  # type: ignore
        return tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
            return tomllib
        except ImportError:
            return None


def load_config(path: Optional[Path] = None) -> BridgeConfig:
    """
    config.toml を読み込んで BridgeConfig を返す。
    path が None / ファイルが存在しない場合はデフォルト値を返す。
    """
    cfg = BridgeConfig()

    if path is None:
        path = Path("config.toml")
    if not Path(path).exists():
        return cfg

    tomllib = _try_import_tomllib()
    if tomllib is None:
        return cfg  # TOML パーサーが使えない場合はデフォルト値で継続

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return cfg

    rb = data.get("rebocap", {})
    vc = data.get("vrchat", {})
    tr = data.get("tracking", {})
    ou = data.get("output", {})
    lo = data.get("logging", {})

    try:
        cfg.rebocap_host = str(rb.get("host", cfg.rebocap_host))
        cfg.rebocap_port = int(rb.get("port", cfg.rebocap_port))
        cfg.osc_ip = str(vc.get("osc_ip", cfg.osc_ip))
        cfg.osc_port = int(vc.get("osc_port", cfg.osc_port))
        cfg.osc_enabled = bool(vc.get("osc_enabled", cfg.osc_enabled))
        cfg.trackers = list(tr.get("trackers", cfg.trackers))
        cfg.send_head = bool(tr.get("send_head", cfg.send_head))
        cfg.align_head_position = bool(tr.get("align_head_position", cfg.align_head_position))
        cfg.height_m = float(tr.get("height_m", cfg.height_m))
        cfg.vmc_enabled = bool(ou.get("vmc_enabled", cfg.vmc_enabled))
        cfg.vmc_ip = str(ou.get("vmc_ip", cfg.vmc_ip))
        cfg.vmc_port = int(ou.get("vmc_port", cfg.vmc_port))
        cfg.oscquery_enabled = bool(ou.get("oscquery_enabled", cfg.oscquery_enabled))
        cfg.oscquery_port = int(ou.get("oscquery_port", cfg.oscquery_port))
        cfg.verbose = bool(lo.get("verbose", cfg.verbose))
        cfg.log_file = str(lo.get("log_file", cfg.log_file))
        cfg.osc_debug = bool(lo.get("osc_debug", cfg.osc_debug))
        validate_config(cfg)
    except (TypeError, ValueError) as e:
        logging.warning("config.toml の設定値が不正です。デフォルト値を使用します: %s", e)
        return BridgeConfig()

    return cfg


def save_config(cfg: BridgeConfig, path: Optional[Path] = None) -> Path:
    """BridgeConfig を config.toml 形式で書き出す。"""
    if path is None:
        path = Path("config.toml")
    path = Path(path)

    trackers_toml = ", ".join(json.dumps(t) for t in cfg.trackers)
    content = f"""\
[rebocap]
# NOTE: host はSDK v2 では使用されません (sdk.open(port) のみ対応)
host = {json.dumps(cfg.rebocap_host)}
port = {cfg.rebocap_port}

[vrchat]
osc_ip   = {json.dumps(cfg.osc_ip)}
osc_port = {cfg.osc_port}
osc_enabled = {"true" if cfg.osc_enabled else "false"}

[tracking]
trackers  = [{trackers_toml}]
send_head = {"true" if cfg.send_head else "false"}
align_head_position = {"true" if cfg.align_head_position else "false"}
height_m  = {cfg.height_m}

[output]
vmc_enabled = {"true" if cfg.vmc_enabled else "false"}
vmc_ip      = {json.dumps(cfg.vmc_ip)}
vmc_port    = {cfg.vmc_port}
oscquery_enabled = {"true" if cfg.oscquery_enabled else "false"}
oscquery_port    = {cfg.oscquery_port}

[logging]
verbose   = {"true" if cfg.verbose else "false"}
log_file  = {json.dumps(cfg.log_file)}
osc_debug = {"true" if cfg.osc_debug else "false"}
"""
    path.write_text(content, encoding="utf-8")
    return path


def save_default_config(path: Optional[Path] = None) -> Path:
    """
    デフォルトの config.toml を生成する。
    ファイルがすでに存在する場合は上書きしない。
    """
    if path is None:
        path = Path("config.toml")
    path = Path(path)
    if path.exists():
        return path
    path.write_text(_DEFAULT_TOML, encoding="utf-8")
    return path


def merge_cli_into_config(cfg: BridgeConfig, args) -> BridgeConfig:
    """
    argparse の Namespace を BridgeConfig にマージする。
    CLI で指定された値を config.toml の値より優先させる。
    """
    if getattr(args, "rebocap_host", None) is not None:
        cfg.rebocap_host = args.rebocap_host
    if getattr(args, "rebocap_port", None) is not None:
        cfg.rebocap_port = args.rebocap_port
    if getattr(args, "osc_ip", None) is not None:
        cfg.osc_ip = args.osc_ip
    if getattr(args, "osc_port", None) is not None:
        cfg.osc_port = args.osc_port
    if getattr(args, "enable_osc", False):
        cfg.osc_enabled = True
    if getattr(args, "trackers", None) is not None:
        cfg.trackers = args.trackers
    if getattr(args, "send_head", False):
        cfg.send_head = True
    if getattr(args, "align_head_position", False):
        cfg.align_head_position = True
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
    if getattr(args, "log_file", None) is not None:
        cfg.log_file = args.log_file
    if getattr(args, "osc_debug", False):
        cfg.osc_debug = True
    return cfg
