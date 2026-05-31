"""
Tests for rebocap-osc-bridge coordinate utilities and SDK-v2 features.
These run without any VR hardware or Rebocap app.

SDK v2 コールバック署名:
    on_pose(sdk_instance, trans, pose24, static_index, ts)
        trans   : [x, y, z]  — pelvis 位置
        pose24  : 24要素リスト, 各要素は [x, y, z, w] quaternion
        static_index : 接地ボーンインデックス (-1 または 0..11)
        ts      : タイムスタンプ (秒)
    on_close(sdk_instance)
SDK v2 接続:
    sdk = RebocapWsSdk(
        coordinate_type=CoordinateType.UnityCoordinate,
        use_global_rotation=True,
    )
    sdk.open(port)   # ← ホスト指定なし
"""

import math
import sys
import os
import time
import types
from unittest.mock import MagicMock, patch

import pytest

# Allow importing from the package without installation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# python-osc をモック
sys.modules.setdefault("pythonosc", MagicMock())
sys.modules.setdefault("pythonosc.udp_client", MagicMock())

from rebocap_osc_bridge.bridge import (
    quat_to_euler_deg,
    rebocap_pos_to_vrchat,
    scale_position,
    parse_rebocap_frame,
    compute_fk_positions,
    VRChatOSCSender,
    BonePose,           # BoneRotation の後方互換エイリアス
    BoneRotation,
    RebocapFrame,
    EXPECTED_BONE_COUNT,
    BridgeRunner,
    _validate_trackers,
)
from rebocap_osc_bridge.config import (
    BridgeConfig,
    load_config,
    save_config,
    save_default_config,
    merge_cli_into_config,
    validate_config,
)


# ---------------------------------------------------------------------------
# 座標変換テスト
# ---------------------------------------------------------------------------

class TestCoordinateConversion:
    def test_identity_quaternion_gives_zero_rotation(self):
        """Identity quaternion → 全オイラー角がゼロ。"""
        ex, ey, ez = quat_to_euler_deg(0, 0, 0, 1)
        assert abs(ex) < 1e-6
        assert abs(ey) < 1e-6
        assert abs(ez) < 1e-6

    def test_position_passthrough(self):
        """SDK は CoordinateType.UnityCoordinate のため Z 反転不要。"""
        x, y, z = rebocap_pos_to_vrchat(1.0, 2.0, 3.0)
        assert x == 1.0
        assert y == 2.0
        assert z == 3.0  # Z を反転してはいけない

    def test_position_zero_unchanged(self):
        x, y, z = rebocap_pos_to_vrchat(0, 0, 0)
        assert x == 0 and y == 0 and z == 0

    def test_position_negative_z_unchanged(self):
        x, y, z = rebocap_pos_to_vrchat(0.0, 0.0, -5.0)
        assert z == -5.0

    def test_90deg_yaw_quaternion(self):
        """Y 軸 90° → euler_y ≈ 90, euler_x/z ≈ 0。"""
        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        ex, ey, ez = quat_to_euler_deg(0, s, 0, c)
        assert abs(ey - 90.0) < 0.01, f"ey should be ~90, got {ey}"
        assert abs(ex) < 0.01
        assert abs(ez) < 0.01

    def test_90deg_pitch_quaternion(self):
        """X 軸 90° → euler_x ≈ 90。"""
        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        ex, ey, ez = quat_to_euler_deg(s, 0, 0, c)
        assert abs(ex - 90.0) < 0.01, f"ex should be ~90, got {ex}"
        assert abs(ey) < 0.01
        assert abs(ez) < 0.01

    def test_90deg_roll_quaternion(self):
        """Z 軸 90° → euler_z ≈ 90。"""
        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        ex, ey, ez = quat_to_euler_deg(0, 0, s, c)
        assert abs(ez - 90.0) < 0.01, f"ez should be ~90, got {ez}"
        assert abs(ex) < 0.01
        assert abs(ey) < 0.01


# ---------------------------------------------------------------------------
# 身長スケール補正テスト
# ---------------------------------------------------------------------------

