#!/usr/bin/env python3
"""不依赖网络的单元测试。

其中一组测试用的是**实测过的真实上游 URL**（见 tests/verify_upstream_geometry.py 与
README 的「验证」一节）：对同一处方块坐标 (3500, -500)，BiliCraft 的 Dynmap 在
L=0..5 各层确实返回 200，URL 如下。这些就是本程序的黄金测试向量。

运行::

    python3 -m unittest discover -s tests -p 'test_*.py' -v
"""

from __future__ import annotations

import json
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.dynmap import (  # noqa: E402
    DynmapGeometry,
    SampleParseError,
    SourceError,
    find_map,
    geometry_from_map_config,
    parse_sample_url,
)
from app.geo import (  # noqa: E402
    EQ_M_PER_PX_Z0,
    LAT_LIMIT,
    ORIGIN_SHIFT,
    WORLD_WIDTH,
    Placement,
    lonlat_to_mercator,
    mercator_to_lonlat,
    meters_to_xyz,
    xyz_tile_meters,
)
from app.render import parse_color  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures_dynmap_config.json"

SAMPLE_URL = ("https://map.bilicraft.com/s2/hyperion/tiles/Paralon/flat/"
              "3_1/zzzzz_96_32.jpg?timestamp=1790688914513")

#: 实测确认存在的上游 URL（按层级 L 从细到粗）
VERIFIED_LEVEL_URLS = {
    0: "Paralon/flat/3_0/109_15.jpg",
    1: "Paralon/flat/3_0/z_108_16.jpg",
    2: "Paralon/flat/3_0/zz_108_16.jpg",
    3: "Paralon/flat/3_0/zzz_104_16.jpg",
    4: "Paralon/flat/3_0/zzzz_96_16.jpg",
    5: "Paralon/flat/3_1/zzzzz_96_32.jpg",
}

#: 探针方块：落在上面那条样例 URL 覆盖的范围内
PROBE_BLOCK = (3500.0, -500.0)


class TestGeoConstants(unittest.TestCase):
    """墨卡托常数必须与 GIS 生态逐位一致。"""

    def test_constants(self):
        self.assertAlmostEqual(ORIGIN_SHIFT, 20037508.342789244, places=6)
        self.assertAlmostEqual(WORLD_WIDTH, 40075016.68557849, places=5)
        self.assertAlmostEqual(LAT_LIMIT, 85.0511287798066, places=10)
        self.assertAlmostEqual(EQ_M_PER_PX_Z0, 156543.03392804097, places=8)

    def test_lonlat_roundtrip(self):
        for lon, lat in ((0, 0), (121.47, 31.23), (-179.9, -84.0), (12.5, 41.9)):
            with self.subTest(lon=lon, lat=lat):
                mx, my = lonlat_to_mercator(lon, lat)
                lon2, lat2 = mercator_to_lonlat(mx, my)
                self.assertAlmostEqual(lon, lon2, places=9)
                self.assertAlmostEqual(lat, lat2, places=9)

    def test_lat_clamped(self):
        _, my = lonlat_to_mercator(0.0, 89.9)
        self.assertAlmostEqual(my, ORIGIN_SHIFT, places=6)

    def test_xyz_tile_zero(self):
        """z=0 的单张瓦片覆盖整个世界。"""
        mx0, my0, mx1, my1 = xyz_tile_meters(0, 0, 0)
        self.assertAlmostEqual(mx0, -ORIGIN_SHIFT, places=6)
        self.assertAlmostEqual(my1, ORIGIN_SHIFT, places=6)
        self.assertAlmostEqual(mx1 - mx0, WORLD_WIDTH, places=5)

    def test_xyz_tile_equator_origin(self):
        """经纬度 (0,0) 落在 z 层正中间那张瓦片的左上角。"""
        for z in (1, 5, 12, 19):
            with self.subTest(z=z):
                fx, fy = meters_to_xyz(0.0, 0.0, z)
                self.assertAlmostEqual(fx, 2 ** (z - 1), places=9)
                self.assertAlmostEqual(fy, 2 ** (z - 1), places=9)


