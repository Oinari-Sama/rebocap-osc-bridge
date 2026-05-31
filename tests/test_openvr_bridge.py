"""Tests for the optional OpenVR forwarding mode."""

from unittest.mock import MagicMock

import pytest

from rebocap_osc_bridge.openvr_bridge import (
    OpenVROSCSender,
    openvr_position_to_unity,
    openvr_rotation_to_unity_euler,
    validate_trackers,
)


def identity_matrix(x=0.0, y=0.0, z=0.0):
    return [
        [1.0, 0.0, 0.0, x],
        [0.0, 1.0, 0.0, y],
        [0.0, 0.0, 1.0, z],
    ]


class TestOpenVRCoordinates:
    def test_position_flips_z_for_unity(self):
        assert openvr_position_to_unity(identity_matrix(1.0, 2.0, 3.0)) == [1.0, 2.0, -3.0]

    def test_identity_rotation_is_zero(self):
        assert openvr_rotation_to_unity_euler(identity_matrix()) == pytest.approx([0.0, 0.0, 0.0])


class TestOpenVRSender:
    def test_sender_is_disabled_by_default(self):
        client = MagicMock()
        sender = OpenVROSCSender("127.0.0.1", 9000, client=client)
        sender.send_poses({"rebo_id_3": identity_matrix()})
        client.send_message.assert_not_called()

    def test_enabled_sender_sends_three_default_trackers_and_head_position(self):
        client = MagicMock()
        sender = OpenVROSCSender(
            "127.0.0.1",
            9000,
            enabled=True,
            align_head_position=True,
            client=client,
        )
        sender.send_poses(
            {
                "rebo_id_3": identity_matrix(1.0, 2.0, 3.0),
                "rebo_id_8": identity_matrix(4.0, 5.0, 6.0),
                "rebo_id_9": identity_matrix(7.0, 8.0, 9.0),
            },
            hmd_matrix=identity_matrix(10.0, 11.0, 12.0),
        )
        assert client.send_message.call_count == 7
        assert client.send_message.call_args_list[0].args == (
            "/tracking/trackers/1/position", [1.0, 2.0, -3.0]
        )
        assert client.send_message.call_args_list[-1].args == (
            "/tracking/trackers/head/position", [10.0, 11.0, -12.0]
        )

    def test_missing_device_is_skipped(self):
        client = MagicMock()
        sender = OpenVROSCSender("127.0.0.1", 9000, enabled=True, client=client)
        sender.send_poses({"rebo_id_3": identity_matrix()})
        assert client.send_message.call_count == 2

    def test_head_offset_changes_alignment_position_only(self):
        client = MagicMock()
        sender = OpenVROSCSender(
            "127.0.0.1",
            9000,
            trackers=[],
            enabled=True,
            align_head_position=True,
            head_offset=(0.1, 0.2, 0.3),
            client=client,
        )
        sender.send_poses({}, hmd_matrix=identity_matrix(1.0, 2.0, 3.0))
        assert client.send_message.call_args.args == (
            "/tracking/trackers/head/position", [1.1, 2.2, -2.7]
        )


class TestTrackerValidation:
    def test_deduplicates_slots(self):
        assert validate_trackers(["hip", "hip", "left_foot"]) == ("hip", "left_foot")

    def test_rejects_unknown_slot(self):
        with pytest.raises(ValueError, match="Unknown tracker"):
            validate_trackers(["hip", "unknown"])
