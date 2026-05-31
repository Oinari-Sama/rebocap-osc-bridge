"""Forward Rebocap's official SteamVR tracker output to VRChat OSC.

This mode uses the virtual trackers produced by Rebocap's official SteamVR
driver. It preserves Rebocap's VR-specific mount-position corrections instead
of approximating tracker positions from SDK joint rotations.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from typing import Dict, Iterable, Optional

from pythonosc import udp_client


TRACKER_ADDRESSES = {
    "hip": "/tracking/trackers/1",
    "chest": "/tracking/trackers/2",
    "left_foot": "/tracking/trackers/3",
    "right_foot": "/tracking/trackers/4",
    "left_knee": "/tracking/trackers/5",
    "right_knee": "/tracking/trackers/6",
    "left_elbow": "/tracking/trackers/7",
    "right_elbow": "/tracking/trackers/8",
}

REBOCAP_DEVICE_IDS = {
    "hip": "rebo_id_3",
    "left_knee": "rebo_id_4",
    "right_knee": "rebo_id_5",
    "left_foot": "rebo_id_8",
    "right_foot": "rebo_id_9",
    "chest": "rebo_id_10",
    "left_elbow": "rebo_id_12",
    "right_elbow": "rebo_id_13",
}

HEAD_ADDRESS = "/tracking/trackers/head"
DEFAULT_TRACKERS = ("hip", "left_foot", "right_foot")


def openvr_position_to_unity(matrix) -> list:
    """Convert OpenVR right-handed position into Unity left-handed position."""
    return [matrix[0][3], matrix[1][3], -matrix[2][3]]


def rotation_matrix_to_vrchat_euler(matrix) -> list:
    """Convert a Unity-basis rotation matrix into VRChat OSC Euler angles."""
    m00, _, m02 = matrix[0]
    m10, m11, m12 = matrix[1]
    m20, _, m22 = matrix[2]
    euler_x = math.asin(max(-1.0, min(1.0, -m12)))
    if abs(abs(m12) - 1.0) < 1e-7:
        euler_y = math.atan2(-m20, m00)
        euler_z = 0.0
    else:
        euler_y = math.atan2(m02, m22)
        euler_z = math.atan2(m10, m11)
    return [math.degrees(value) for value in (euler_x, euler_y, euler_z)]


def openvr_rotation_to_unity_euler(matrix) -> list:
    """Convert an OpenVR right-handed rotation matrix into VRChat Euler angles."""
    rotation = [
        [matrix[0][0], matrix[0][1], -matrix[0][2]],
        [matrix[1][0], matrix[1][1], -matrix[1][2]],
        [-matrix[2][0], -matrix[2][1], matrix[2][2]],
    ]
    return rotation_matrix_to_vrchat_euler(rotation)


def collect_rebocap_devices(vr, poses, openvr_module) -> Dict[str, object]:
    """Return the valid official Rebocap virtual tracker matrices by device id."""
    devices = {}
    for index, pose in enumerate(poses):
        if not pose.bPoseIsValid:
            continue
        try:
            serial = vr.getStringTrackedDeviceProperty(
                index, openvr_module.Prop_SerialNumber_String
            )
        except Exception:
            continue
        if serial in REBOCAP_DEVICE_IDS.values():
            devices[serial] = pose.mDeviceToAbsoluteTracking
    return devices


def validate_trackers(trackers: Iterable[str]) -> tuple:
    """Validate and deduplicate tracker slot names while preserving order."""
    values = tuple(dict.fromkeys(trackers))
    invalid = [value for value in values if value not in TRACKER_ADDRESSES]
    if invalid:
        raise ValueError(
            f"Unknown tracker slot(s): {', '.join(invalid)}. "
            f"Choose from: {', '.join(TRACKER_ADDRESSES)}"
        )
    return values


class OpenVROSCSender:
    """Send official Rebocap OpenVR trackers to VRChat OSC."""

    def __init__(
        self,
        ip: str,
        port: int,
        trackers: Iterable[str] = DEFAULT_TRACKERS,
        *,
        enabled: bool = False,
        align_head_position: bool = False,
        head_offset=(0.0, 0.0, 0.0),
        osc_debug: bool = False,
        client=None,
    ):
        self._client = client or udp_client.SimpleUDPClient(ip, port)
        self._trackers = validate_trackers(trackers)
        self._enabled = enabled
        self._align_head_position = align_head_position
        self._head_offset = tuple(float(value) for value in head_offset)
        if len(self._head_offset) != 3 or not all(math.isfinite(value) for value in self._head_offset):
            raise ValueError("head_offset must contain three finite numbers")
        self._osc_debug = osc_debug

    def _send(self, address: str, values: list) -> None:
        self._client.send_message(address, values)
        if self._osc_debug:
            logging.debug("OSC %s %s", address, values)

    def send_poses(self, devices: Dict[str, object], hmd_matrix=None) -> None:
        if not self._enabled:
            return
        for slot in self._trackers:
            matrix = devices.get(REBOCAP_DEVICE_IDS[slot])
            if matrix is None:
                continue
            address = TRACKER_ADDRESSES[slot]
            self._send(f"{address}/position", openvr_position_to_unity(matrix))
            self._send(f"{address}/rotation", openvr_rotation_to_unity_euler(matrix))
        if self._align_head_position and hmd_matrix is not None:
            head_position = openvr_position_to_unity(hmd_matrix)
            self._send(
                f"{HEAD_ADDRESS}/position",
                [head_position[index] + self._head_offset[index] for index in range(3)],
            )


def _import_openvr():
    try:
        import openvr  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "OpenVR mode requires the optional dependency. "
            "Install with: pip install -e .[openvr]"
        ) from exc
    return openvr


def run(
    *,
    osc_ip: str,
    osc_port: int,
    trackers: Iterable[str],
    enabled: bool,
    align_head_position: bool,
    head_offset,
    fps: float,
    osc_debug: bool,
) -> None:
    """Run the OpenVR forwarding loop until interrupted."""
    if not math.isfinite(fps) or fps <= 0 or fps > 240:
        raise ValueError("fps must be a finite number in the range 0 < fps <= 240")
    openvr = _import_openvr()
    sender = OpenVROSCSender(
        osc_ip,
        osc_port,
        trackers,
        enabled=enabled,
        align_head_position=align_head_position,
        head_offset=head_offset,
        osc_debug=osc_debug,
    )
    if not enabled:
        logging.warning("VRChat OSC output is disabled. Pass --enable-osc to send.")
    openvr.init(openvr.VRApplication_Background)
    vr = openvr.VRSystem()
    period = 1.0 / fps
    last_missing_warning = 0.0
    try:
        while True:
            started = time.perf_counter()
            poses = vr.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding,
                0,
                openvr.k_unMaxTrackedDeviceCount,
            )
            devices = collect_rebocap_devices(vr, poses, openvr)
            missing = [
                slot for slot in sender._trackers
                if REBOCAP_DEVICE_IDS[slot] not in devices
            ]
            now = time.monotonic()
            if missing and now - last_missing_warning >= 5.0:
                logging.warning(
                    "Missing official Rebocap OpenVR tracker(s): %s",
                    ", ".join(missing),
                )
                last_missing_warning = now
            hmd_pose = poses[openvr.k_unTrackedDeviceIndex_Hmd]
            hmd_matrix = hmd_pose.mDeviceToAbsoluteTracking if hmd_pose.bPoseIsValid else None
            sender.send_poses(devices, hmd_matrix)
            time.sleep(max(0.0, period - (time.perf_counter() - started)))
    finally:
        openvr.shutdown()


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Forward Rebocap's official SteamVR trackers to VRChat OSC."
    )
    parser.add_argument("--osc-ip", default="127.0.0.1")
    parser.add_argument("--osc-port", type=int, default=9000)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--enable-osc", action="store_true")
    parser.add_argument("--align-head-position", action="store_true")
    parser.add_argument(
        "--head-offset",
        nargs=3,
        type=float,
        default=(0.0, 0.0, 0.0),
        metavar=("X", "Y", "Z"),
        help="Optional HMD alignment offset in meters, applied only to head position.",
    )
    parser.add_argument("--osc-debug", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--trackers",
        nargs="+",
        default=list(DEFAULT_TRACKERS),
        metavar="SLOT",
        help=f"Tracker slots. Choose from: {', '.join(TRACKER_ADDRESSES)}",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        trackers = validate_trackers(args.trackers)
        run(
            osc_ip=args.osc_ip,
            osc_port=args.osc_port,
            trackers=trackers,
            enabled=args.enable_osc,
            align_head_position=args.align_head_position,
            head_offset=args.head_offset,
            fps=args.fps,
            osc_debug=args.osc_debug,
        )
    except KeyboardInterrupt:
        logging.info("Stopped.")
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main(sys.argv[1:])
