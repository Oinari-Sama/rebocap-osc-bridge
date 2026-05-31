"""
rebocap-osc-bridge: Rebocap SDK v2 receive-layer preview
Receives and validates Rebocap pose frames for a future VRChat OSC bridge.

VRChat OSC Trackers spec:
  https://docs.vrchat.com/docs/osc-trackers
Rebocap Python SDK v2:
  https://doc.rebocap.com/en_US/SDK/

開発状況メモ:
  SDK 受信層 (RebocapFrame パース) は SDK v2 API に対応済み。
  Python SDK がボーン長を公開しないため、OSC Tracker 送信は近似 FK。
  誤送信防止のため --enable-osc を指定した場合のみ送信する。
"""

import argparse
import logging
import math
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

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

# Approximate SMPL-like rest offsets at 1.70 m height. The SDK v2 Python API
# exposes rotations but no skeleton offsets, so FK must use an explicit model.
_MODEL_HEIGHT_M = 1.70
_BONE_PARENTS = (
    -1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8,
    9, 9, 9, 12, 13, 14, 16, 17, 18, 19, 20, 21,
)
_REST_OFFSETS = (
    (0.00, 0.00, 0.00),
    (-0.09, -0.08, 0.00), (0.09, -0.08, 0.00), (0.00, 0.10, 0.00),
    (0.00, -0.42, 0.00), (0.00, -0.42, 0.00), (0.00, 0.11, 0.00),
    (0.00, -0.42, 0.00), (0.00, -0.42, 0.00), (0.00, 0.11, 0.00),
    (0.00, -0.05, 0.14), (0.00, -0.05, 0.14), (0.00, 0.16, 0.00),
    (-0.08, 0.10, 0.00), (0.08, 0.10, 0.00), (0.00, 0.12, 0.00),
    (-0.16, 0.00, 0.00), (0.16, 0.00, 0.00),
    (-0.27, 0.00, 0.00), (0.27, 0.00, 0.00),
    (-0.25, 0.00, 0.00), (0.25, 0.00, 0.00),
    (-0.10, 0.00, 0.00), (0.10, 0.00, 0.00),
)

# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def quat_to_euler_deg(x: float, y: float, z: float, w: float):
    """
    Convert a quaternion to VRChat OSC Tracker Euler angles in degrees.
    VRChat applies the returned Euler angles in Z, X, Y order.
    入力: (x, y, z, w) 順
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
    SDK は CoordinateType.UnityCoordinate で初期化されているため変換不要。
    値をそのまま返す。
    """
    return (x, y, z)


def scale_position(x: float, y: float, z: float, height_m: float) -> tuple:
    """
    身長キャリブレーション。
    VRChat OSC 仕様では 1.0 = 1 メートルの実寸が基準。
    Rebocap の出力も基本的に SI 単位系 (メートル) だが、
    ユーザーの実身長を使って正規化することでアバタースケールのズレを補正する。

    正規化係数 = 実身長 / 基準身長 (1.75m)
    """
    REF_HEIGHT_M = 1.75
    scale = height_m / REF_HEIGHT_M
    return (x * scale, y * scale, z * scale)


def _rotate_vector(q: "BoneRotation", vector: tuple) -> tuple:
    """Rotate a Vector3 by the internal named quaternion fields."""
    w, x, y, z = q.qw, q.qx, q.qy, q.qz
    vx, vy, vz = vector
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Quaternion must contain finite values and have a non-zero length.")
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def compute_fk_positions(frame: "RebocapFrame", height_m: float) -> List[Tuple[float, float, float]]:
    """Compute approximate world-space joint positions from global rotations."""
    scale = height_m / _MODEL_HEIGHT_M
    positions: List[Tuple[float, float, float]] = [frame.pelvis_position]
    for bone_index in range(1, EXPECTED_BONE_COUNT):
        parent = _BONE_PARENTS[bone_index]
        offset = tuple(value * scale for value in _REST_OFFSETS[bone_index])
        rotated = _rotate_vector(frame.bones[parent], offset)
        parent_pos = positions[parent]
        positions.append(tuple(parent_pos[i] + rotated[i] for i in range(3)))
    return positions


# ---------------------------------------------------------------------------
# Data structures (SDK v2 対応)
# ---------------------------------------------------------------------------

