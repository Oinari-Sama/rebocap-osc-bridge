"""
oscquery.py — OSCQuery サーバー (RFC草案準拠)

VRChat は OSCQuery を使って自動的に OSC ポートを検出できます。
このモジュールは軽量な HTTP + mDNS サーバーを提供します。

OSCQuery 仕様:
  https://github.com/Vidvox/OSCQueryProposal
VRChat OSCQuery 対応ドキュメント:
  https://docs.vrchat.com/docs/oscquery
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

# mDNS 広告には zeroconf が必要 (オプション依存)
try:
    from zeroconf import ServiceInfo, Zeroconf  # type: ignore
    _HAS_ZEROCONF = True
except ImportError:
    _HAS_ZEROCONF = False


# VRChat が期待する OSCQuery ホスト情報レスポンス
def _build_host_info(osc_port: int, oscquery_port: int) -> dict:
    return {
        "NAME": "rebocap-osc-bridge",
        "OSC_PORT": osc_port,
        "OSC_TRANSPORT": "UDP",
        "EXTENSIONS": {
            "ACCESS": True,
            "CLIPMODE": False,
            "RANGE": False,
            "TYPE": True,
            "VALUE": False,
        },
    }


# VRChat が期待するノードツリー (トラッカーアドレス一覧)
def _build_node_tree() -> dict:
    trackers = {}
    for i in range(1, 9):
        trackers[str(i)] = {
            "FULL_PATH": f"/tracking/trackers/{i}",
            "CONTENTS": {
                "position": {
                    "FULL_PATH": f"/tracking/trackers/{i}/position",
                    "TYPE": "fff",
                    "ACCESS": 2,
                },
                "rotation": {
                    "FULL_PATH": f"/tracking/trackers/{i}/rotation",
                    "TYPE": "fff",
                    "ACCESS": 2,
                },
            },
        }
    trackers["head"] = {
        "FULL_PATH": "/tracking/trackers/head",
        "CONTENTS": {
            "position": {
                "FULL_PATH": "/tracking/trackers/head/position",
                "TYPE": "fff",
                "ACCESS": 2,
            },
            "rotation": {
                "FULL_PATH": "/tracking/trackers/head/rotation",
                "TYPE": "fff",
                "ACCESS": 2,
            },
        },
    }
    return {
        "FULL_PATH": "/",
        "CONTENTS": {
            "tracking": {
                "FULL_PATH": "/tracking",
                "CONTENTS": {
                    "trackers": {
                        "FULL_PATH": "/tracking/trackers",
                        "CONTENTS": trackers,
                    }
                },
            }
        },
    }


class _OSCQueryHandler(BaseHTTPRequestHandler):
    """OSCQuery HTTP レスポンスハンドラ。"""

    osc_port: int = 9000
    oscquery_port: int = 9001

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            body = json.dumps(_build_node_tree()).encode()
        elif self.path == "/?HOST_INFO":
            body = json.dumps(
                _build_host_info(self.osc_port, self.oscquery_port)
            ).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: N802
        # BaseHTTPRequestHandler のデフォルトログを抑制
        logging.debug("OSCQuery HTTP: " + fmt, *args)


class OSCQueryServer:
    """
    OSCQuery HTTP サーバーと mDNS 広告を管理するクラス。

    使い方:
        server = OSCQueryServer(osc_port=9000, oscquery_port=9001)
        server.start()
        ...
        server.stop()
    """

    def __init__(self, osc_port: int = 9000, oscquery_port: int = 9001):
        self._osc_port = osc_port
        self._oscquery_port = oscquery_port
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._zeroconf: Optional[object] = None
        self._service_info: Optional[object] = None

    def start(self) -> None:
        raise RuntimeError(
            "OSCQuery is temporarily disabled until sender-side discovery "
            "is implemented correctly."
        )
        # ハンドラクラスにポート情報を注入
        handler = type(
            "_Handler",
            (_OSCQueryHandler,),
            {"osc_port": self._osc_port, "oscquery_port": self._oscquery_port},
        )
        self._httpd = HTTPServer(("0.0.0.0", self._oscquery_port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="oscquery-http"
        )
        self._thread.start()
        logging.info(
            "OSCQuery HTTP サーバー起動: http://0.0.0.0:%d", self._oscquery_port
        )
        self._advertise_mdns()

    def _advertise_mdns(self) -> None:
        if not _HAS_ZEROCONF:
            logging.debug(
                "zeroconf がインストールされていないため mDNS 広告をスキップします。\n"
                "  pip install zeroconf  で有効になります。"
            )
            return
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            self._service_info = ServiceInfo(
                "_oscjson._tcp.local.",
                f"rebocap-osc-bridge._oscjson._tcp.local.",
                addresses=[socket.inet_aton(local_ip)],
                port=self._oscquery_port,
                properties={"txtvers": "1"},
            )
            self._zeroconf = Zeroconf()
            self._zeroconf.register_service(self._service_info)
            logging.info("mDNS 広告開始: rebocap-osc-bridge @ %s:%d",
                         local_ip, self._oscquery_port)
        except Exception as e:
            logging.warning("mDNS 広告に失敗しました: %s", e)

    def stop(self) -> None:
        if self._zeroconf and self._service_info:
            try:
                self._zeroconf.unregister_service(self._service_info)
                self._zeroconf.close()
            except Exception:
                pass
        if self._httpd:
            self._httpd.shutdown()
        logging.info("OSCQuery サーバーを停止しました。")
