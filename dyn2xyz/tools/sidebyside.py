"""生成「dyn2xyz 渲染」与「直接裁剪上游」的并排对比图，用于肉眼核对。

这是给人看的验证工具：左边是本程序的输出，右边是把上游瓦片直接拼起来、
按同一方块窗口裁剪缩放的结果。两者应当看起来完全一样。

用法::

    python3 tools/sidebyside.py [配置文件] [z] [x] [y] [输出文件]

不给 z/x/y 时，默认取包含方块 (3500, -500) 的原生层瓦片。
"""
import io, math, sys, urllib.request
sys.path.insert(0, ".")
from PIL import Image, ImageChops, ImageStat, ImageDraw
from app.config import load_config, build_source
from app.render import TileRenderer

argv = sys.argv[1:]
cfg_path = argv[0] if len(argv) > 0 else "config/dyn2xyz.toml"
cfg = load_config(cfg_path); src = build_source(cfg)
r = TileRenderer(src, cfg.geo.placement(), cfg.render)
g = src.geometry; t = g.tile_px; w = cfg.render.output_px
from app.geo import meters_to_xyz
if len(argv) >= 4:
    Z, X, Y = int(argv[1]), int(argv[2]), int(argv[3])
else:
    Z = r.native_zoom
    mx, my = cfg.geo.placement().block_to_meters(3500.0, -500.0)
    fx, fy = meters_to_xyz(mx, my, Z)
    X, Y = int(fx), int(fy)
out_path = argv[4] if len(argv) >= 5 else "compare.png"

mine = r.render(Z, X, Y)

bx0, bz0, bx1, bz1 = cfg.geo.placement().xyz_tile_block_bounds(Z, X, Y)
level = r.level_for_zoom(Z)
px0, py0 = g.level_pixels(bx0, bz0, level)
px1, py1 = g.level_pixels(bx1, bz1, level)
px0, px1 = sorted((px0, px1)); py0, py1 = sorted((py0, py1))
kx0, ky0 = math.floor(px0 / t), math.floor(py0 / t)
nkx = math.floor((px1 - 1e-9) / t) - kx0 + 1
nky = math.floor((py1 - 1e-9) / t) - ky0 + 1
print(f"窗口 {px1-px0:.2f}x{py1-py0:.2f} 源像素，需 {nkx}x{nky} 张上游瓦片")

ref = Image.new("RGBA", (nkx * t, nky * t), (0, 0, 0, 0))
for j in range(nky):
    for i in range(nkx):
        u = src.tile_url(kx0 + i, ky0 + j, level)
        try:
            with urllib.request.urlopen(u, timeout=45) as resp:
                with Image.open(io.BytesIO(resp.read())) as im:
                    ref.paste(im.convert("RGBA"), (i * t, j * t))
        except Exception as e:
            print("  缺失:", u.split('/tiles/')[-1], e)
ox, oy = round(px0 - kx0 * t), round(py0 - ky0 * t)
refimg = ref.crop((ox, oy, ox + round(px1 - px0), oy + round(py1 - py0))).resize((w, w), Image.BILINEAR)

out = Image.new("RGB", (w * 2 + 12, w + 20), (24, 24, 28))
out.paste(mine.convert("RGB"), (0, 20)); out.paste(refimg.convert("RGB"), (w + 12, 20))
d = ImageDraw.Draw(out)
d.text((4, 4), f"dyn2xyz render  z={Z} -> L={level}", fill=(230, 230, 230))
d.text((w + 16, 4), "independent upstream crop", fill=(230, 230, 230))
out.save(out_path)
print(f"对比图已写入 {out_path}（左：dyn2xyz；右：上游直接裁剪）")

diff = ImageChops.difference(mine.convert("RGB"), refimg.convert("RGB"))
st = ImageStat.Stat(diff)
print(f"最大通道差 = {max(s[1] for s in st.extrema)}   平均差 = {sum(st.mean)/3:.3f}")
r.close()
