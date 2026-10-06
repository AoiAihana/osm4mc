# 自然地貌（natural=*）粗略导入

> **状态：已按你的要求整体撤销（2026-10-02 晚）。**
> changeset 125–127 删除了 106 个自然面，128–135 删除了随之不再被引用的 14,289 个节点；
> 现在库里 `natural=*` 只剩你自己画的 coastline(85) / cliff(5) / water(1)。
> 下面内容保留作为过程记录；脚本可重跑（`undo_natural_final.py` 是本次撤销脚本）。

按 Minecraft 卫星影像（map.bilicraft.com 的 Paralon `flat` 图层，正射俯视）
**大致**画出的自然要素，并且**裁剪到海岸线以内**（不越过 coastline）。

## 结果（当前入库版本）

面本身在 changeset 98 / 101 / 102 / 107（节点在 94 / 95 / 96），渲染库里的实际面积：

| natural=* | 面数 | 面积 |
| --- | --- | --- |
| glacier（雪原/冰盖） | 15 | 87.7 km² |
| wood（森林） | 23 | 60.3 km² |
| scrub（稀树/干草原） | 7 | 35.9 km² |
| bare_rock（裸岩/石山） | 16 | 5.0 km² |
| water=lake（内陆湖） | 22 | 3.1 km² |
| beach（临水沙地） | 12 | 3.0 km² |
| grassland（草甸） | 3 | 1.7 km² |
| sand（内陆沙地） | 8 | 1.4 km² |

海洋没画：按项目约定由 `natural=coastline` + `place=sea` 表示（你已有的那套）。
河流太窄（16 方块一格）没画。

## 做法

1. **取图**：抓上游 Dynmap 的 L3 瓦片（2 方块/像素，9021 张）拼成整张世界图。
2. **特征栅格**（32 方块/格）：均值色、亮度、饱和度、纹理方差，以及
   蓝/雪白/绿/暖黄/中灰/极暗/未渲染灰(79,79,79) 的像素占比。
3. **分类**：水 → 雪（极白或雪占比高）→ 沙 → 绿（方差大或偏暗=森林，否则草地）
   → 灰=裸岩 → 暖色=稀树 → 其余草地；Dynmap 未渲染的固定灰判为无数据。
4. **按海岸线裁剪**（关键一步）：把分类升采样到 16 方块/格，
   用 OSM 的 `natural=coastline`（85 条 way，接成 77 个闭环）栅格化出陆地掩膜，
   落在海里的陆地类别一律改成水 —— 面因此止于海岸线。
5. **成面**：连通块 → 沿格子边走边界 → 只取外环 → Douglas-Peucker 抽稀（16 方块），
   并按面积过滤（只留大块，所以是"大概"而不是逐棵树）。
6. **上传**：闭合 way（首尾同一节点）+ `natural=*`；临水的沙地写 `beach`，
   内陆写 `sand`；内陆湖写 `water=lake`。

## 过程中踩过的坑（都已修好）

* 上一版没按海岸线裁，面会伸进海里 → 已按上面第 4 步重做。
* changeset 超时：本地 API 上传 1 万节点要几分钟，而 changeset 闲置 1 小时会被自动关闭，
  中途被打断过一次，留下 12,840 个**空白节点**（无标签、不属于任何路径）。
  这些已用 cs 77/80/81 删除；现在全库"可见的空白孤立点"为 **0**。
  （另有早期测试遗留的一对叠在同一点的空白点，见 cs 89。）
* 分批发上传时把节点和路径拆到了不同 changeset，导致渲染库一度缺几何
  （osm2pgsql 建不出几何就把 way 丢掉）→ 用 cs 104 重新 modify 补回，
  超大冰川环（2056 点 > 单 way 2000 点上限）改成抽稀到 815 点的单条闭合 way（cs 107）。

## 核对图

* `out/coast_zoom_final.png`：海岸线附近 z15/16 的成品（面止于海岸）。
* `out/world_z11_after.png`：全网总览（本地 carto 渲染）。
* `out/coast_preview.png`：分类结果 + 海岸线（红）叠加，左=影像右=面。
* `out/compare_v3.png`：分类与原图并排。

## 复现

```bash
cd natural-import/scripts
python3 fetch_level.py 3 12                      # 抓 L3 瓦片
python3 build_grid.py --level 3 --cell 32        # 特征栅格
python3 -c "..."                                 # classify + majority_filter
python3 rebuild_with_coast.py                    # 按海岸线裁剪 + 成面
python3 natural_upload.py --polys polygons_coast.json --upload
```