class TestPlacement(unittest.TestCase):
    """世界摆放：默认 方块(0,0) -> 赤道×本初子午线，1 方块 = 1 米。"""

    def test_default_origin(self):
        p = Placement()
        # 注意 log(tan(pi/4)) 在浮点下是 -1.1e-16 而非 0，乘 R 后约 0.7 纳米。
        # 这个量级对米制瓦片毫无影响，但断言不能写死等号。
        mx, my = p.block_to_meters(0.0, 0.0)
        self.assertAlmostEqual(mx, 0.0, places=6)
        self.assertAlmostEqual(my, 0.0, places=6)
        lon, lat = p.block_to_lonlat(0.0, 0.0)
        self.assertAlmostEqual(lon, 0.0, places=9)
        self.assertAlmostEqual(lat, 0.0, places=9)

    def test_axes(self):
        """+X 向东（经度增），+Z 向南（纬度减）。"""
        p = Placement()
        lon_e, lat_e = p.block_to_lonlat(1000.0, 0.0)
        lon_s, lat_s = p.block_to_lonlat(0.0, 1000.0)
        self.assertGreater(lon_e, 0.0)
        self.assertAlmostEqual(lat_e, 0.0, places=9)
        self.assertAlmostEqual(lon_s, 0.0, places=9)
        self.assertLess(lat_s, 0.0)

    def test_roundtrip(self):
        p = Placement(origin_lon=8.5, origin_lat=47.3, meters_per_block=3.0,
                      origin_block_x=128.0, origin_block_z=-64.0)
        for bx, bz in ((0, 0), (1000, -2000), (-5000, 5000), (128, -64)):
            with self.subTest(bx=bx, bz=bz):
                b2 = p.meters_to_block(*p.block_to_meters(bx, bz))
                self.assertAlmostEqual(bx, b2[0], places=6)
                self.assertAlmostEqual(bz, b2[1], places=6)

    def test_meters_per_block_scales(self):
        """渲染比例放大 2 倍，同一个方块就跨 2 倍的经纬度距离。"""
        a = Placement().block_to_lonlat(1000.0, 0.0)[0]
        b = Placement(meters_per_block=2.0).block_to_lonlat(1000.0, 0.0)[0]
        self.assertAlmostEqual(b / a, 2.0, places=9)

    def test_tile_block_bounds_square_at_equator(self):
        """赤道附近 XYZ 瓦片的方块范围应当是正方形（这正是选赤道做原点的好处）。"""
        p = Placement()
        for z, x, y in ((19, 262144, 262144), (17, 65536, 65536)):
            with self.subTest(z=z):
                bx0, bz0, bx1, bz1 = p.xyz_tile_block_bounds(z, x, y)
                self.assertAlmostEqual(bx1 - bx0, bz1 - bz0, places=6)

    def test_tile_block_bounds_are_negative_south(self):
        """y 小于赤道所在行时，方块 Z 应当为负（北）。"""
        p = Placement()
        # z=19 时赤道在第 2^18 = 262144 行
        bx0, bz0, bx1, bz1 = p.xyz_tile_block_bounds(19, 262144, 262143)
        self.assertLess(bz1, 0.0)


