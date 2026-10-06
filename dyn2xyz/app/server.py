"""server.py —— HTTP 服务：对外提供标准 XYZ 瓦片、TileJSON 与诊断端点。

端点
----
========================================  ==========================================
``GET /tiles/{z}/{x}/{y}.{ext}``          标准 XYZ 瓦片（``/tile/...`` 是别名）
``GET /tilejson.json``                     TileJSON 3.0，供 QGIS / MapLibre 直接导入
``GET /healthz``                           健康检查（容器 healthcheck 用）
``GET /status.json``                       派生出的几何、缩放映射、缓存命中率
``GET /``                                  预览页（Leaflet）
========================================  ==========================================
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PIL import Image

from .config import AppConfig
from .dynmap import DynmapSource
from .geo import Placement, mercator_to_lonlat
from .render import RenderSettings, TileRenderer, TileUnavailable

log = logging.getLogger("dyn2xyz.server")

TILE_RE = re.compile(r"^/(?:tiles|tile)/(\d+)/(-?\d+)/(-?\d+)(?:\.([A-Za-z0-9]+))?$")


class TileApp:
    """把配置好的渲染器 + 元数据组装起来，供 HTTP 层调用。"""

    def __init__(self, cfg: AppConfig, source: DynmapSource) -> None:
        self.cfg = cfg
        self.source = source
        self.renderer = TileRenderer(source, cfg.geo.placement(), cfg.render)
        self._bounds_meters = self._compute_bounds_meters()

    # -- 世界范围 -----------------------------------------------------------
    def _compute_bounds_meters(self) -> tuple[float, float, float, float] | None:
        bb = self.cfg.geo.bounds_blocks
        if not bb:
            return None
        p = self.cfg.geo.placement()
        xs, ys = [], []
        for bx, bz in ((bb[0], bb[1]), (bb[2], bb[3])):
            mx, my = p.block_to_meters(bx, bz)
            xs.append(mx)
            ys.append(my)
        return min(xs), min(ys), max(xs), max(ys)

    def tile_out_of_bounds(self, z: int, x: int, y: int) -> bool:
        from .geo import xyz_tile_meters

        if self._bounds_meters is None:
            return False
        mx0, my0, mx1, my1 = xyz_tile_meters(z, x, y)
        bx0, by0, bx1, by1 = self._bounds_meters
        return mx1 <= bx0 or mx0 >= bx1 or my1 <= by0 or my0 >= by1

    # -- TileJSON -----------------------------------------------------------
    def tilejson(self, base_url: str) -> dict:
        r = self.renderer
        tj: dict = {
            "tilejson": "3.0.0",
            "name": f"dyn2xyz — {self.source.world}/{self.source.prefix} (Dynmap → XYZ)",
            "scheme": "xyz",
            "format": "png",
            "tiles": [f"{base_url}{self.cfg.server.tile_path}/{{z}}/{{x}}/{{y}}.png"],
            "minzoom": r.minzoom,
            "maxzoom": r.maxzoom,
            "attribution": f"Dynmap ({self.source.webroot}) · 经 dyn2xyz 转译",
        }
        p: Placement = self.cfg.geo.placement()
        cx, cy = p.block_to_meters(self.cfg.geo.origin_block_x, self.cfg.geo.origin_block_z)
        lon_c, lat_c = mercator_to_lonlat(cx, cy)
        if self._bounds_meters:
            lon0, lat0 = mercator_to_lonlat(self._bounds_meters[0], self._bounds_meters[1])
            lon1, lat1 = mercator_to_lonlat(self._bounds_meters[2], self._bounds_meters[3])
            tj["bounds"] = [lon0, lat0, lon1, lat1]
        else:
            tj["bounds"] = [-180.0, -85.0511287798066, 180.0, 85.0511287798066]
        tj["center"] = [lon_c, lat_c, max(r.minzoom, min(r.maxzoom, r.native_zoom))]
        return tj

    def status(self) -> dict:
        return {
            "geo": {
                "origin_lon": self.cfg.geo.origin_lon,
                "origin_lat": self.cfg.geo.origin_lat,
                "meters_per_block": self.cfg.geo.meters_per_block,
                "origin_block": [self.cfg.geo.origin_block_x, self.cfg.geo.origin_block_z],
                "bounds_blocks": self.cfg.geo.bounds_blocks,
            },
            "derived": self.renderer.describe(),
        }

    # -- 出图 ---------------------------------------------------------------
    def blank_tile(self, fmt: str) -> tuple[bytes, str]:
        img = Image.new("RGBA", (self.cfg.render.output_px, self.cfg.render.output_px),
                        (0, 0, 0, 0))
        return self.renderer.encode(img, fmt)

    def get_tile(self, z: int, x: int, y: int, fmt: str) -> tuple[bytes, str, str]:
        """返回 ``(数据, Content-Type, 状态标记)``。"""
        if self.tile_out_of_bounds(z, x, y):
            return *self.blank_tile(fmt), "out-of-bounds"
        try:
            body, ctype = self.renderer.tile(z, x, y, fmt)
            return body, ctype, "ok"
        except TileUnavailable as exc:
            log.debug("瓦片 %s/%s/%s 不可用: %s", z, x, y, exc)
            return *self.blank_tile(fmt), "unavailable"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "dyn2xyz"
    protocol_version = "HTTP/1.1"

    app: TileApp  # 由 make_handler 注入

    # -- 工具 ---------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self.app.cfg.server.allow_origin)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: object) -> None:
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _base_url(self) -> str:
        if self.app.cfg.server.public_url:
            return self.app.cfg.server.public_url.rstrip("/")
        host = self.headers.get("Host") or f"{self.app.cfg.server.host}:{self.app.cfg.server.port}"
        return f"http://{host}"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("%s - %s", self.address_string(), fmt % args)

    # -- 路由 ---------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._route()

    def do_HEAD(self) -> None:  # noqa: N802
        self._route()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")

    def _route(self) -> None:
        path = urlparse(self.path).path
        try:
            m = TILE_RE.match(path)
            if m:
                z = int(m.group(1))
                x = int(m.group(2))
                y = int(m.group(3))
                fmt = (m.group(4) or "png").lower()
                if not (0 <= z <= 30):
                    self._send(HTTPStatus.BAD_REQUEST, b"zoom out of range", "text/plain")
                    return
                if not (0 <= y < (1 << z)):
                    # 行号越界在 XYZ 里是非法请求
                    self._send(HTTPStatus.BAD_REQUEST, b"y out of range", "text/plain")
                    return
                body, ctype, state = self.app.get_tile(z, x, y, fmt)
                self._send(HTTPStatus.OK, body, ctype, {
                    "Cache-Control": "public, max-age=300",
                    "X-Dyn2xyz-State": state,
                    "X-Dyn2xyz-Level": str(self.app.renderer.level_for_zoom(z)),
                })
                return

            if path in ("/tilejson.json", "/tiles.json"):
                self._json(HTTPStatus.OK, self.app.tilejson(self._base_url()))
                return
            if path == "/status.json":
                self._json(HTTPStatus.OK, self.app.status())
                return
            if path == "/healthz":
                self._send(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8",
                           {"Cache-Control": "no-store"})
                return
            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, self._preview().encode("utf-8"),
                           "text/html; charset=utf-8", {"Cache-Control": "no-store"})
                return

            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8",
                       {"Cache-Control": "no-store"})
        except BrokenPipeError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("处理 %s 时出错", path)
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc).encode(), "text/plain")

    # -- 预览页 -------------------------------------------------------------
    def _preview(self) -> str:
        r = self.app.renderer
        s = self.app.source
        base = self._base_url()
        tj = self.app.tilejson(base)
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>dyn2xyz — {s.world}/{s.prefix}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{{height:100%;margin:0}}
#hud{{position:absolute;z-index:999;top:8px;right:8px;background:#fffc;padding:8px 10px;
font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;border-radius:6px;max-width:320px}}</style>
</head><body>
<div id="map"></div>
<div id="hud">
<b>dyn2xyz</b><br>
源: {s.webroot}<br>
地图: {s.world} / {s.prefix}<br>
原生层: {r.source.geometry.blocks_per_pixel_native:g} 方块/像素<br>
缩放范围: z{r.minzoom} … z{r.maxzoom}<br>
<span id="cur"></span>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const tj = {json.dumps(tj)};
const map = L.map('map', {{center:[tj.center[1],tj.center[0]], zoom:tj.center[2],
  minZoom:tj.minzoom, maxZoom:tj.maxzoom, continuousWorld:true}});
L.tileLayer(tj.tiles[0], {{minZoom:tj.minzoom, maxZoom:tj.maxzoom, tileSize:256}}).addTo(map);
map.on('mousemove', e => {{
  document.getElementById('cur').textContent =
    `光标: ${{e.latlng.lat.toFixed(1)}}, ${{e.latlng.lng.toFixed(1)}} | z${{map.getZoom()}}`;
}});
</script></body></html>"""


def make_server(app: TileApp, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def serve(app: TileApp) -> None:
    httpd = make_server(app, app.cfg.server.host, app.cfg.server.port)
    log.info("dyn2xyz 监听 %s:%s", app.cfg.server.host, app.cfg.server.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        app.renderer.close()
