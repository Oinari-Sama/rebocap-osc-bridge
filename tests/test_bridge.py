"""
Tests for rebocap-osc-bridge coordinate utilities and new features.
These run without any VR hardware or Rebocap app.
"""

import math
import sys
import os
import time
import types
from unittest.mock import MagicMock, patch

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
    BonePose,
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
        """Fix (#1): SDK は CoordSpace.UNITY のため Z 反転不要。"""
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
        """Fix (#2): Y 軸 90° → euler_y ≈ 90, euler_x/z ≈ 0。"""
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
# Feature #3: 身長スケール補正テスト
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
# フレームパーステスト
# ---------------------------------------------------------------------------

class TestFrameParsing:
    def _make_msg(self, n=EXPECTED_BONE_COUNT):
        class Pos:
            def __init__(self): self.x = self.y = self.z = 0.0
        class Rot:
            def __init__(self): self.x = self.y = self.z = 0.0; self.w = 1.0
        class Pose:
            def __init__(self): self.pos = Pos(); self.rot = Rot()
        class Msg:
            def __init__(self): self.poses = [Pose() for _ in range(n)]
        return Msg()

    def test_parse_valid_frame(self):
        bones = parse_rebocap_frame(self._make_msg(EXPECTED_BONE_COUNT))
        assert bones is not None
        assert len(bones) == EXPECTED_BONE_COUNT

    def test_parse_malformed_returns_none(self):
        assert parse_rebocap_frame(None) is None

    def test_parse_wrong_bone_count_returns_none(self):
        """Fix (#5/#10): 不正なボーン数は None を返す (tautology テストの修正)。"""
        assert parse_rebocap_frame(self._make_msg(10)) is None

    def test_parse_too_many_bones_returns_none(self):
        assert parse_rebocap_frame(self._make_msg(30)) is None

    def test_parse_non_finite_value_returns_none(self):
        msg = self._make_msg()
        msg.poses[0].pos.x = float("nan")
        assert parse_rebocap_frame(msg) is None

    def test_parse_zero_quaternion_returns_none(self):
        msg = self._make_msg()
        msg.poses[0].rot.w = 0.0
        assert parse_rebocap_frame(msg) is None

    def test_bone_pose_values_preserved(self):
        msg = self._make_msg()
        msg.poses[0].pos.x = 1.5; msg.poses[0].pos.y = 2.5; msg.poses[0].pos.z = 3.5
        msg.poses[0].rot.x = 0.1; msg.poses[0].rot.y = 0.2
        msg.poses[0].rot.z = 0.3; msg.poses[0].rot.w = 0.9
        bones = parse_rebocap_frame(msg)
        assert bones is not None
        assert bones[0].px == 1.5 and bones[0].py == 2.5 and bones[0].pz == 3.5
        assert bones[0].qx == 0.1 and bones[0].qw == 0.9


# ---------------------------------------------------------------------------
# Feature #6: トラッカーバリデーションテスト
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
        """Fix (#6): 不明なスロットは sys.exit(2)。"""
        import pytest
        with pytest.raises(SystemExit) as exc_info:
            _validate_trackers(["hip", "invalid_slot"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Feature #5: config.toml テスト
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_config_values(self):
        """config ファイルなしでデフォルト値が返される。"""
        cfg = load_config(path=None)  # 存在しないパスは BridgeConfig デフォルトを返す
        # デフォルト値の確認 (ファイルが存在しなければデフォルト通り)
        assert cfg.rebocap_port == 9527
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
        assert merged.rebocap_port == 9527             # CLI 未指定 → config のまま
        assert merged.osc_port == 9999                 # CLI が上書き
        assert merged.osc_ip == "127.0.0.1"           # CLI 未指定 → デフォルトのまま

    def test_save_config_round_trip(self, tmp_path):
        p = tmp_path / "config.toml"
        cfg = BridgeConfig(
            rebocap_host="10.0.0.10",
            osc_ip="10.0.0.20",
            trackers=["hip", "chest"],
            send_head=True,
            height_m=1.82,
        )
        save_config(cfg, path=p)
        loaded = load_config(path=p)
        assert loaded.rebocap_host == "10.0.0.10"
        assert loaded.osc_ip == "10.0.0.20"
        assert loaded.trackers == ["hip", "chest"]
        assert loaded.send_head is True
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


class TestBridgeRunner:
    def test_sdk_import_failure_clears_running_state(self):
        runner = BridgeRunner(BridgeConfig())
        with patch.dict(sys.modules, {"rebocap_ws_sdk": None}):
            runner.start()
            runner._thread.join(timeout=1)
        assert runner.is_running is False
        assert runner._thread.is_alive() is False

    def test_initial_connection_failure_is_retried(self):
        open_results = [1, 0]
        created_sdks = []

        class FakeSdk:
            def __init__(self, *_args, **_kwargs):
                created_sdks.append(self)

            def set_pose_msg_callback(self, callback):
                self.pose_callback = callback

            def set_exception_close_callback(self, callback):
                self.close_callback = callback

            def open(self, _host, _port):
                return open_results.pop(0)

            def close(self):
                pass

        fake_module = types.SimpleNamespace(
            RebocapWsSdk=FakeSdk,
            CoordSpace=types.SimpleNamespace(UNITY=object()),
        )
        runner = BridgeRunner(BridgeConfig())
        with patch.dict(sys.modules, {"rebocap_ws_sdk": fake_module}), \
             patch("rebocap_osc_bridge.bridge._RECONNECT_BASE_DELAY", 0.01), \
             patch("rebocap_osc_bridge.bridge._RECONNECT_MAX_DELAY", 0.02):
            runner.start()
            deadline = time.time() + 1
            while runner.status != "ストリーミング中" and time.time() < deadline:
                time.sleep(0.01)
            runner.stop()
            runner._thread.join(timeout=1)

        assert len(created_sdks) >= 2
        assert runner.is_running is False
