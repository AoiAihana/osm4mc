"""config.py —— 读取并校验 TOML 配置，组装出可用的瓦片源。

配置支持三种指定瓦片来源的方式（详见 config/dyn2xyz.toml 的注释）：

1. ``source.sample_url``  —— 直接从浏览器里复制一条真实瓦片 URL（推荐）
2. ``source.webroot`` + ``source.world`` + ``source.prefix``
3. ``source.template``    —— 完全自定义 URL 模板

几何参数默认从 ``<webroot>/standalone/dynmap_config.json`` 自动获取；
若该文件不可达，可在 ``[source.geometry]`` 里手工覆盖。
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dynmap import (
    DEFAULT_TEMPLATE,
    DEFAULT_USER_AGENT,
    DynmapGeometry,
    DynmapSource,
    SourceError,
    fetch_dynmap_config,
    find_map,
    geometry_from_map_config,
    parse_sample_url,
)
from .geo import Placement
from .render import RenderSettings

log = logging.getLogger("dyn2xyz.config")


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    public_url: str = ""
    tile_path: str = "/tiles"
    allow_origin: str = "*"


@dataclass
class GeoConfig:
    origin_lon: float = 0.0
    origin_lat: float = 0.0
    meters_per_block: float = 1.0
    origin_block_x: float = 0.0
    origin_block_z: float = 0.0
    bounds_blocks: list[float] | None = None

    def placement(self) -> Placement:
        return Placement(
            origin_lon=self.origin_lon,
            origin_lat=self.origin_lat,
            meters_per_block=self.meters_per_block,
            origin_block_x=self.origin_block_x,
            origin_block_z=self.origin_block_z,
        )


@dataclass
class SourceConfig:
    sample_url: str | None = None
    webroot: str | None = None
    world: str | None = None
    prefix: str | None = None
    image_format: str | None = None
    config_path: str = "standalone/dynmap_config.json"
    template: str | None = None
    extra_query: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 20.0
    manual: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    geo: GeoConfig = field(default_factory=GeoConfig)
    render: RenderSettings = field(default_factory=RenderSettings)
    source: SourceConfig = field(default_factory=SourceConfig)


def _pick(d: dict[str, Any], key: str, default: Any = None) -> Any:
    v = d.get(key, default)
    return default if v is None else v


def load_config(path: str | Path) -> AppConfig:
    """读取 TOML 配置文件。

    少数几个键可以被环境变量覆盖，方便在 Docker 里不重建镜像就改行为：

    ==========================  ============================
    环境变量                     覆盖的配置键
    ==========================  ============================
    ``DYN2XYZ_HOST``            ``server.host``
    ``DYN2XYZ_PORT``            ``server.port``
    ``DYN2XYZ_PUBLIC_URL``      ``server.public_url``
    ``DYN2XYZ_ALLOW_ORIGIN``    ``server.allow_origin``
    ``DYN2XYZ_SAMPLE_URL``      ``source.sample_url``
    ``DYN2XYZ_ORIGIN_LON``      ``geo.origin_lon``
    ``DYN2XYZ_ORIGIN_LAT``      ``geo.origin_lat``
    ``DYN2XYZ_METERS_PER_BLOCK`` ``geo.meters_per_block``
    ``DYN2XYZ_OVERZOOM``        ``render.overzoom``
    ``DYN2XYZ_UNDERZOOM``       ``render.underzoom``
    ``DYN2XYZ_CACHE_TILES``     ``render.cache_tiles``
    ==========================  ============================
    """
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    env = os.environ

    srv = raw.get("server", {}) or {}
    geo = raw.get("geo", {}) or {}
    rnd = raw.get("render", {}) or {}
    src = raw.get("source", {}) or {}
    out = raw.get("output", {}) or {}

    server = ServerConfig(
        host=str(_pick(srv, "host", env.get("DYN2XYZ_HOST", "0.0.0.0"))),
        port=int(env.get("DYN2XYZ_PORT") or _pick(srv, "port", 8080)),
        public_url=str(env.get("DYN2XYZ_PUBLIC_URL") or _pick(srv, "public_url", "")),
        tile_path=str(_pick(srv, "tile_path", "/tiles")),
        allow_origin=str(env.get("DYN2XYZ_ALLOW_ORIGIN") or _pick(srv, "allow_origin", "*")),
    )

    bounds = geo.get("bounds_blocks")
    if bounds is not None:
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("geo.bounds_blocks 必须是 [min_x, min_z, max_x, max_z]")
        bounds = [float(v) for v in bounds]

    geocfg = GeoConfig(
        origin_lon=float(env.get("DYN2XYZ_ORIGIN_LON") or _pick(geo, "origin_lon", 0.0)),
        origin_lat=float(env.get("DYN2XYZ_ORIGIN_LAT") or _pick(geo, "origin_lat", 0.0)),
        meters_per_block=float(env.get("DYN2XYZ_METERS_PER_BLOCK")
                               or _pick(geo, "meters_per_block", 1.0)),
        origin_block_x=float(_pick(geo, "origin_block_x", 0.0)),
        origin_block_z=float(_pick(geo, "origin_block_z", 0.0)),
        bounds_blocks=bounds,
    )
    if not -90.0 <= geocfg.origin_lat <= 90.0:
        raise ValueError("geo.origin_lat 必须在 [-90, 90] 内")
    if geocfg.meters_per_block <= 0:
        raise ValueError("geo.meters_per_block 必须为正数")

    render = RenderSettings(
        output_px=int(_pick(out, "tile_px", 256)),
        jpeg_quality=int(_pick(out, "jpeg_quality", 82)),
        background=str(_pick(out, "background", "#00000000")),
        flatten=str(_pick(out, "flatten", "#000000")),
        overzoom=int(env.get("DYN2XYZ_OVERZOOM") or _pick(rnd, "overzoom", 2)),
        underzoom=int(env.get("DYN2XYZ_UNDERZOOM") or _pick(rnd, "underzoom", 0)),
        max_source_tiles=int(_pick(rnd, "max_source_tiles", 16)),
        render_deadline=float(env.get("DYN2XYZ_RENDER_DEADLINE")
                              or _pick(rnd, "render_deadline", 10.0)),
        fetch_workers=int(_pick(rnd, "fetch_workers", 8)),
        fetch_timeout=float(_pick(rnd, "fetch_timeout", 20.0)),
        fetch_retries=int(_pick(rnd, "fetch_retries", 2)),
        cache_tiles=int(env.get("DYN2XYZ_CACHE_TILES") or _pick(rnd, "cache_tiles", 1024)),
        resample=str(_pick(rnd, "resample", "bilinear")),
    )
    if render.output_px <= 0 or render.output_px > 4096:
        raise ValueError("output.tile_px 必须在 1..4096 内")
    if render.resample not in ("nearest", "bilinear", "bicubic", "lanczos"):
        raise ValueError("render.resample 必须是 nearest/bilinear/bicubic/lanczos 之一")
    if render.max_source_tiles < 1:
        raise ValueError("render.max_source_tiles 必须 >= 1")

    source = SourceConfig(
        sample_url=env.get("DYN2XYZ_SAMPLE_URL") or src.get("sample_url"),
        webroot=src.get("webroot"),
        world=src.get("world"),
        prefix=src.get("prefix"),
        image_format=src.get("image_format"),
        config_path=str(_pick(src, "config_path", "standalone/dynmap_config.json")),
        template=src.get("template"),
        extra_query=str(_pick(src, "extra_query", "")),
        user_agent=str(_pick(src, "user_agent", DEFAULT_USER_AGENT)),
        timeout=float(_pick(src, "timeout", 20.0)),
        manual=dict(src.get("geometry", {}) or {}),
    )

    return AppConfig(server=server, geo=geocfg, render=render, source=source)


def _manual_geometry(manual: dict[str, Any]) -> DynmapGeometry | None:
    """如果用户在 ``[source.geometry]`` 里给全了参数，就直接构造。"""
    needed = ("tile_px", "mapzoomout", "scale_x", "scale_z")
    if not all(k in manual for k in needed):
        return None
    return DynmapGeometry(
        tile_px=int(manual["tile_px"]),
        mapzoomout=int(manual["mapzoomout"]),
        mapzoomin=int(manual.get("mapzoomin", 0)),
        scale_x=float(manual["scale_x"]),
        scale_z=float(manual["scale_z"]),
        image_format=str(manual.get("image_format", "png")),
    )


def build_source(cfg: AppConfig) -> DynmapSource:
    """按配置解析出 :class:`DynmapSource`。

    几何参数的优先级：

    1. **抓 ``dynmap_config.json``**（权威来源，还能顺带做一致性校验）；
    2. 抓不到就退回 ``[source.geometry]`` 里手工写的值。

    第 2 条很重要：上游是第三方服务，会抖动（实测遇到过 Cloudflare 报
    ``525 SSL Handshake Failed with Origin Server``）。如果启动时非抓到不可，
    一次抖动就会让容器起不来——而它本来只是要几个常量。所以配置文件里预置了
    一份已验证的几何，抓不到也能正常启动，只是少了一层自检。
    """
    src = cfg.source

    parsed = None
    if src.sample_url:
        parsed = parse_sample_url(src.sample_url)

    webroot = src.webroot or (parsed.webroot if parsed else None)
    world = src.world or (parsed.world if parsed else None)
    prefix = src.prefix or (parsed.prefix if parsed else None)
    image_format = src.image_format or (parsed.image_format if parsed else None)

    if not webroot or not world or not prefix:
        raise SourceError(
            "瓦片来源信息不足：请提供 source.sample_url，"
            "或同时提供 source.webroot / source.world / source.prefix。"
        )

    manual_geometry = _manual_geometry(src.manual)

    geometry: DynmapGeometry | None = None
    try:
        config = fetch_dynmap_config(webroot, timeout=src.timeout,
                                     config_path=src.config_path,
                                     user_agent=src.user_agent)
        map_cfg = find_map(config, world, prefix)
        geometry = geometry_from_map_config(
            map_cfg,
            max_level_override=int(src.manual["mapzoomout"]) if "mapzoomout" in src.manual else None,
        )
        if image_format is None:
            image_format = geometry.image_format
        if manual_geometry is not None:
            log.info("已从上游读到权威几何参数；[source.geometry] 里的手工值仅作兜底")
    except SourceError as exc:
        if manual_geometry is None:
            raise
        log.warning("抓取上游配置失败（%s），改用 [source.geometry] 里的手工几何参数启动。"
                    "瓦片仍可正常转译，只是少了一层与上游的一致性校验。", exc)
        geometry = manual_geometry

    assert geometry is not None  # 上面两条分支必居其一

    # 自检：样例 URL 的层级不能超过地图实际提供的层级
    if parsed is not None and parsed.level > geometry.mapzoomout:
        raise SourceError(
            f"样例 URL 的缩放层级为 {parsed.level}（{parsed.level} 个 z），"
            f"但该地图的 mapzoomout 只有 {geometry.mapzoomout}。"
            "样例 URL 可能不是这张地图的瓦片。"
        )

    geometry = DynmapGeometry(
        tile_px=geometry.tile_px,
        mapzoomout=geometry.mapzoomout,
        mapzoomin=geometry.mapzoomin,
        scale_x=geometry.scale_x,
        scale_z=geometry.scale_z,
        image_format=image_format or geometry.image_format,
    )

    return DynmapSource(
        webroot=webroot,
        world=world,
        prefix=prefix,
        geometry=geometry,
        template=src.template or DEFAULT_TEMPLATE,
        extra_query=src.extra_query,
        user_agent=src.user_agent,
        timeout=src.timeout,
    )
