"""
rebocap-osc-bridge: Rebocap → VRChat OSC Tracker Bridge
Streams full-body tracking data from Rebocap directly to VRChat
via OSC Trackers protocol (no SteamVR required).

VRChat OSC Trackers spec:
  https://docs.vrchat.com/docs/osc-trackers
Rebocap Python SDK:
  https://doc.rebocap.com/en_US/SDK/
"""

import argparse
import logging
import math
import sys
import time
import threading
from dataclasses import dataclass
from typing import Optional

from pythonosc import udp_client

from .config import (
    BridgeConfig,
    load_config,
    merge_cli_into_config,
    save_default_config,
    validate_config,
)

# ---------------------------------------------------------------------------
# Bone index constants (Rebocap SMPL order, 24 bones)
# ---------------------------------------------------------------------------
BONE_PELVIS       = 0
BONE_L_HIP        = 1
BONE_R_HIP        = 2
BONE_SPINE1       = 3
BONE_L_KNEE       = 4
BONE_R_KNEE       = 5
BONE_SPINE2       = 6
BONE_L_ANKLE      = 7
BONE_R_ANKLE      = 8
BONE_SPINE3       = 9
BONE_L_FOOT       = 10
BONE_R_FOOT       = 11
BONE_NECK         = 12
BONE_L_COLLAR     = 13
BONE_R_COLLAR     = 14
BONE_HEAD         = 15
BONE_L_SHOULDER   = 16
BONE_R_SHOULDER   = 17
BONE_L_ELBOW      = 18
BONE_R_ELBOW      = 19
BONE_L_WRIST      = 20
BONE_R_WRIST      = 21
BONE_L_HAND       = 22
BONE_R_HAND       = 23

EXPECTED_BONE_COUNT = 24

# ---------------------------------------------------------------------------
# VRChat OSC Tracker address mapping
# ---------------------------------------------------------------------------
TRACKER_ADDRESSES = {
    "hip":        "/tracking/trackers/1",
    "chest":      "/tracking/trackers/2",
    "left_foot":  "/tracking/trackers/3",
    "right_foot": "/tracking/trackers/4",
    "left_knee":  "/tracking/trackers/5",
    "right_knee": "/tracking/trackers/6",
    "left_elbow": "/tracking/trackers/7",
    "right_elbow":"/tracking/trackers/8",
}

HEAD_ADDRESS = "/tracking/trackers/head"

BONE_MAP = {
    "hip":         BONE_PELVIS,
    "chest":       BONE_SPINE3,
    "left_foot":   BONE_L_ANKLE,
    "right_foot":  BONE_R_ANKLE,
    "left_knee":   BONE_L_KNEE,
    "right_knee":  BONE_R_KNEE,
    "left_elbow":  BONE_L_ELBOW,
    "right_elbow": BONE_R_ELBOW,
}

# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def quat_to_euler_deg(x: float, y: float, z: float, w: float):
    """
    Convert a quaternion to VRChat OSC Tracker Euler angles in degrees.
    VRChat applies the returned Euler angles in Z, X, Y order.
    """
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Quaternion must contain finite values and have a non-zero length.")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm

    m00 = 1.0 - 2.0 * (y * y + z * z)
    m02 = 2.0 * (x * z + y * w)
    m10 = 2.0 * (x * y + z * w)
    m11 = 1.0 - 2.0 * (x * x + z * z)
    m12 = 2.0 * (y * z - x * w)
    m20 = 2.0 * (x * z - y * w)
    m22 = 1.0 - 2.0 * (x * x + y * y)

    euler_x = math.asin(max(-1.0, min(1.0, -m12)))
    if abs(abs(m12) - 1.0) < 1e-7:
        euler_y = math.atan2(-m20, m00)
        euler_z = 0.0
    else:
        euler_y = math.atan2(m02, m22)
        euler_z = math.atan2(m10, m11)

    def degrees_without_negative_zero(value: float) -> float:
        degrees = math.degrees(value)
        return 0.0 if abs(degrees) < 1e-10 else degrees

    return tuple(degrees_without_negative_zero(v) for v in (euler_x, euler_y, euler_z))


def rebocap_pos_to_vrchat(x: float, y: float, z: float):
    """
    SDK は CoordSpace.UNITY で初期化されているため変換不要。
    値をそのまま返す。
    """
    return (x, y, z)