class TestSampleUrlParsing(unittest.TestCase):
    def test_valid(self):
        s = parse_sample_url(SAMPLE_URL)
        self.assertEqual(s.webroot, "https://map.bilicraft.com/s2/hyperion")
        self.assertEqual(s.world, "Paralon")
        self.assertEqual(s.prefix, "flat")
        self.assertEqual(s.image_format, "jpg")
        self.assertEqual(s.level, 5)
        self.assertEqual((s.filename_x, s.filename_y), (96, 32))

    def test_level_zero_has_no_zoom_prefix(self):
        """层级 0 的文件名没有 z 前缀，也没有前导下划线。"""
        s = parse_sample_url("https://h/tiles/w/flat/3_0/109_15.jpg")
        self.assertEqual(s.level, 0)
        self.assertEqual((s.filename_x, s.filename_y), (109, 15))

    def test_negative_coordinates(self):
        s = parse_sample_url("https://h/tiles/w/flat/-1_-1/zz_-32_-1.png")
        self.assertEqual(s.level, 2)
        self.assertEqual((s.filename_x, s.filename_y), (-32, -1))

    def test_nightday_suffix(self):
        s = parse_sample_url("https://h/tiles/w/flat_day/3_0/109_15.jpg")
        self.assertEqual(s.prefix, "flat_day")
        self.assertEqual(s.base_prefix, "flat")
        self.assertEqual(s.nightday_suffix, "_day")

    def test_rejects_inconsistent_shard(self):
        """分片目录必须等于坐标 >> 5 —— 这是防粘错地图的自检。"""
        with self.assertRaises(SampleParseError):
            parse_sample_url("https://h/tiles/w/flat/9_9/zzzzz_96_32.jpg")

    def test_rejects_missing_tiles_segment(self):
        with self.assertRaises(SampleParseError):
            parse_sample_url("https://h/w/flat/3_1/zzzzz_96_32.jpg")

    def test_rejects_bad_zoom_prefix(self):
        with self.assertRaises(SampleParseError):
            parse_sample_url("https://h/tiles/w/flat/3_1/qq_96_32.jpg")