class TestScalePosition:
    def test_reference_height_no_scale(self):
        """基準身長 (1.75m) では座標がそのまま。"""
        x, y, z = scale_position(1.0, 2.0, 3.0, height_m=1.75)
        assert abs(x - 1.0) < 1e-9
        assert abs(y - 2.0) < 1e-9
        assert abs(z - 3.0) < 1e-9

    def test_tall_person_scales_up(self):
        """基準より高い身長 (1.75 × 2 = 3.50m) では 2 倍になる。"""
        x, y, z = scale_position(1.0, 1.0, 1.0, height_m=3.50)
        assert abs(x - 2.0) < 1e-6
        assert abs(y - 2.0) < 1e-6
        assert abs(z - 2.0) < 1e-6

    def test_short_person_scales_down(self):
        """基準の半分の身長 (0.875m) では 0.5 倍になる。"""
        x, y, z = scale_position(2.0, 2.0, 2.0, height_m=0.875)
        assert abs(x - 1.0) < 1e-6
        assert abs(y - 1.0) < 1e-6
        assert abs(z - 1.0) < 1e-6

    def test_zero_position_always_zero(self):
        """位置がゼロの場合はスケールに関わらずゼロのまま。"""
        x, y, z = scale_position(0.0, 0.0, 0.0, height_m=1.60)
        assert x == 0.0 and y == 0.0 and z == 0.0

    def test_typical_height(self):
        """一般的な身長 (1.70m) で係数が正しい。"""
        scale = 1.70 / 1.75
        x, y, z = scale_position(1.0, 1.0, 1.0, height_m=1.70)
        assert abs(x - scale) < 1e-9


# ---------------------------------------------------------------------------
# フレームパーステスト (SDK v2 API: parse_rebocap_frame(trans, pose24, si, ts))
# ---------------------------------------------------------------------------