def scale_position(x: float, y: float, z: float, height_m: float) -> tuple:
    """
    Feature #3: 身長キャリブレーション。
    VRChat OSC 仕様では 1.0 = 1 メートルの実寸が基準。
    Rebocap の出力も基本的に SI 単位系 (メートル) だが、
    ユーザーの実身長を使って正規化することでアバタースケールのズレを補正する。

    正規化係数 = 実身長 / 基準身長 (1.75m)
    """
    REF_HEIGHT_M = 1.75
    scale = height_m / REF_HEIGHT_M
    return (x * scale, y * scale, z * scale)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BonePose:
    px: float; py: float; pz: float   # position (meters)
    qx: float; qy: float; qz: float; qw: float  # quaternion


def parse_rebocap_frame(msg) -> Optional[list]:
    """
    Parse one Rebocap SDK pose message into a list of BonePose (24 entries).
    Returns None if the message is malformed or has an unexpected bone count.
    """
    try:
        bones = []
        for p in msg.poses:
            bone = BonePose(
                px=p.pos.x, py=p.pos.y, pz=p.pos.z,
                qx=p.rot.x, qy=p.rot.y, qz=p.rot.z, qw=p.rot.w,
            )
            if not all(math.isfinite(value) for value in bone.__dict__.values()):
                return None
            if bone.qx == bone.qy == bone.qz == bone.qw == 0:
                return None
            bones.append(bone)
        if len(bones) != EXPECTED_BONE_COUNT:
            logging.error(
                "Expected %d bones, got %d — SDK version mismatch?",
                EXPECTED_BONE_COUNT, len(bones),
            )
            return None
        return bones
    except (AttributeError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# OSC sender (VRChat)
# ---------------------------------------------------------------------------

class VRChatOSCSender:
    def __init__(self, ip: str, port: int, active_trackers: list,
                 height_m: float = 1.75, osc_debug: bool = False):
        self._client = udp_client.SimpleUDPClient(ip, port)
        self._active = set(active_trackers)
        self._height_m = height_m
        self._osc_debug = osc_debug
        logging.info("OSC → %s:%d | trackers: %s", ip, port, ", ".join(active_trackers))

    def _send(self, address: str, values: list) -> None:
        self._client.send_message(address, values)
        if self._osc_debug:
            # Feature #6: OSC Debug — 送信パケットをターミナルに表示
            formatted = ", ".join(f"{v:.4f}" if isinstance(v, float) else str(v)
                                  for v in values)
            logging.debug("OSC  %s  [%s]", address, formatted)

    def send_frame(self, bones: list) -> None:
        for slot, address in TRACKER_ADDRESSES.items():
            if slot not in self._active:
                continue
            bone_idx = BONE_MAP[slot]
            b = bones[bone_idx]

            vx, vy, vz = rebocap_pos_to_vrchat(b.px, b.py, b.pz)
            # Feature #3: 身長スケール補正
            vx, vy, vz = scale_position(vx, vy, vz, self._height_m)
            ex, ey, ez = quat_to_euler_deg(b.qx, b.qy, b.qz, b.qw)

            self._send(f"{address}/position", [vx, vy, vz])
            self._send(f"{address}/rotation", [ex, ey, ez])

    def send_head(self, bones: list) -> None:
        """
        Head pose 送信 (HMD/VR モード専用)。
        デスクトップモードで使うと全トラッカーが意図しない高さにシフトする。
        """
        b = bones[BONE_HEAD]
        vx, vy, vz = rebocap_pos_to_vrchat(b.px, b.py, b.pz)
        vx, vy, vz = scale_position(vx, vy, vz, self._height_m)
        ex, ey, ez = quat_to_euler_deg(b.qx, b.qy, b.qz, b.qw)
        self._send(f"{HEAD_ADDRESS}/position", [vx, vy, vz])
        self._send(f"{HEAD_ADDRESS}/rotation", [ex, ey, ez])


# ---------------------------------------------------------------------------
# Bridge main loop (コアロジック — GUI / CLI 両方から呼ぶ)
# ---------------------------------------------------------------------------

_RECONNECT_BASE_DELAY = 2.0
_RECONNECT_MAX_DELAY  = 30.0


class BridgeRunner:
    """
    ブリッジのコアロジックをスレッドで実行するクラス。
    GUI と CLI のどちらからでも使えるように状態を公開する。
    """

    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        self.status: str = "停止中"
        self.fps: float = 0.0
        self.is_running: bool = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self.is_running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="bridge-main"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.is_running = False
        self.status = "停止中"

    def _run(self) -> None:
        try:
            try:
                from rebocap_ws_sdk import RebocapWsSdk, CoordSpace  # type: ignore
            except ImportError:
                msg = (
                    "Rebocap Python SDK が見つかりません。\n"
                    "https://doc.rebocap.com/en_US/SDK/ からダウンロードし\n"
                    "'rebocap_ws_sdk' フォルダを Python から import できる場所に配置してください。"
                )
                logging.error(msg)
                self.status = "エラー: SDK が見つかりません"
                return

            cfg = self.cfg
            osc = VRChatOSCSender(
                cfg.osc_ip, cfg.osc_port, cfg.trackers,
                height_m=cfg.height_m,
                osc_debug=cfg.osc_debug,
            )

            frame_lock = threading.Lock()
            last_frame_count = 0
            reconnect_event = threading.Event()

            def on_pose(msg):
                nonlocal last_frame_count
                bones = parse_rebocap_frame(msg)
                if bones is None:
                    logging.warning("不正なポーズメッセージを受信しました。スキップします。")
                    return
                osc.send_frame(bones)
                if cfg.send_head:
                    osc.send_head(bones)
                with frame_lock:
                    last_frame_count += 1

            def on_close(code, reason):
                logging.warning("Rebocap WebSocket 切断: %s %s", code, reason)
                self.status = "切断 - 再接続中..."
                reconnect_event.set()

            def connect(sdk) -> bool:
                self.status = f"接続中... {cfg.rebocap_host}:{cfg.rebocap_port}"
                logging.info("Rebocap に接続中: ws://%s:%d ...",
                             cfg.rebocap_host, cfg.rebocap_port)
                try:
                    ret = sdk.open(cfg.rebocap_host, cfg.rebocap_port)
                except Exception as e:
                    logging.warning("Rebocap への接続で例外が発生しました: %s", e)
                    return False
                if ret != 0:
                    logging.warning(
                        "Rebocap への接続に失敗しました (error %d)。アプリが起動しているか確認してください。", ret
                    )
                    return False
                self.status = "ストリーミング中"
                logging.info("接続成功。VRChat へ送信中です。 (Ctrl+C で停止)")
                return True

            def new_sdk():
                sdk = RebocapWsSdk(CoordSpace.UNITY, use_global=True)
                sdk.set_pose_msg_callback(on_pose)
                sdk.set_exception_close_callback(on_close)
                return sdk

            sdk = None
            fps_timer = time.time()
            reconnect_delay = _RECONNECT_BASE_DELAY
            while not self._stop_event.is_set():
                if sdk is None:
                    sdk = new_sdk()
                    if connect(sdk):
                        reconnect_delay = _RECONNECT_BASE_DELAY
                        fps_timer = time.time()
                    else:
                        self.status = f"接続失敗 - {reconnect_delay:.0f} 秒後に再試行"
                        try:
                            sdk.close()
                        except Exception:
                            pass
                        sdk = None
                        self._stop_event.wait(timeout=reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, _RECONNECT_MAX_DELAY)
                    continue

                if reconnect_event.wait(timeout=5.0):
                    reconnect_event.clear()
                    try:
                        sdk.close()
                    except Exception:
                        pass
                    sdk = None
                    continue

                now = time.time()
                elapsed = now - fps_timer
                if elapsed >= 5.0:
                    with frame_lock:
                        count = last_frame_count
                        last_frame_count = 0
                    self.fps = count / elapsed if elapsed > 0 else 0.0
                    logging.info("%.1f frames/s → VRChat", self.fps)
                    fps_timer = now

        finally:
            if "sdk" in locals() and sdk is not None:
                try:
                    sdk.close()
                except Exception:
                    pass
            self.status = "停止中"
            self.is_running = False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _validate_trackers(values: list) -> list:
    all_slots = set(TRACKER_ADDRESSES.keys())
    invalid = [v for v in values if v not in all_slots]
    if invalid:
        logging.error(
            "不明なトラッカースロット: %s\n有効な選択肢: %s",
            ", ".join(invalid),
            ", ".join(sorted(all_slots)),
        )
        sys.exit(2)
    return values


def _setup_logging(cfg: BridgeConfig) -> None:
    """Feature #9: ログファイル出力対応のロギング設定。"""
    level = logging.DEBUG if cfg.verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list = [logging.StreamHandler()]

    # Feature #9: log_file が指定されていればファイルにも書き出す
    if cfg.log_file:
        try:
            fh = logging.FileHandler(cfg.log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
            handlers.append(fh)
            # basicConfig 呼び出し前に追加ハンドラを設定
        except OSError as e:
            # ファイル書き込み不可の場合は警告を出してコンソールのみ継続
            print(f"WARNING: ログファイルを開けません ({e})。コンソールのみに出力します。",
                  file=sys.stderr)

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt,
                        handlers=handlers)


def main():
    all_slots = list(TRACKER_ADDRESSES.keys())

    parser = argparse.ArgumentParser(
        description=(
            "rebocap-osc-bridge — Rebocap フルボディトラッキングを VRChat に送信\n"
            "OSC Trackers 使用 (SteamVR 不要)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rebocap-host", default=None,
                        help="Rebocap アプリのホスト (デフォルト: 127.0.0.1)")
    parser.add_argument("--rebocap-port", type=int, default=None,
                        help="Rebocap WebSocket ポート (デフォルト: 9527)")
    parser.add_argument("--osc-ip", default=None,
                        help="VRChat OSC 送信先 IP (デフォルト: 127.0.0.1)")
    parser.add_argument("--osc-port", type=int, default=None,
                        help="VRChat OSC ポート (デフォルト: 9000)")
    parser.add_argument(
        "--trackers", nargs="+", default=None,
        metavar="SLOT",
        help=(
            f"送信するトラッカースロット。選択肢: {all_slots}。"
            "デフォルト: hip left_foot right_foot"
        ),
    )
    parser.add_argument(
        "--send-head", action="store_true",
        help=(
            "head pose も送信。"
            "警告: HMD/VR モード専用。デスクトップモードでは使用しないこと。"
        ),
    )
    # Feature #3: 身長キャリブレーション
    parser.add_argument(
        "--height", type=float, default=None, metavar="METERS",
        help="実身長 (メートル)。スケール補正に使用 (デフォルト: config.toml の値または 1.70)",
    )
    # Feature #7: VMC
    parser.add_argument("--vmc", action="store_true",
                        help="現在利用不可: VMC 出力は安全な実装が完成するまで一時停止中")
    parser.add_argument("--vmc-ip", default=None,
                        help="VMC 送信先 IP (デフォルト: 127.0.0.1)")
    parser.add_argument("--vmc-port", type=int, default=None,
                        help="VMC ポート (デフォルト: 39539)")
    # Feature #8: OSCQuery
    parser.add_argument("--oscquery", action="store_true",
                        help="現在利用不可: OSCQuery は正しい送信側実装が完成するまで一時停止中")
    # Feature #6: OSC Debug
    parser.add_argument("--osc-debug", action="store_true",
                        help="送信中の OSC パケットをターミナルに表示")
    # Feature #9: ログファイル
    parser.add_argument("--log-file", default=None, metavar="PATH",
                        help="ログをファイルにも書き出す")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="デバッグログを有効化")
    # Feature #5: 設定ファイル
    parser.add_argument("--init-config", action="store_true",
                        help="デフォルトの config.toml をカレントディレクトリに生成して終了")
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="config.toml のパスを指定 (デフォルト: ./config.toml)")
    # Feature #1: GUI
    parser.add_argument("--gui", action="store_true",
                        help="GUI モードで起動")

    args = parser.parse_args()

    # --init-config: 設定ファイルを生成して終了
    if args.init_config:
        from pathlib import Path
        p = Path(args.config) if args.config else None
        out = save_default_config(p)
        print(f"config.toml を生成しました: {out}")
        sys.exit(0)

    # Feature #5: config.toml を読み込み、CLI 引数でオーバーライド
    from pathlib import Path
    config_path = Path(args.config) if args.config else None
    cfg = load_config(config_path)
    cfg = merge_cli_into_config(cfg, args)

    _setup_logging(cfg)

    # Feature #1: GUI モード
    if args.gui:
        from .gui import launch_gui
        launch_gui(cfg)
        return

    # トラッカー検証
    cfg.trackers = _validate_trackers(cfg.trackers)
    try:
        validate_config(cfg)
    except ValueError as e:
        parser.error(str(e))

    runner = BridgeRunner(cfg)
    runner.start()

    try:
        while runner.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("ブリッジを停止します。")
        runner.stop()


if __name__ == "__main__":
    main()