@dataclass
class BoneRotation:
    """
    1 本のボーンの回転データ。
    SDK v2 は各ボーンの position を提供しないため rotation のみ保持する。
    quaternion は内部で (w, x, y, z) の名前付きフィールドへ格納する。
    UnityCoordinate の実機出力配列は [x, y, z, w] 順。
    """
    qw: float
    qx: float
    qy: float
    qz: float


# 後方互換のエイリアス: テスト側が BonePose を参照する場合に備える
BonePose = BoneRotation


@dataclass
class RebocapFrame:
    """
    parse_rebocap_frame() が返す 1 フレーム分のデータ。

    pelvis_position: pelvis の 3D 位置 (SDK v2 の trans 引数)
    bones:           24 本のボーン回転 (SDK v2 の pose24 引数)
    static_index:    接地ボーンインデックス (-1 または 0..11)
    timestamp:       フレームタイムスタンプ (秒)
    """
    pelvis_position: Tuple[float, float, float]
    bones: List[BoneRotation]
    static_index: int
    timestamp: float


def parse_rebocap_frame(
    trans,
    pose24,
    static_index: int = -1,
    ts: float = 0.0,
) -> Optional[RebocapFrame]:
    """
    SDK v2 コールバック引数を検証して RebocapFrame に変換する。

    Parameters
    ----------
    trans        : シーケンス長 3 — pelvis の [x, y, z] 位置
    pose24       : シーケンス長 24 — 各要素は [x, y, z, w] quaternion
    static_index : 接地ボーンインデックス (-1 または 0..11)
    ts           : フレームタイムスタンプ (秒)

    Returns
    -------
    RebocapFrame  正常時
    None          入力が不正な場合 (bone 数不一致、NaN、ゼロ quaternion 等)
    """
    try:
        # --- trans 検証 ---
        if trans is None or len(trans) != 3:
            return None
        tx, ty, tz = float(trans[0]), float(trans[1]), float(trans[2])
        if not (math.isfinite(tx) and math.isfinite(ty) and math.isfinite(tz)):
            return None

        # --- pose24 検証 ---
        if pose24 is None or len(pose24) != EXPECTED_BONE_COUNT:
            if pose24 is not None:
                logging.error(
                    "Expected %d bones, got %d — SDK version mismatch?",
                    EXPECTED_BONE_COUNT, len(pose24),
                )
            return None

        bones: List[BoneRotation] = []
        for raw_q in pose24:
            if len(raw_q) != 4:
                return None
            # UnityCoordinate の実機出力順: [x, y, z, w]
            qx = float(raw_q[0])
            qy = float(raw_q[1])
            qz = float(raw_q[2])
            qw = float(raw_q[3])
            if not all(math.isfinite(v) for v in (qw, qx, qy, qz)):
                return None
            if qw == 0.0 and qx == 0.0 and qy == 0.0 and qz == 0.0:
                return None
            bones.append(BoneRotation(qw=qw, qx=qx, qy=qy, qz=qz))

        static_index_value = int(static_index)
        timestamp_value = float(ts)
        if static_index_value < -1 or static_index_value > 11:
            return None
        if not math.isfinite(timestamp_value):
            return None

        return RebocapFrame(
            pelvis_position=(tx, ty, tz),
            bones=bones,
            static_index=static_index_value,
            timestamp=timestamp_value,
        )

    except (AttributeError, IndexError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# OSC sender (VRChat)
# ---------------------------------------------------------------------------

class VRChatOSCSender:
    """
    VRChat OSC Tracker 送信クラス。

    SDK v2 がボーン長を公開しないため、明示的な近似人体モデルで FK を行う。
    誤送信を防ぐため、enabled=True の場合のみ OSC Tracker を送信する。
    """

    def __init__(self, ip: str, port: int, active_trackers: list,
                 enabled: bool = False,
                 height_m: float = 1.75, osc_debug: bool = False):
        self._client = udp_client.SimpleUDPClient(ip, port)
        self._active = tuple(dict.fromkeys(active_trackers))
        self._enabled = enabled
        self._height_m = height_m
        self._osc_debug = osc_debug

        # 最新の pelvis 値は診断にも利用できるよう保持する。
        self._last_pelvis_pos: Optional[Tuple[float, float, float]] = None
        self._last_pelvis_rot: Optional[Tuple[float, float, float]] = None

        if not enabled:
            logging.warning("VRChat OSC 送信は無効です。送信するには --enable-osc が必要です。")
        logging.info("OSC → %s:%d | trackers: %s", ip, port, ", ".join(active_trackers))

    def _send(self, address: str, values: list) -> None:
        self._client.send_message(address, values)
        if self._osc_debug:
            formatted = ", ".join(f"{v:.4f}" if isinstance(v, float) else str(v)
                                  for v in values)
            logging.debug("OSC  %s  [%s]", address, formatted)

    def send_frame(self, frame: RebocapFrame) -> None:
        """
        RebocapFrame から近似 FK を計算し、有効化時のみ OSC Tracker を送信する。
        """
        # pelvis 回転・位置を内部バッファに保持
        pelvis = frame.bones[BONE_PELVIS]
        px, py, pz = frame.pelvis_position
        vx, vy, vz = rebocap_pos_to_vrchat(px, py, pz)
        ex, ey, ez = quat_to_euler_deg(pelvis.qx, pelvis.qy, pelvis.qz, pelvis.qw)
        self._last_pelvis_pos = (vx, vy, vz)
        self._last_pelvis_rot = (ex, ey, ez)

        if not self._enabled:
            return

        positions = compute_fk_positions(frame, self._height_m)
        for slot in self._active:
            bone_index = BONE_MAP[slot]
            address = TRACKER_ADDRESSES[slot]
            position = list(positions[bone_index])
            bone = frame.bones[bone_index]
            rotation = list(quat_to_euler_deg(bone.qx, bone.qy, bone.qz, bone.qw))
            self._send(f"{address}/position", position)
            self._send(f"{address}/rotation", rotation)

        logging.debug(
            "pelvis pos=(%.3f, %.3f, %.3f) rot=(%.1f, %.1f, %.1f)",
            vx, vy, vz, ex, ey, ez,
        )

    def send_head(self, frame: RebocapFrame) -> None:
        """
        有効化時のみ head pose を送信する。
        """
        if not self._enabled:
            return
        positions = compute_fk_positions(frame, self._height_m)
        head = frame.bones[BONE_HEAD]
        self._send(f"{HEAD_ADDRESS}/position", list(positions[BONE_HEAD]))
        self._send(f"{HEAD_ADDRESS}/rotation",
                   list(quat_to_euler_deg(head.qx, head.qy, head.qz, head.qw)))

    def send_head_position(self, frame: RebocapFrame) -> None:
        """Send head position only so VRChat can translate OSC tracking space."""
        if not self._enabled:
            return
        positions = compute_fk_positions(frame, self._height_m)
        self._send(f"{HEAD_ADDRESS}/position", list(positions[BONE_HEAD]))


# ---------------------------------------------------------------------------
# Bridge main loop
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
        self._reconnect_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._reconnect_event.clear()
        self.is_running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="bridge-main"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._reconnect_event.set()
        self.is_running = False
        self.status = "停止中"

    def _run(self) -> None:
        try:
            try:
                import rebocap_ws_sdk  # type: ignore
                RebocapWsSdk = rebocap_ws_sdk.RebocapWsSdk
                CoordinateType = rebocap_ws_sdk.CoordinateType
            except (ImportError, AttributeError):
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
                enabled=cfg.osc_enabled,
                height_m=cfg.height_m,
                osc_debug=cfg.osc_debug,
            )

            frame_lock = threading.Lock()
            last_frame_count = 0
            def on_pose(sdk_instance, trans, pose24, static_index, ts):
                """SDK v2 コールバック署名: (sdk_instance, trans, pose24, static_index, ts)"""
                nonlocal last_frame_count
                frame = parse_rebocap_frame(trans, pose24, static_index, ts)
                if frame is None:
                    logging.warning("不正なポーズメッセージを受信しました。スキップします。")
                    return
                osc.send_frame(frame)
                if cfg.send_head:
                    osc.send_head(frame)
                elif cfg.align_head_position:
                    osc.send_head_position(frame)
                with frame_lock:
                    last_frame_count += 1

            def on_close(sdk_instance):
                """SDK v2 コールバック署名: (sdk_instance,)"""
                logging.warning("Rebocap WebSocket 切断")
                self.status = "切断 - 再接続中..."
                self._reconnect_event.set()

            def connect(sdk) -> bool:
                # NOTE: SDK v2 は sdk.open(port) のみ。ホスト指定は非対応。
                self.status = f"接続中... ポート:{cfg.rebocap_port}"
                logging.info(
                    "Rebocap に接続中: ポート %d (SDK v2 はホスト指定非対応) ...",
                    cfg.rebocap_port,
                )
                try:
                    ret = sdk.open(cfg.rebocap_port)
                except Exception as e:
                    logging.warning("Rebocap への接続で例外が発生しました: %s", e)
                    return False
                if ret != 0:
                    logging.warning(
                        "Rebocap への接続に失敗しました (error %d)。"
                        "アプリが起動しているか確認してください。", ret
                    )
                    return False
                self.status = "ストリーミング中"
                logging.info(
                    "接続成功。SDK受信中です。"
                    " (OSC 送信: %s / Ctrl+C で停止)",
                    "有効" if cfg.osc_enabled else "無効",
                )
                return True

            def new_sdk():
                # SDK v2 API: CoordinateType.UnityCoordinate, use_global_rotation=True
                sdk = RebocapWsSdk(
                    coordinate_type=CoordinateType.UnityCoordinate,
                    use_global_rotation=True,
                )
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

                if self._reconnect_event.wait(timeout=5.0):
                    self._reconnect_event.clear()
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
                    logging.info("%.1f frames/s 受信 (OSC 送信: %s)", self.fps,
                                 "有効" if cfg.osc_enabled else "無効")
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
    level = logging.DEBUG if cfg.verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list = [logging.StreamHandler()]

    if cfg.log_file:
        try:
            fh = logging.FileHandler(cfg.log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
            handlers.append(fh)
        except OSError as e:
            print(f"WARNING: ログファイルを開けません ({e})。コンソールのみに出力します。",
                  file=sys.stderr)

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt,
                        handlers=handlers)