class TestFrameParsing:
    """
    SDK v2 形式のフレームパーステスト。

    parse_rebocap_frame(trans, pose24, static_index, ts) → Optional[RebocapFrame]
      trans   : [x, y, z]  長さ 3 の pelvis 位置
      pose24  : 24 quaternions, 各要素 [x, y, z, w] (UnityCoordinate 実機出力順)
    """

    def _valid_trans(self):
        """有効な trans: [x, y, z]"""
        return [0.0, 0.0, 0.0]

    def _valid_pose24(self, n=EXPECTED_BONE_COUNT):
        """有効な pose24: n 個の [x, y, z, w] quaternion (identity)"""
        return [[0.0, 0.0, 0.0, 1.0] for _ in range(n)]

    # --- 正常系 ---

    def test_parse_valid_frame_returns_rebocap_frame(self):
        """正常な入力 → RebocapFrame が返る。"""
        frame = parse_rebocap_frame(self._valid_trans(), self._valid_pose24())
        assert frame is not None
        assert isinstance(frame, RebocapFrame)

    def test_parse_valid_frame_has_expected_bone_count(self):
        """正常フレームは EXPECTED_BONE_COUNT 本のボーンを持つ。"""
        frame = parse_rebocap_frame(self._valid_trans(), self._valid_pose24())
        assert frame is not None
        assert len(frame.bones) == EXPECTED_BONE_COUNT

    def test_pelvis_position_from_trans(self):
        """pelvis_position は trans 引数の値を使う。"""
        trans = [1.5, 2.5, 3.5]
        frame = parse_rebocap_frame(trans, self._valid_pose24())
        assert frame is not None
        assert frame.pelvis_position == (1.5, 2.5, 3.5)

    def test_static_index_preserved(self):
        """static_index がフレームに保存される。"""
        frame = parse_rebocap_frame(
            self._valid_trans(), self._valid_pose24(), static_index=5
        )
        assert frame is not None
        assert frame.static_index == 5

    def test_timestamp_preserved(self):
        """タイムスタンプがフレームに保存される。"""
        frame = parse_rebocap_frame(
            self._valid_trans(), self._valid_pose24(), ts=1.23
        )
        assert frame is not None
        assert abs(frame.timestamp - 1.23) < 1e-9

    def test_default_static_index_is_minus_one(self):
        """static_index 省略時のデフォルトは -1。"""
        frame = parse_rebocap_frame(self._valid_trans(), self._valid_pose24())
        assert frame is not None
        assert frame.static_index == -1

    def test_static_index_out_of_range_returns_none(self):
        assert parse_rebocap_frame(
            self._valid_trans(), self._valid_pose24(), static_index=12
        ) is None
        assert parse_rebocap_frame(
            self._valid_trans(), self._valid_pose24(), static_index=-2
        ) is None

    def test_non_finite_timestamp_returns_none(self):
        assert parse_rebocap_frame(
            self._valid_trans(), self._valid_pose24(), ts=float("nan")
        ) is None
        assert parse_rebocap_frame(
            self._valid_trans(), self._valid_pose24(), ts=float("inf")
        ) is None

    # --- UnityCoordinate 実機出力 [x, y, z, w] 順の検証 ---

    def test_quaternion_wxyz_order_preserved(self):
        """UnityCoordinate 実機出力 [x, y, z, w] を名前付きフィールドへ正しくマップ。"""
        pose24 = self._valid_pose24()
        pose24[0] = [0.1, 0.2, 0.3, 0.9]   # x=0.1, y=0.2, z=0.3, w=0.9
        frame = parse_rebocap_frame(self._valid_trans(), pose24)
        assert frame is not None
        b = frame.bones[0]
        assert b.qw == 0.9, f"qw expected 0.9, got {b.qw}"
        assert b.qx == 0.1, f"qx expected 0.1, got {b.qx}"
        assert b.qy == 0.2, f"qy expected 0.2, got {b.qy}"
        assert b.qz == 0.3, f"qz expected 0.3, got {b.qz}"

    def test_all_bones_wxyz_order(self):
        """全 24 ボーンで [x,y,z,w] 順が正しく適用される。"""
        pose24 = [[0.01, 0.02, 0.03, float(i + 1)] for i in range(EXPECTED_BONE_COUNT)]
        # 正規化されない (ゼロでなければ通る)
        frame = parse_rebocap_frame(self._valid_trans(), pose24)
        assert frame is not None
        for i, bone in enumerate(frame.bones):
            assert bone.qw == float(i + 1), f"bone {i}: qw mismatch"

    # --- trans 長さ検証 ---

    def test_parse_trans_none_returns_none(self):
        """trans=None → None。"""
        assert parse_rebocap_frame(None, self._valid_pose24()) is None

    def test_parse_trans_too_short_returns_none(self):
        """trans 長さ < 3 → None。"""
        assert parse_rebocap_frame([0.0, 0.0], self._valid_pose24()) is None

    def test_parse_trans_too_long_returns_none(self):
        """trans 長さ > 3 → None。"""
        assert parse_rebocap_frame([0.0, 0.0, 0.0, 0.0], self._valid_pose24()) is None

    def test_parse_trans_nan_returns_none(self):
        """trans に NaN → None。"""
        assert parse_rebocap_frame([float("nan"), 0.0, 0.0], self._valid_pose24()) is None

    def test_parse_trans_inf_returns_none(self):
        """trans に Inf → None。"""
        assert parse_rebocap_frame([float("inf"), 0.0, 0.0], self._valid_pose24()) is None

    # --- bone 数検証 ---

    def test_parse_pose24_none_returns_none(self):
        """pose24=None → None。"""
        assert parse_rebocap_frame(self._valid_trans(), None) is None

    def test_parse_wrong_bone_count_too_few_returns_none(self):
        """bone 数が少なすぎ → None。"""
        assert parse_rebocap_frame(self._valid_trans(), self._valid_pose24(10)) is None

    def test_parse_wrong_bone_count_too_many_returns_none(self):
        """bone 数が多すぎ → None。"""
        assert parse_rebocap_frame(self._valid_trans(), self._valid_pose24(30)) is None

    # --- quaternion 長さ検証 ---

    def test_parse_quaternion_too_short_returns_none(self):
        """quaternion 長さ < 4 → None。"""
        pose24 = self._valid_pose24()
        pose24[0] = [1.0, 0.0, 0.0]  # 長さ 3
        assert parse_rebocap_frame(self._valid_trans(), pose24) is None

    def test_parse_quaternion_too_long_returns_none(self):
        """quaternion 長さ > 4 → None。"""
        pose24 = self._valid_pose24()
        pose24[0] = [1.0, 0.0, 0.0, 0.0, 0.0]  # 長さ 5
        assert parse_rebocap_frame(self._valid_trans(), pose24) is None

    def test_parse_quaternion_wrong_length_last_bone_returns_none(self):
        """最後の bone の quaternion 長さが不正でも → None。"""
        pose24 = self._valid_pose24()
        pose24[-1] = [1.0, 0.0, 0.0]  # 長さ 3
        assert parse_rebocap_frame(self._valid_trans(), pose24) is None

    # --- NaN・ゼロ quaternion 検証 ---

    def test_parse_quaternion_nan_w_returns_none(self):
        """quaternion の w に NaN → None。"""
        pose24 = self._valid_pose24()
        pose24[0] = [float("nan"), 0.0, 0.0, 0.0]  # w=NaN
        assert parse_rebocap_frame(self._valid_trans(), pose24) is None

    def test_parse_quaternion_nan_xyz_returns_none(self):
        """quaternion の x/y/z に NaN → None。"""
        pose24 = self._valid_pose24()
        pose24[3] = [1.0, float("nan"), 0.0, 0.0]  # x=NaN
        assert parse_rebocap_frame(self._valid_trans(), pose24) is None

    def test_parse_zero_quaternion_returns_none(self):
        """全ゼロ quaternion → None。"""
        pose24 = self._valid_pose24()
        pose24[0] = [0.0, 0.0, 0.0, 0.0]
        assert parse_rebocap_frame(self._valid_trans(), pose24) is None

    def test_parse_zero_quaternion_middle_bone_returns_none(self):
        """中間ボーンのゼロ quaternion も → None。"""
        pose24 = self._valid_pose24()
        pose24[12] = [0.0, 0.0, 0.0, 0.0]
        assert parse_rebocap_frame(self._valid_trans(), pose24) is None

    # --- BonePose 後方互換エイリアス ---

    def test_bonepose_is_alias_for_bonerotation(self):
        """BonePose は BoneRotation の後方互換エイリアス。"""
        assert BonePose is BoneRotation


