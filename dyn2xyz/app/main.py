"""main.py —— 命令行入口。

用法::

    python -m app.main [配置文件]        # 启动服务
    python -m app.main --check [配置文件]  # 只做自检：抓配置、推导几何、打印缩放映射
    python -m app.main --sample 3500,-500 [配置文件]
                                        # 打印某个方块坐标在各层级的上下游 URL
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import build_source, load_config
from .geo import xyz_tile_lonlat_bounds
from .render import TileRenderer

DEFAULT_CONFIG_PATHS = (
    os.environ.get("DYN2XYZ_CONFIG", ""),
    "/config/dyn2xyz.toml",
    "config/dyn2xyz.toml",
    "dyn2xyz.toml",
)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def find_config(explicit: str | None) -> str:
    if explicit:
        return explicit
    for p in DEFAULT_CONFIG_PATHS:
        if p and os.path.exists(p):
            return p
    raise SystemExit(
        "找不到配置文件。请用参数指定，或设置环境变量 DYN2XYZ_CONFIG。\n"
        f"已尝试: {[p for p in DEFAULT_CONFIG_PATHS if p]}"
    )


def build_renderer(config_path: str) -> tuple[object, TileRenderer, object]:
    cfg = load_config(config_path)
    source = build_source(cfg)
    renderer = TileRenderer(source, cfg.geo.placement(), cfg.render)
    return cfg, renderer, source


def print_report(cfg, renderer: TileRenderer, source) -> None:
    r = renderer
    g = source.geometry
    print("=" * 78)
    print("dyn2xyz —— 配置自检")
    print("=" * 78)
    print(f"上游 webroot      : {source.webroot}")
    print(f"世界 / 前缀        : {source.world} / {source.prefix}")
    print(f"URL 模板          : {source.template}")
    print(f"瓦片像素           : {g.tile_px} × {g.tile_px}  ({g.image_format})")
    print(f"worldtomap 缩放    : scale_x={g.scale_x:g}  scale_z={g.scale_z:g}")
    print(f"原生分辨率         : {g.blocks_per_pixel_native:g} 方块/像素")
    print(f"mapzoomout        : {g.mapzoomout}   (可用层级 L = 0 … {g.mapzoomout})")
    print(f"最粗层瓦片覆盖      : {g.blocks_per_tile(g.mapzoomout):g} 方块/瓦片")
    print("-" * 78)
    print(f"世界摆放           : 方块 ({cfg.geo.origin_block_x:g}, {cfg.geo.origin_block_z:g})"
          f" -> lon {cfg.geo.origin_lon}, lat {cfg.geo.origin_lat}")
    if cfg.geo.bounds_blocks:
        b = cfg.geo.bounds_blocks
        print(f"数据范围           : 方块 X [{b[0]:g}, {b[2]:g}]  Z [{b[1]:g}, {b[3]:g}]"
              f"  (约 {(b[2] - b[0]) / 1000:.1f}k × {(b[3] - b[1]) / 1000:.1f}k 方块)")
    print(f"渲染比例           : 1 方块 = {cfg.geo.meters_per_block:g} 米")
    print("-" * 78)
    print(f"Dynmap 原生层 ≈ XYZ 层级 : z = {r.native_zoom_exact:.4f}")
    print(f"对外提供的 XYZ 缩放范围   : z{r.minzoom} … z{r.maxzoom}"
          f"   (overzoom={cfg.render.overzoom}, underzoom={cfg.render.underzoom})")
    print(f"输出瓦片像素              : {cfg.render.output_px} × {cfg.render.output_px}")
    print(f"源瓦片数上限              : {cfg.render.max_source_tiles}")
    print("-" * 78)
    print("层级映射（XYZ z -> Dynmap L）：")
    for z in range(r.minzoom, r.maxzoom + 1):
        lv = r.level_for_zoom(z)
        lon0, lat0, lon1, lat1 = xyz_tile_lonlat_bounds(z, 0, 0)
        print(f"  z={z:<3} -> L={lv}   (该层瓦片覆盖 "
              f"{g.blocks_per_tile(lv):g} 方块)")
    print("=" * 78)


def print_sample(renderer: TileRenderer, source, bx: float, bz: float) -> None:
    """打印某处方块坐标的经纬度、XYZ 瓦片坐标与各层级的上游 URL。

    这是给人肉眼核对用的：把 L=max 那一行的 URL 贴进浏览器，
    应该正好是包含该方块的那张 Dynmap 瓦片。
    """
    from .geo import meters_to_xyz

    g = source.geometry
    p = renderer.placement
    lon, lat = p.block_to_lonlat(bx, bz)
    mx, my = p.block_to_meters(bx, bz)
    fx, fy = meters_to_xyz(mx, my, renderer.native_zoom)

    print(f"方块坐标 ({bx:g}, {bz:g})  <->  lon {lon:.6f}, lat {lat:.6f}")
    print(f"  在 z={renderer.native_zoom} 下的小数 XYZ 瓦片坐标: x={fx:.4f}, y={fy:.4f}")
    print(f"  即瓦片 ({int(fx)}, {int(fy)})，瓦片内像素偏移 "
          f"({(fx % 1) * 256:.0f}, {(fy % 1) * 256:.0f})")
    print()
    print("各层级的上游 Dynmap 瓦片 URL（L 越大越粗）：")
    for lv in range(0, g.mapzoomout + 1):
        print(f"  L={lv}  {source.tile_url_for_block(bx, bz, lv)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dyn2xyz", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?", help="TOML 配置文件路径")
    ap.add_argument("--check", action="store_true", help="只自检并打印推导结果，不启动服务")
    ap.add_argument("--sample", metavar="BX,BZ", help="打印某处方块坐标的上下游 URL")
    ap.add_argument("--log-level", default=os.environ.get("DYN2XYZ_LOG_LEVEL", "INFO"))
    args = ap.parse_args(argv)

    setup_logging(args.log_level)
    config_path = find_config(args.config)

    try:
        cfg, renderer, source = build_renderer(config_path)
    except Exception as exc:  # noqa: BLE001
        print(f"配置/数据源初始化失败：{exc}", file=sys.stderr)
        return 2

    print_report(cfg, renderer, source)

    if args.sample:
        try:
            bx, bz = (float(v) for v in args.sample.split(","))
        except ValueError:
            print("--sample 需要形如 3500,-500 的两个数字", file=sys.stderr)
            return 2
        print()
        print_sample(renderer, source, bx, bz)

    if args.check:
        renderer.close()
        return 0

    from .server import TileApp, serve

    print()
    serve(TileApp(cfg, source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
