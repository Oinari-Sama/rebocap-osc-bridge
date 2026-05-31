# rebocap-osc-bridge

> [!WARNING]
> **Development status: not working with the official Rebocap Python SDK yet.**
>
> The official Python SDK currently exposes only the pelvis translation and
> 24 bone rotations. This project still needs forward kinematics or another
> verified position source before it can send usable VRChat OSC Tracker poses.
> Do not use this preview for live tracking.

**Stream Rebocap full-body tracking directly into VRChat — no SteamVR required.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

-----

## What it does

Rebocap normally connects to VRChat through SteamVR's virtual tracker driver.  
This bridge skips SteamVR entirely and sends pose data **directly to VRChat** using its native [OSC Trackers](https://docs.vrchat.com/docs/osc-trackers) protocol.

```
Rebocap App  ──WebSocket──▶  rebocap-osc-bridge  ──OSC──▶  VRChat
                                      │
```

**Benefits over the SteamVR driver:**

- No SteamVR needed — useful for standalone or low-spec PCs
- Works from a **different machine on the same network** (e.g. Rebocap on PC-A, VRChat on PC-B)
- **Quest standalone support** — play VRChat on Quest without PCVR ([詳細ガイド](docs/quest-standalone-guide.md))
- **Auto-reconnect** — if Rebocap restarts mid-session, the bridge reconnects automatically
- **GUI mode** — non-engineers can use it without touching the terminal
- **config.toml** — save your settings, launch with zero arguments

-----

## Requirements

| Requirement             | Notes                                                             |
|-------------------------|-------------------------------------------------------------------|
| Python 3.9+             | [python.org](https://www.python.org/downloads/)                   |
| Rebocap app             | Must be running and calibrated                                    |
| Rebocap Python SDK      | [Download from official docs](https://doc.rebocap.com/en_US/SDK/) |
| VRChat with OSC enabled | Settings → OSC → Enable                                           |

-----

## Installation

```bash
pip install rebocap-osc-bridge
```

Or from source:

```bash
git clone https://github.com/Oinari-Sama/rebocap-osc-bridge.git
cd rebocap-osc-bridge
pip install -e .
```

**Then install the Rebocap Python SDK:**

1. Download `rebocap_ws_sdk_python_v2.zip` from [doc.rebocap.com/en_US/SDK](https://doc.rebocap.com/en_US/SDK/)
2. Unzip and place the `rebocap_ws_sdk` folder in your working directory

-----

## Quick start

### GUI モード（推奨）

```bash
rebocap-osc-bridge --gui
```

ウィンドウが開くので接続先を入力して **▶ 開始** を押すだけです。

### CLI モード

1. Rebocap アプリを起動し、キャリブレーションを完了する
2. VRChat で **Settings → OSC → Enable** を有効にする
3. ブリッジを起動する:

```bash
rebocap-osc-bridge
```

Default: streams **hip + left foot + right foot** to VRChat on `127.0.0.1:9000`.

4. VRChat の Action Menu → **Calibrate FBT** → T ポーズでキャリブレーション

-----

## Configuration (config.toml)

設定をファイルに保存して、次回から引数なしで起動できます。

```bash
# デフォルトの config.toml を生成
rebocap-osc-bridge --init-config
```

生成される `config.toml`:

```toml
[rebocap]
host = "127.0.0.1"
port = 9527

[vrchat]
osc_ip   = "127.0.0.1"
osc_port = 9000

[tracking]
trackers  = ["hip", "left_foot", "right_foot"]
send_head = false
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
```

CLI 引数は config.toml より優先されます（`CLI > config.toml > デフォルト値`）。

-----

## Usage

```
rebocap-osc-bridge [OPTIONS]

接続:
  --rebocap-host HOST     Rebocap app IP address     [default: 127.0.0.1]
  --rebocap-port PORT     Rebocap WebSocket port      [default: 9527]
  --osc-ip IP             VRChat OSC target IP        [default: 127.0.0.1]
  --osc-port PORT         VRChat OSC port             [default: 9000]

トラッキング:
  --trackers SLOT [...]   Tracker slots to stream     [default: hip left_foot right_foot]
  --send-head             Head pose 送信 (HMD/VR モード専用)
  --height METERS         実身長(m) — スケール補正     [default: config or 1.70]

出力:
  --vmc                   現在利用不可
  --vmc-ip IP             VMC 送信先 IP               [default: 127.0.0.1]
  --vmc-port PORT         VMC ポート                  [default: 39539]
  --oscquery              現在利用不可

ログ:
  --osc-debug             OSC パケットをターミナルに表示
  --log-file PATH         ログをファイルにも書き出す
  --verbose / -v          デバッグログを有効化

その他:
  --gui                   GUI モードで起動
  --init-config           デフォルトの config.toml を生成して終了
  --config PATH           config.toml のパスを指定
```

### Available tracker slots

| Slot          | Bone        |
|---------------|-------------|
| `hip`         | Pelvis      |
| `chest`       | Spine3      |
| `left_foot`   | Left ankle  |
| `right_foot`  | Right ankle |
| `left_knee`   | Left knee   |
| `right_knee`  | Right knee  |
| `left_elbow`  | Left elbow  |
| `right_elbow` | Right elbow |

### `--send-head` 警告

> ⚠️ **`--send-head` は HMD (VR ヘッドセット) モード専用です。**
>
> VRChat は head トラッカーの位置を OSC トラッキング空間の原点として使用します。
> **デスクトップモード**では HMD がないため、全トラッカーが意図しない高さにシフトし
> アバターが浮いたり地面に埋まったりします。

### フルボディ 8 点トラッキング (VR モード)

```bash
rebocap-osc-bridge \
  --trackers hip chest left_foot right_foot left_knee right_knee left_elbow right_elbow \
  --height 1.72 \
  --send-head
```

### Quest 単体モード (別マシンの VRChat へ送信)

```bash
rebocap-osc-bridge \
  --osc-ip 192.168.1.42 \
  --height 1.72
```

→ 詳細: [Quest 単体対応ガイド](docs/quest-standalone-guide.md)

### OSC デバッグ (送信パケットをターミナルに表示)

```bash
rebocap-osc-bridge --osc-debug --verbose
```

-----

## How it works

1. Connects to the Rebocap app via its Python WebSocket SDK (60 fps).
2. Reads global bone positions and quaternion rotations for all 24 joints.
3. The SDK is initialised in `CoordSpace.UNITY` mode — coordinates are already Unity left-handed (Y-up, Z away). No additional coordinate flip needed.
4. Applies height-based scale correction so `1.0 = 1 metre` in VRChat's tracking space.
5. Converts quaternions to XYZ Euler angles (Unity convention) and sends them via OSC to VRChat's `/tracking/trackers/{N}/position` and `/tracking/trackers/{N}/rotation` endpoints.
6. If the WebSocket drops, auto-reconnects with exponential back-off (2 s → 4 s → … → 30 s max).

-----

## Troubleshooting

**Bridge connects but avatar doesn't move**

- Make sure OSC is enabled in VRChat (Settings → OSC).
- Ensure your avatar supports FBT.
- In VR mode only, try `--send-head`.

**Avatar floats or sinks into the ground**

- Check `--height` matches your real height in metres.
- If using `--send-head` in desktop mode, remove that flag.

**"Failed to connect to Rebocap"**

- Rebocap app must be open with trackers connected.
- Check the WebSocket port in Rebocap settings (default 9527).
- The bridge retries automatically — watch the log for reconnect attempts.

**Quest / cross-machine: avatar doesn't move**

- Check PC and Quest are on the same Wi-Fi network.
- Open UDP port 9000 in Windows Firewall on the PC running VRChat.
- See the full [Quest 単体対応ガイド](docs/quest-standalone-guide.md).

**Unknown tracker slot error**

- Valid names: `hip chest left_foot right_foot left_knee right_knee left_elbow right_elbow`
- Names are case-sensitive and use underscores.

## Limitations and safety

- VMC output is temporarily disabled until local bone transforms can be obtained correctly.
- OSCQuery is temporarily disabled until sender-side discovery is implemented correctly.
- OSC uses unauthenticated, unencrypted UDP. Use this bridge only on a trusted local network.
- `--osc-debug` logs body pose values. Do not share debug logs without reviewing them.
- This is an unofficial community project. It is not affiliated with or endorsed by Rebocap or VRChat.

-----

## Contributing

Issues and pull requests welcome!  
This project is especially interested in:

- Testing on different Rebocap hardware revisions
- GUI improvements (system tray, dark mode)
- Additional VMC feature support

-----

## License

[MIT](LICENSE)

-----

## Related projects

- [AXIS → VRChat OSC Bridge](https://github.com/JLChnToZ/axis-vrc-osc-bridge) — inspiration for this project
- [VRCFaceTracking](https://github.com/benaclejames/VRCFaceTracking) — face tracking via OSC
- [SlimeVR](https://github.com/SlimeVR/SlimeVR-Server) — open-source IMU tracker server
- [VMC Protocol](https://protocol.vmc.info/) — Virtual Motion Capture protocol spec