class TestForwardKinematics:
    def _identity_frame(self):
        return parse_rebocap_frame(
            [0.0, 1.0, 0.0],
            [[0.0, 0.0, 0.0, 1.0] for _ in range(EXPECTED_BONE_COUNT)],
        )

    def test_identity_pose_places_ankles_below_pelvis(self):
        positions = compute_fk_positions(self._identity_frame(), height_m=1.70)
        assert len(positions) == EXPECTED_BONE_COUNT
        assert positions[7] == pytest.approx((-0.09, 0.08, 0.0))
        assert positions[8] == pytest.approx((0.09, 0.08, 0.0))

    def test_height_scales_offsets_but_not_pelvis_translation(self):
        frame = self._identity_frame()
        positions = compute_fk_positions(frame, height_m=3.40)
        assert positions[0] == (0.0, 1.0, 0.0)
        assert positions[7] == pytest.approx((-0.18, -0.84, 0.0))

    def test_sender_is_disabled_by_default(self):
        client = MagicMock()
        with patch("rebocap_osc_bridge.bridge.udp_client.SimpleUDPClient", return_value=client):
            sender = VRChatOSCSender("127.0.0.1", 9000, ["hip"])
            sender.send_frame(self._identity_frame())
        client.send_message.assert_not_called()

    def test_enabled_sender_sends_position_and_rotation(self):
        client = MagicMock()
        with patch("rebocap_osc_bridge.bridge.udp_client.SimpleUDPClient", return_value=client):
            sender = VRChatOSCSender("127.0.0.1", 9000, ["hip"], enabled=True)
            sender.send_frame(self._identity_frame())
        assert client.send_message.call_count == 2
        assert client.send_message.call_args_list[0].args[0] == "/tracking/trackers/1/position"
        assert client.send_message.call_args_list[1].args[0] == "/tracking/trackers/1/rotation"

    def test_head_position_alignment_sends_position_only(self):
        client = MagicMock()
        with patch("rebocap_osc_bridge.bridge.udp_client.SimpleUDPClient", return_value=client):
            sender = VRChatOSCSender("127.0.0.1", 9000, [], enabled=True)
            sender.send_head_position(self._identity_frame())
        assert client.send_message.call_count == 1
        assert client.send_message.call_args.args[0] == "/tracking/trackers/head/position"


# ---------------------------------------------------------------------------
# トラッカーバリデーションテスト
# ---------------------------------------------------------------------------

