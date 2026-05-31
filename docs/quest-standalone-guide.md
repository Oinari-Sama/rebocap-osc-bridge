# Quest 単体対応ガイド

> **「SteamVR も PCVR も不要で、Quest だけで Rebocap フルトラ」**

このガイドでは、PC は Rebocap を動かすためだけに使い、
VRChat は **Meta Quest の単体モード (Air Link / USB-C 有線なし)**
で動かす構成を説明します。

---

## 必要なもの

| 機器 | 役割 |
|------|------|
| Windows / Mac PC | Rebocap アプリ + rebocap-osc-bridge を実行 |
| Meta Quest 2 / 3 / Pro | VRChat を単体モードで実行 |
| 同一 Wi-Fi ネットワーク | PC と Quest を同じ LAN に接続 |

> ⚠️ Quest の VRChat と PC は **同じ Wi-Fi ルーターに接続** してください。
> 5 GHz 帯を推奨します（遅延が少ない）。

---

## ステップ 1: Quest の IP アドレスを確認する

1. Quest を装着し、**設定 → Wi-Fi → 接続中のネットワーク名**をタップ
2. 画面下部に表示される **IP アドレス** をメモする  
   例: `192.168.1.42`

---

## ステップ 2: VRChat の OSC を有効にする (Quest 側)

Quest の VRChat 内で:

1. **Action Menu (左手の上ボタン長押し)** を開く
2. **Options → OSC → Enabled** をオンにする

> Quest を再起動した場合や VRChat を再起動した場合も、
> OSC が無効に戻ることがあるため毎回確認してください。

---

## ステップ 3: PC で rebocap-osc-bridge を起動する

Quest の IP アドレスを `--osc-ip` に指定します。

### CLI の場合

```bash
rebocap-osc-bridge \
  --osc-ip 192.168.1.42 \
  --trackers hip left_foot right_foot \
  --height 1.70
```

### GUI の場合

```
rebocap-osc-bridge --gui
```

「VRChat OSC IP」欄に Quest の IP アドレス (`192.168.1.42`) を入力して **開始** を押す。

### config.toml の場合

```toml
[vrchat]
osc_ip = "192.168.1.42"
```

---

## ステップ 4: VRChat でキャリブレーションする

1. Quest を装着し、VRChat でフルトラ対応アバターを選ぶ
2. **Action Menu → Calibrate FBT** をタップ
3. T ポーズを取る

ブリッジのログに `ストリーミング中` または `Streaming` と表示されていれば送信中です。

---

## トラブルシューティング

### アバターが動かない

- PC と Quest が **同じ Wi-Fi** に繋がっているか確認する
- Quest 側で OSC が有効になっているか確認する（毎回必要）
- Quest の IP アドレスが変わっていないか確認する

PC から Quest へ UDP を送信する構成なので、通常は PC 側で UDP 9000 の受信規則を追加する必要はありません。
ルーターやセキュリティソフトで端末間通信を制限している場合は、信頼できる LAN 内の通信だけを許可してください。

### 遅延が大きい

- Wi-Fi を **5 GHz 帯**に切り替える
- Quest と Wi-Fi ルーターの距離を近くする
- PC の有線 LAN 接続を試みる

---

## ネットワーク構成図

```
[Rebocap sensors]
       │ Bluetooth/USB
       ▼
  [Windows PC]
  Rebocap App
  rebocap-osc-bridge
       │
       │  UDP OSC パケット
       │  (LAN 経由)
       ▼
  [Wi-Fi ルーター]
       │
       │  Wi-Fi (5GHz 推奨)
       ▼
  [Meta Quest]
  VRChat (単体モード)
```

---

## 参考リンク

- [VRChat OSC Trackers ドキュメント](https://docs.vrchat.com/docs/osc-trackers)
- [SlimeVR OSC ガイド](https://docs.slimevr.dev/server/osc-support.html) (OSC 設定の参考に)
- [Meta Quest Wi-Fi 設定](https://www.meta.com/ja-jp/help/quest/articles/headsets-and-accessories/oculus-link/connect-with-air-link/)