class TestGeometry(unittest.TestCase):
    """几何推导 —— 与实测 URL 对齐。"""

    @classmethod
    def setUpClass(cls):
        cfg = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.map_cfg = find_map(cfg, "Paralon", "flat")
        cls.geom = geometry_from_map_config(cls.map_cfg)

    def test_geometry_from_real_config(self):
        g = self.geom
        self.assertEqual(g.tile_px, 128)
        self.assertEqual(g.mapzoomout, 5)
        self.assertEqual(g.mapzoomin, 4)
        self.assertEqual(g.scale_x, 4.0)
        self.assertEqual(g.scale_z, -4.0)
        self.assertEqual(g.image_format, "jpg")

    def test_native_resolution(self):
        self.assertAlmostEqual(self.geom.blocks_per_pixel_native, 0.25, places=12)
        self.assertAlmostEqual(self.geom.blocks_per_tile(0), 32.0, places=9)
        self.assertAlmostEqual(self.geom.blocks_per_tile(5), 1024.0, places=9)

    def test_matches_every_verified_upstream_url(self):
        """核心测试：对同一处方块，各层级算出的相对路径必须与实测 URL 完全一致。"""
        base = "https://map.bilicraft.com/s2/hyperion/tiles/"
        for level, expected in VERIFIED_LEVEL_URLS.items():
            with self.subTest(level=level):
                nlx, nly = self.geom.tile_index(*PROBE_BLOCK, level)
                stem = self.geom.tile_stem(nlx, nly, level)
                sx, sy = self.geom.tile_shard(nlx, nly)
                got = f"Paralon/flat/{sx}_{sy}/{stem}.jpg"
                self.assertEqual(got, expected)
                self.assertTrue(base.endswith("/tiles/"))

    def test_reproduces_the_sample_url_exactly(self):
        """用户给的那条样例 URL 必须能被原样重算出来。"""
        parsed = parse_sample_url(SAMPLE_URL)
        nlx, nly = self.geom.tile_index(*PROBE_BLOCK, parsed.level)
        self.assertEqual((nlx, -nly), (parsed.filename_x, parsed.filename_y))
        self.assertEqual(self.geom.tile_shard(nlx, nly),
                         (parsed.filename_x >> 5, parsed.filename_y >> 5))

    def test_tile_bounds_cover_probe_block(self):
        """瓦片覆盖的方块范围必须真的包含探针方块。"""
        for level in range(0, 6):
            with self.subTest(level=level):
                nlx, nly = self.geom.tile_index(*PROBE_BLOCK, level)
                x0, z0, x1, z1 = self.geom.tile_block_bounds(nlx, nly, level)
                self.assertLessEqual(x0, PROBE_BLOCK[0])
                self.assertGreaterEqual(x1, PROBE_BLOCK[0])
                self.assertLessEqual(z0, PROBE_BLOCK[1])
                self.assertGreaterEqual(z1, PROBE_BLOCK[1])
                span = self.geom.blocks_per_tile(level)
                self.assertAlmostEqual(x1 - x0, span, places=6)

    def test_level_pixels_roundtrip(self):
        for level in range(0, 6):
            with self.subTest(level=level):
                px, py = self.geom.level_pixels(*PROBE_BLOCK, level)
                bx, bz = self.geom.block_from_level_pixels(px, py, level)
                self.assertAlmostEqual(bx, PROBE_BLOCK[0], places=6)
                self.assertAlmostEqual(bz, PROBE_BLOCK[1], places=6)

    def test_level_pixel_grid_matches_tile_index(self):
        """回归测试：层级 L 像素空间里的「粗索引 << L」必须等于 Dynmap 瓦片索引。

        这条不变量曾经被写漏（少乘了 2^L），导致 L>0 的层级算出空白瓦片，
        而 L=0 却正常——所以必须逐层覆盖。
        """
        t = self.geom.tile_px
        blocks = [PROBE_BLOCK, (0.0, 0.0), (-1234.5, 6789.0), (-30000.0, -30000.0)]
        for level in range(0, 6):
            for bx, bz in blocks:
                with self.subTest(level=level, block=(bx, bz)):
                    px, py = self.geom.level_pixels(bx, bz, level)
                    nlx, nly = self.geom.tile_index(bx, bz, level)
                    self.assertEqual(math.floor(px / t) << level, nlx)
                    self.assertEqual(math.floor(py / t) << level, nly)

    def test_tile_window_stays_within_source_tiles(self):
        """回归测试：由像素窗口推出的源瓦片范围必须真的覆盖该 XYZ 瓦片。

        这条不变量曾经被写漏（少乘了 2^L），导致 L>0 的层级抓到错误的源瓦片、
        输出空白，而 L=0 却完全正常。所以必须逐层覆盖。
        """
        from app.dynmap import DynmapSource
        from app.geo import WORLD_WIDTH
        from app.render import RenderSettings, TileRenderer

        src = DynmapSource(webroot="http://example.invalid", world="w", prefix="flat",
                           geometry=self.geom)
        r = TileRenderer(src, Placement(), RenderSettings())
        try:
            self.assertEqual((r.minzoom, r.maxzoom), (14, 21))
            for z in range(r.minzoom, r.maxzoom + 1):
                level = r.level_for_zoom(z)
                t = self.geom.tile_px
                mid = 2 ** (z - 1)
                for x, y in ((mid, mid), (mid + 3, mid - 5), (mid - 100, mid + 40)):
                    with self.subTest(z=z, x=x, y=y):
                        bx0, bz0, bx1, bz1 = r.placement.xyz_tile_block_bounds(z, x, y)
                        px0, py0 = self.geom.level_pixels(bx0, bz0, level)
                        px1, py1 = self.geom.level_pixels(bx1, bz1, level)
                        kx0 = math.floor(min(px0, px1) / t)
                        ky0 = math.floor(min(py0, py1) / t)
                        kx1 = math.floor((max(px0, px1) - 1e-9) / t)
                        ky1 = math.floor((max(py0, py1) - 1e-9) / t)
                        # 覆盖该窗口的所有源瓦片，其方块范围的并集必须包含 XYZ 瓦片
                        sx0, sz0, sx1, sz1 = self.geom.tile_block_bounds(
                            kx0 << level, ky0 << level, level)
                        ex0, ez0, ex1, ez1 = self.geom.tile_block_bounds(
                            kx1 << level, ky1 << level, level)
                        self.assertLessEqual(sx0, min(bx0, bx1) + 1e-6)
                        self.assertGreaterEqual(ex1, max(bx0, bx1) - 1e-6)
                        self.assertLessEqual(sz0, min(bz0, bz1) + 1e-6)
                        self.assertGreaterEqual(ez1, max(bz0, bz1) - 1e-6)
                        # 该层需要的源瓦片数必须在上限内，否则 minzoom 就设错了
                        n = (kx1 - kx0 + 1) * (ky1 - ky0 + 1)
                        self.assertLessEqual(n, RenderSettings().max_source_tiles,
                                             f"z={z} 需要 {n} 个源瓦片")
        finally:
            r.close()

    def test_every_level_is_reachable_and_nonblank_by_construction(self):
        """每个对外声明的缩放层级都必须映射到一个真实存在的 Dynmap 层级。"""
        from app.dynmap import DynmapSource
        from app.render import RenderSettings, TileRenderer

        src = DynmapSource(webroot="http://example.invalid", world="w", prefix="flat",
                           geometry=self.geom)
        r = TileRenderer(src, Placement(), RenderSettings())
        try:
            seen = set()
            for z in range(r.minzoom, r.maxzoom + 1):
                lv = r.level_for_zoom(z)
                self.assertGreaterEqual(lv, 0)
                self.assertLessEqual(lv, self.geom.mapzoomout)
                seen.add(lv)
            # 从最粗到原生层每一层都应该被用到，不应有「跳层」
            self.assertEqual(seen, set(range(0, self.geom.mapzoomout + 1)))
        finally:
            r.close()

    def test_rejects_isometric_map(self):
        """等轴测图（surface）必须被明确拒绝，而不是悄悄出错误的地图。"""
        cfg = json.loads(FIXTURE.read_text(encoding="utf-8"))
        iso = find_map(cfg, "Paralon", "t")
        with self.assertRaises(SourceError) as ctx:
            geometry_from_map_config(iso)
        self.assertIn("俯视图", str(ctx.exception))

    def test_unknown_map_lists_alternatives(self):
        cfg = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with self.assertRaises(SourceError) as ctx:
            find_map(cfg, "NoSuchWorld", "flat")
        self.assertIn("Paralon/flat", str(ctx.exception))

    def test_rejects_mirrored_matrix(self):
        with self.assertRaises(SourceError):
            geometry_from_map_config({
                "worldtomap": [-4.0, 0.0, 0.0, 0.0, 0.0, -4.0, 0.0, 1.0, 0.0],
                "tilescale": 0, "mapzoomout": 3,
            })


class TestZoomMapping(unittest.TestCase):
    """缩放层级的换算关系。"""

    def test_native_zoom_value(self):
        # z = log2(2*pi*R * 4 / 256) = log2(626172.13...) = 19.2562...
        expected = math.log2(WORLD_WIDTH * 4.0 / 256.0)
        self.assertAlmostEqual(expected, 19.25621, places=4)

    def test_geometry_rejects_bad_tile_px(self):
        with self.assertRaises(SourceError):
            DynmapGeometry(tile_px=100, mapzoomout=1, mapzoomin=0,
                           scale_x=4.0, scale_z=-4.0)


class TestColorParsing(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_color("#fff"), (255, 255, 255, 255))
        self.assertEqual(parse_color("#00000000"), (0, 0, 0, 0))
        self.assertEqual(parse_color("#1a2b3c"), (26, 43, 60, 255))
        self.assertEqual(parse_color("#1a2b3c80"), (26, 43, 60, 128))

    def test_rejects_bad(self):
        with self.assertRaises(ValueError):
            parse_color("#12345")


if __name__ == "__main__":
    unittest.main(verbosity=2)
