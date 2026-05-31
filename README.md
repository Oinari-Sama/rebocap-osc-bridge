# rebocap-osc-bridge

> [!WARNING]
> **Experimental preview.**
>
> SDK v2 receiving and approximate forward kinematics (FK) are implemented.
> OSC Tracker output is disabled by default and must be enabled explicitly.
> Start with hip and feet only. Do not depend on this preview for safety-critical
> or professional use.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9-3.12](https://img.shields.io/badge/python-3.9--3.12-blue.svg)](https://www.python.org/)

## Current Scope

This project:

- forwards Rebocap's official SteamVR virtual trackers to VRChat OSC;
- connects to the Rebocap app through the official Python SDK v2 API;
- receives pelvis translation and 24 global bone rotations;
- validates malformed frames and retries after WebSocket disconnects;
- calculates approximate tracker positions from an explicit body model;
- optionally sends VRChat OSC Trackers when `--enable-osc` is specified.

The Python SDK v2 API does not expose skeleton offsets or the foot-vertex helper.
Tracker positions are therefore approximate and may need further calibration.

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.9-3.12 | Python 3.13 is not supported by the official SDK v2 |
| Rebocap app | Must be running with sensors connected and calibrated |
| Rebocap Python SDK v2 | [Download from official docs](https://doc.rebocap.com/en_US/SDK/) |
| VRChat | Required only when testing OSC output |

## Installation

```bash
git clone https://github.com/Oinari-Sama/rebocap-osc-bridge.git
cd rebocap-osc-bridge
pip install -e .
```

For the recommended SteamVR forwarding mode:

```bash
pip install -e .[openvr]
```

Download `rebocap_ws_sdk_python_v2.zip` from the
[official SDK documentation](https://doc.rebocap.com/en_US/SDK/), then place
the `rebocap_ws_sdk` folder where Python can import it.

## Safe Test Procedure

When SteamVR and Rebocap's official SteamVR driver are available, first run
the forwarding mode without OSC output:

```bash
rebocap-openvr-osc-bridge
```

Then enable only the hardware-tested waist-and-feet configuration:

```bash
rebocap-openvr-osc-bridge --enable-osc --align-head-position
```

This mode forwards Rebocap's official corrected virtual tracker positions.
Add chest, knees, or elbows only after the minimal setup behaves correctly:

```bash
rebocap-openvr-osc-bridge --enable-osc --align-head-position --trackers hip left_foot right_foot chest
```

Rebocap's [VR panel documentation](https://doc.rebocap.com/ja_JP/ui_help_doc/control/connect.html)
describes VR-specific leg-node handling. Forwarding the official OpenVR
trackers preserves that correction stage.

If all OSC trackers remain uniformly shifted after head alignment, adjust the
shared tracking-space alignment rather than individual trackers:

```bash
rebocap-openvr-osc-bridge --enable-osc --align-head-position --head-offset 0 0.03 0
```

For one Quest 2 + Virtual Desktop test setup, the following shared alignment
offset produced matching OpenVR and OSC tracker positions:

```bash
rebocap-openvr-osc-bridge --enable-osc --align-head-position --head-offset 0 -0.05 -0.05 --trackers hip left_foot right_foot chest left_knee right_knee left_elbow right_elbow
```

Treat this as a per-setup example, not a universal default. Start from zero
offset and adjust only when every OSC tracker is shifted in the same direction.

The SDK approximate-FK preview remains available for systems where SteamVR
forwarding is not used.

First verify SDK receiving without sending OSC:

```bash
rebocap-osc-bridge
```

Then enable VRChat OSC and test the minimal hip-and-feet setup:

```bash
rebocap-osc-bridge --enable-osc --align-head-position --trackers hip left_foot right_foot
```

Stop with `Ctrl+C`. Add chest, knees, or elbows only after the minimal setup
behaves correctly. `--align-head-position` sends head position only, allowing
VRChat to translate OSC tracking space so that the trackers align with the
avatar. This minimal configuration was verified with connected hardware.

Full head pose sending is optional and also affects yaw alignment:

```bash
rebocap-osc-bridge --enable-osc --send-head
```

## Configuration

Generate `config.toml`:

```bash
rebocap-osc-bridge --init-config
```

OSC remains disabled unless `osc_enabled = true` is set under `[vrchat]` or
`--enable-osc` is passed explicitly.

`--rebocap-host` remains available for config compatibility but is unused.
The official SDK v2 API accepts `sdk.open(port)` only.

## Quaternion Note

For `CoordinateType.UnityCoordinate`, connected-hardware tests show the Python
SDK pose arrays behaving as `[x, y, z, w]`. This differs from wording in the
SDK archive README. The bridge converts those arrays into named quaternion
fields before FK.

## Limitations And Safety

- FK uses an approximate 1.70 m body model scaled by configured height.
- OpenVR forwarding requires SteamVR and Rebocap's official SteamVR driver.
- The optional `openvr` Python dependency is used only by OpenVR forwarding.
- OSC uses unauthenticated, unencrypted UDP. Use trusted local networks only.
- Body pose values may appear in debug logs. Review logs before sharing them.
- VMC output, OSCQuery discovery, GUI mode, and Quest standalone instructions
  are not included in this preview.
- This is an unofficial community project. It is not affiliated with or
  endorsed by Rebocap or VRChat.

## License

[MIT](LICENSE)
