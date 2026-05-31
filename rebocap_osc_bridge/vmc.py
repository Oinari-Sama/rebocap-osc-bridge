"""
vmc.py — VMC (Virtual Motion Capture) プロトコル出力

VSeeFace / Warudo / Luppet などの VMC 対応ソフトウェアに
ボーンデータを送信します。

VMC プロトコル仕様:
  https://protocol.vmc.info/
"""

from __future__ import annotations

import logging
import struct
from typing import List

from pythonosc import udp_client

# VMC OSC アドレス
VMC_BONE_ADDR = "/VMC/Ext/Bone/Pos"
VMC_ROOT_ADDR = "/VMC/Ext/Root/Pos"
VMC_ALIVE_ADDR = "/VMC/Ext/OK"

# Rebocap ボーンインデックス → Unity HumanBodyBones 名のマッピング
# 参考: https://docs.unity3d.com/ScriptReference/HumanBodyBones.html
BONE_NAME_MAP = {
    0:  "Hips",
    1:  "LeftUpperLeg",
    2:  "RightUpperLeg",
    3:  "Spine",
    4:  "LeftLowerLeg",
    5:  "RightLowerLeg",
    6:  "Chest",
    7:  "LeftFoot",
    8:  "RightFoot",
    9:  "UpperChest",
    10: "LeftToes",
    11: "RightToes",
    12: "Neck",
    13: "LeftShoulder",
    14: "RightShoulder",
    15: "Head",
    16: "LeftUpperArm",
    17: "RightUpperArm",
    18: "LeftLowerArm",
    19: "RightLowerArm",
    20: "LeftHand",
    21: "RightHand",
    22: "LeftMiddleProximal",
    23: "RightMiddleProximal",
}


class VMCSender:
    """
    VMC プロトコルで全ボーンを送信するクラス。
    VRChat OSCSender と並列で動作します。
    """

    def __init__(self, ip: str, port: int):
        raise RuntimeError(
            "VMC output is temporarily disabled until Rebocap local bone "
            "transforms can be obtained correctly."
        )
        self._client = udp_client.SimpleUDPClient(ip, port)
        logging.info("VMC → %s:%d", ip, port)

    def send_frame(self, bones: list) -> None:
        """
        全ボーンの位置・回転を VMC /VMC/Ext/Bone/Pos で送信し、
        ルート (Hips) を /VMC/Ext/Root/Pos でも送信する。
        """
        for idx, bone in enumerate(bones):
            name = BONE_NAME_MAP.get(idx)
            if name is None:
                continue
            # /VMC/Ext/Bone/Pos [name, px, py, pz, qx, qy, qz, qw]
            self._client.send_message(
                VMC_BONE_ADDR,
                [name, bone.px, bone.py, bone.pz,
                 bone.qx, bone.qy, bone.qz, bone.qw],
            )

        # ルートボーン (Hips) を Root アドレスでも送信
        root = bones[0]
        self._client.send_message(
            VMC_ROOT_ADDR,
            ["root", root.px, root.py, root.pz,
             root.qx, root.qy, root.qz, root.qw],
        )

        # アライブシグナル (receiving=1)
        self._client.send_message(VMC_ALIVE_ADDR, [1])

    def close(self) -> None:
        # SimpleUDPClient はソケットを明示的に閉じる API がないため
        # アライブシグナルを 0 にして接続終了を通知する
        try:
            self._client.send_message(VMC_ALIVE_ADDR, [0])
        except Exception:
            pass