class TestTrackerValidation:
    def test_valid_trackers_pass(self):
        result = _validate_trackers(["hip", "left_foot", "right_foot"])
        assert result == ["hip", "left_foot", "right_foot"]

    def test_all_valid_slots(self):
        slots = ["hip", "chest", "left_foot", "right_foot",
                 "left_knee", "right_knee", "left_elbow", "right_elbow"]
        assert _validate_trackers(slots) == slots

    def test_invalid_tracker_exits(self):
        """不明なスロットは sys.exit(2)。"""
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _validate_trackers(["hip", "invalid_slot"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# config.toml テスト
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_config_values(self):
        """config ファイルなしでデフォルト値が返される。"""
        cfg = load_config(path=None)
        # SDK v2 デフォルトポートは 7690
        assert cfg.rebocap_port == 7690
        assert cfg.osc_port == 9000
        assert cfg.height_m == 1.70

    def test_save_default_config(self, tmp_path):
        """デフォルト config.toml が正しく生成される。"""
        p = tmp_path / "config.toml"
        out = save_default_config(path=p)
        assert p.exists()
        content = p.read_text(encoding="utf-8")
        assert "rebocap" in content
        assert "vrchat" in content
        assert "tracking" in content
        assert "height_m" in content

    def test_save_default_config_no_overwrite(self, tmp_path):
        """既存ファイルは上書きしない。"""
        p = tmp_path / "config.toml"
        p.write_text("existing", encoding="utf-8")
        save_default_config(path=p)
        assert p.read_text(encoding="utf-8") == "existing"

    def test_load_config_from_file(self, tmp_path):
        """config.toml を読み込んで値が反映される。"""
        p = tmp_path / "config.toml"
        p.write_text(
            '[rebocap]\nhost = "10.0.0.1"\nport = 1234\n'
            '[vrchat]\nosc_ip = "10.0.0.2"\nosc_port = 8888\n'
            '[tracking]\ntrackers = ["hip", "chest"]\nheight_m = 1.60\n',
            encoding="utf-8",
        )
        cfg = load_config(path=p)
        assert cfg.rebocap_host == "10.0.0.1"
        assert cfg.rebocap_port == 1234
        assert cfg.osc_ip == "10.0.0.2"
        assert cfg.osc_port == 8888
        assert cfg.trackers == ["hip", "chest"]
        assert abs(cfg.height_m - 1.60) < 1e-9

    def test_load_invalid_config_value_falls_back_to_defaults(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text('[rebocap]\nport = "oops"\n', encoding="utf-8")
        cfg = load_config(path=p)
        assert cfg == BridgeConfig()

    def test_save_config_escapes_toml_strings(self, tmp_path):
        p = tmp_path / "config.toml"
        cfg = BridgeConfig(log_file='C:\\logs\\pose "test".log')
        save_config(cfg, path=p)
        loaded = load_config(path=p)
        assert loaded.log_file == cfg.log_file

    def test_merge_cli_overrides_config(self):
        """CLI 引数が config.toml の値を上書きする。"""
        cfg = BridgeConfig(rebocap_host="10.0.0.1", osc_port=8888)

        class FakeArgs:
            rebocap_host = "192.168.1.1"
            rebocap_port = None
            osc_ip = None
            osc_port = 9999
            trackers = None
            send_head = False
            align_head_position = False
            height = None
            vmc_ip = None
            vmc_port = None
            vmc = False
            oscquery = False
            verbose = False
            log_file = None
            osc_debug = False

        merged = merge_cli_into_config(cfg, FakeArgs())
        assert merged.rebocap_host == "192.168.1.1"   # CLI が上書き
        assert merged.rebocap_port == 7690             # CLI 未指定 → SDK v2 デフォルト値
        assert merged.osc_port == 9999                 # CLI が上書き
        assert merged.osc_ip == "127.0.0.1"           # CLI 未指定 → デフォルトのまま

    def test_save_config_round_trip(self, tmp_path):
        p = tmp_path / "config.toml"
        cfg = BridgeConfig(
            rebocap_host="10.0.0.10",
            osc_ip="10.0.0.20",
            trackers=["hip", "chest"],
            send_head=True,
            osc_enabled=True,
            align_head_position=True,
            height_m=1.82,
        )
        save_config(cfg, path=p)
        loaded = load_config(path=p)
        assert loaded.rebocap_host == "10.0.0.10"
        assert loaded.osc_ip == "10.0.0.20"
        assert loaded.trackers == ["hip", "chest"]
        assert loaded.send_head is True
        assert loaded.osc_enabled is True
        assert loaded.align_head_position is True
        assert loaded.height_m == 1.82

    def test_validate_rejects_invalid_height(self):
        import pytest
        with pytest.raises(ValueError):
            validate_config(BridgeConfig(height_m=float("nan")))

    def test_validate_rejects_invalid_port(self):
        import pytest
        with pytest.raises(ValueError):
            validate_config(BridgeConfig(osc_port=70000))

    def test_validate_rejects_disabled_experimental_features(self):
        import pytest
        with pytest.raises(ValueError):
            validate_config(BridgeConfig(vmc_enabled=True))
        with pytest.raises(ValueError):
            validate_config(BridgeConfig(oscquery_enabled=True))


# ---------------------------------------------------------------------------
# BridgeRunner テスト (SDK v2 モック)
# ---------------------------------------------------------------------------

class TestBridgeRunner:
    def test_sdk_import_failure_clears_running_state(self):
        """SDK が見つからない場合、スレッド終了後に is_running が False になる。"""
        runner = BridgeRunner(BridgeConfig())
        with patch.dict(sys.modules, {"rebocap_ws_sdk": None}):
            runner.start()
            runner._thread.join(timeout=2)
        assert runner.is_running is False
        assert runner._thread.is_alive() is False

    def test_open_called_with_port_only(self):
        """sdk.open() は port 引数のみで呼ばれる (SDK v2 はホスト指定非対応)。"""
        open_calls = []

        class FakeSdk:
            def __init__(self, *_args, **_kwargs):
                pass

            def set_pose_msg_callback(self, callback):
                pass

            def set_exception_close_callback(self, callback):
                pass

            def open(self, *args):
                open_calls.append(args)
                return 0  # success

            def close(self):
                pass

        fake_module = types.SimpleNamespace(
            RebocapWsSdk=FakeSdk,
            CoordinateType=types.SimpleNamespace(UnityCoordinate=object()),
        )
        runner = BridgeRunner(BridgeConfig())
        with patch.dict(sys.modules, {"rebocap_ws_sdk": fake_module}), \
             patch("rebocap_osc_bridge.bridge._RECONNECT_BASE_DELAY", 0.01), \
             patch("rebocap_osc_bridge.bridge._RECONNECT_MAX_DELAY", 0.02):
            runner.start()
            deadline = time.time() + 2
            while not open_calls and time.time() < deadline:
                time.sleep(0.01)
            runner.stop()
            runner._thread.join(timeout=2)

        assert runner._thread.is_alive() is False
        assert len(open_calls) >= 1, "sdk.open() が呼ばれなかった"
        for call_args in open_calls:
            assert len(call_args) == 1, (
                f"sdk.open() は port 引数のみ受け取るべきだが {len(call_args)} 個の引数で呼ばれた: {call_args}"
            )
            assert isinstance(call_args[0], int), (
                f"port は int でなければならないが {type(call_args[0])} が渡された"
            )

    def test_initial_connection_failure_is_retried(self):
        """初回接続失敗後に再接続が試みられる (SDK v2 API)。"""
        open_results = [1, 0]   # 1回目失敗, 2回目成功
        created_sdks = []

        class FakeSdk:
            def __init__(self, *_args, **_kwargs):
                created_sdks.append(self)

            def set_pose_msg_callback(self, callback):
                self.pose_callback = callback

            def set_exception_close_callback(self, callback):
                self.close_callback = callback

            def open(self, _port):
                # SDK v2: port のみ (ホストなし)
                return open_results.pop(0)

            def close(self):
                pass

        fake_module = types.SimpleNamespace(
            RebocapWsSdk=FakeSdk,
            # SDK v2 では CoordSpace ではなく CoordinateType
            CoordinateType=types.SimpleNamespace(UnityCoordinate=object()),
        )
        runner = BridgeRunner(BridgeConfig())
        with patch.dict(sys.modules, {"rebocap_ws_sdk": fake_module}), \
             patch("rebocap_osc_bridge.bridge._RECONNECT_BASE_DELAY", 0.01), \
             patch("rebocap_osc_bridge.bridge._RECONNECT_MAX_DELAY", 0.02):
            runner.start()
            deadline = time.time() + 2
            while runner.status != "ストリーミング中" and time.time() < deadline:
                time.sleep(0.01)
            runner.stop()
            runner._thread.join(timeout=2)

        assert len(created_sdks) >= 2, (
            f"再接続が試みられなかった (SDK 生成回数: {len(created_sdks)})"
        )
        assert runner.is_running is False