def main():
    all_slots = list(TRACKER_ADDRESSES.keys())

    parser = argparse.ArgumentParser(
        description=(
            "rebocap-osc-bridge - Rebocap フルボディトラッキングを VRChat に送信\n"
            "OSC Trackers 使用 (SteamVR 不要)\n"
            "WARNING: OSC 送信は既定で無効。近似 FK を試す場合のみ --enable-osc を指定"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # NOTE: --rebocap-host は SDK v2 では未使用だが後方互換のため残す
    parser.add_argument("--rebocap-host", default=None,
                        help="[未使用: SDK v2 はホスト指定非対応] Rebocap アプリのホスト")
    parser.add_argument("--rebocap-port", type=int, default=None,
                        help="Rebocap WebSocket ポート (デフォルト: 7690)")
    parser.add_argument("--osc-ip", default=None,
                        help="VRChat OSC 送信先 IP (デフォルト: 127.0.0.1)")
    parser.add_argument("--osc-port", type=int, default=None,
                        help="VRChat OSC ポート (デフォルト: 9000)")
    parser.add_argument("--enable-osc", action="store_true",
                        help="近似 FK の OSC Tracker 送信を明示的に有効化")
    parser.add_argument(
        "--trackers", nargs="+", default=None,
        metavar="SLOT",
        help=f"送信するトラッカースロット。選択肢: {all_slots}。デフォルト: hip left_foot right_foot",
    )
    parser.add_argument(
        "--send-head", action="store_true",
        help="head pose も送信 (HMD/VR モード専用、tracking-space 配置に影響)",
    )
    parser.add_argument(
        "--align-head-position", action="store_true",
        help="head 位置だけを送り OSC tracking-space の平行移動を補正",
    )
    parser.add_argument(
        "--height", type=float, default=None, metavar="METERS",
        help="実身長 (メートル)。スケール補正に使用 (デフォルト: 1.70)",
    )
    parser.add_argument("--vmc", action="store_true",
                        help="現在利用不可: VMC 出力は一時停止中")
    parser.add_argument("--vmc-ip", default=None)
    parser.add_argument("--vmc-port", type=int, default=None)
    parser.add_argument("--oscquery", action="store_true",
                        help="現在利用不可: OSCQuery は一時停止中")
    parser.add_argument("--osc-debug", action="store_true",
                        help="送信中の OSC パケットをターミナルに表示")
    parser.add_argument("--log-file", default=None, metavar="PATH")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--init-config", action="store_true",
                        help="デフォルトの config.toml を生成して終了")
    parser.add_argument("--config", default=None, metavar="PATH")

    args = parser.parse_args()

    if args.init_config:
        from pathlib import Path
        p = Path(args.config) if args.config else None
        out = save_default_config(p)
        print(f"config.toml を生成しました: {out}")
        sys.exit(0)

    from pathlib import Path
    config_path = Path(args.config) if args.config else None
    cfg = load_config(config_path)
    cfg = merge_cli_into_config(cfg, args)

    _setup_logging(cfg)

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
