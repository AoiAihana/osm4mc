#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交叉校验：iD 的 minecraft 元素预设  <->  osm-carto4mc 的样式表

为什么需要这个脚本
------------------
`openstreetmap-website/config/id_presets_minecraft.yml` 里的 106 个预设，
和 `osm-carto4mc/style/minecraft*.mss` 里的渲染规则，是**两份各自手写**的东西：

    YAML  : 「用户能在 iD 里点选哪些 minecraft:* 键值」
    MSS   : 「osm-carto4mc 到底会画哪些 minecraft:* 键值」

对不上的两种后果都是静默的：

  A. 预设写出来的标签，样式表根本不画
     —— 用户在 iD 里标了一个元素，地图上什么都没有，且没有任何报错。
        最容易踩的是通配值：iD 把 `key: "*"` 的 preset 落成 `key=yes`，
        而样式表可能只认几个具体值、又没写兜底规则。

  B. 样式表能画的值，预设里没有
     —— 值只能手工敲键盘输入，元素预设面板里找不到。

两边哪一侧改了都会破坏一致性，而各自的构建/验证脚本都只看自己那一侧
（`scripts/build_id_minecraft_presets.rb verify` 只校验 YAML 与 sprite 图标、
基础 schema 合并情况；carto 那边只校验 CartoCSS 能编译），所以这里补一条跨项目的检查。

用法
----
    python3 tools/check-id-presets-vs-carto.py          # 人类可读报告
    python3 tools/check-id-presets-vs-carto.py --quiet   # 只输出结论，供脚本调用

退出码：0 = 一致；1 = 发现 A 或 B 类不一致（白名单之外的）。

判定方式
--------
把 .mss 里所有 `[minecraft_xxx = '值']` / `[minecraft_xxx != null]` 抽成
「每个列接受哪些值 + 有没有兜底规则」，再把每个预设**应用后会写入的标签**
（iD 的 `addTags`，缺省等于 `tags`；`"*"` 会落成 `"yes"`）代进去比对。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MSS_GLOB = os.path.join(ROOT, "osm-carto4mc", "style", "minecraft*.mss")
PRESETS = os.path.join(
    ROOT, "openstreetmap-website", "config", "id_minecraft", "presets.min.json"
)

# tag key -> project.mml 里 SELECT 出来的列名。
# 大部分是 `:` 换 `_`，个别是 SQL 里另起的别名（雪屋地下室），所以显式列出。
COLUMNS = {
    "minecraft=yes": None,  # 计划写明不参与渲染
    "minecraft:landuse": "minecraft_landuse",
    "minecraft:industrial": "minecraft_industrial",
    "minecraft:mob_farm": "minecraft_mob_farm",
    "minecraft:redstone": "minecraft_redstone",
    "minecraft:cannon": "minecraft_cannon",
    "minecraft:craft": "minecraft_craft",
    "minecraft:block": "minecraft_block",
    "minecraft:structure": "minecraft_structure",
    "minecraft:structure:igloo:underground": "minecraft_igloo_underground",
    "minecraft:amenity": "minecraft_amenity",
    "minecraft:portal": "minecraft_portal",
    "minecraft:portal:type": "minecraft_portal_type",
    "minecraft:ocean": "minecraft_ocean",
}

# 样式表**故意**不画的值。依据是各 .mss 顶部的注释（计划文档没定义渲染方式）。
# 预设照做是为了「标签可编辑」，不是遗漏，所以这里不算不一致。
INTENTIONALLY_NOT_RENDERED = {
    ("minecraft_landuse", "village"): "计划文档标注「不建议使用」且未定义渲染方式",
    ("minecraft_redstone", "yes"): "计划：「数值为 yes 时不参与 carto4mc 渲染」",
    ("minecraft_redstone", "timer"): "计划未定义渲染方式",
    ("minecraft_redstone", "memory"): "计划未定义渲染方式",
    ("minecraft_redstone", "monitor"): "计划未定义渲染方式",
    ("minecraft_redstone", "tnt_spawner"): "计划未定义渲染方式",
    ("minecraft_cannon", "arrow"): "计划未定义渲染方式",
}

# 样式表为了兼容计划文档里的笔误/大小写而额外接受的值。
# 预设只用规范拼写，所以「没有预设产生它」是正常的。
ALIAS_VALUES = {
    ("minecraft_amenity", "villager_exchage"): "计划文档拼写 villager_exchage，规范值 villager_exchange",
    ("minecraft_block", "monster_spawner"): "刷怪笼旧写法（字段选项里有）",
    ("minecraft_block", "moster_spawner"): "计划文档错拼 moster_spawner（字段选项里有）",
    ("minecraft_mob_farm", "Enderman"): "计划文档首字母大写，规范值 enderman",
    ("minecraft_mob_farm", "guavity"): "计划文档错拼 guavity，规范值 gravity",
    ("minecraft_portal", "Podium"): "计划文档首字母大写，规范值 podium",
    ("minecraft_portal_type", "gatewey"): "计划文档错拼 gatewey，规范值 gateway",
    ("minecraft_structure", "igllo"): "计划文档错拼 igllo，规范值 igloo",
}

SEL = re.compile(r"\[\s*(minecraft_[a-z0-9_]+)\s*(=|!=)\s*(?:'([^']*)'|null)\s*\]")


def parse_stylesheets() -> dict[str, dict]:
    """{'minecraft_landuse': {'values': {...}, 'catchall': bool}}"""
    rules: dict[str, dict] = {}
    files = sorted(glob.glob(MSS_GLOB))
    if not files:
        sys.exit(f"找不到样式表：{MSS_GLOB}")
    for path in files:
        text = re.sub(r"//[^\n]*", "", open(path, encoding="utf-8").read())
        for col, op, val in SEL.findall(text):
            entry = rules.setdefault(col, {"values": set(), "catchall": False})
            if op == "=" and val != "":          # [col = 'x']
                entry["values"].add(val)
            elif op == "!=" and val == "":       # [col != null] —— 兜底规则
                entry["catchall"] = True
    return rules


def effective_tags(preset: dict) -> dict[str, str]:
    """iD 应用这个预设后会写入的标签。

    iD 的 preset.js: `_this.addTags = _this.addTags || _this.tags`，
    而 setTags() 把 `"*"` 落成 `"yes"`（modules/presets/preset.js）。
    """
    tags = preset.get("addTags") or preset.get("tags") or {}
    return {k: ("yes" if v == "*" else v) for k, v in tags.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--quiet", action="store_true", help="只输出结论与不一致项")
    args = ap.parse_args()

    if not os.path.exists(PRESETS):
        sys.exit(
            f"找不到预设产物：{PRESETS}\n"
            "先跑：cd openstreetmap-website && ruby scripts/build_id_minecraft_presets.rb all"
        )

    rules = parse_stylesheets()
    presets = json.load(open(PRESETS, encoding="utf-8"))
    mine = {k: v for k, v in presets.items() if k.startswith("minecraft/")}

    problems: list[str] = []
    notes: list[str] = []
    meta_only: list[str] = []
    rendered = 0

    # ---- A. 预设写出来的标签，样式表画不画？ -------------------------------
    for pid in sorted(mine):
        tags = effective_tags(mine[pid])
        hits = 0
        for key, val in tags.items():
            col = COLUMNS.get(key)
            if not col or col not in rules:
                continue
            hits += 1
            rule = rules[col]
            if val in rule["values"] or rule["catchall"]:
                continue
            if (col, val) in INTENTIONALLY_NOT_RENDERED:
                notes.append(
                    f"{pid}: {key}={val} 不渲染（{INTENTIONALLY_NOT_RENDERED[(col, val)]}）"
                )
                continue
            problems.append(
                f"[A] {pid} 应用后会写 {key}={val}，"
                f"但样式表对 {col} 只认 {sorted(rule['values'])} 且没有兜底规则"
                f"（[col != null]）—— 地图上不会出现任何东西"
            )
        if hits and not any(p.startswith(f"[A] {pid} ") for p in problems):
            rendered += 1
        elif not hits:
            meta_only.append(pid)

    # ---- B. 样式表能画的值，有没有预设能产生？ -----------------------------
    produced = set()
    for preset in mine.values():
        for key, val in effective_tags(preset).items():
            col = COLUMNS.get(key)
            if col:
                produced.add((col, val))

    for col in sorted(rules):
        rule = rules[col]
        for val in sorted(rule["values"]):
            if (col, val) in produced:
                continue
            if (col, val) in ALIAS_VALUES:
                notes.append(
                    f"{col}={val} 没有预设（{ALIAS_VALUES[(col, val)]}）"
                )
                continue
            problems.append(
                f"[B] 样式表能渲染 {col}={val}，但没有任何预设会产生这个值 —— "
                f"用户在元素预设面板里找不到它"
            )
        if rule["catchall"] and not any(c == col for c, _ in produced):
            problems.append(
                f"[B] 样式表对 {col} 有兜底规则（[col != null]），"
                f"但没有任何预设会产生这个列"
            )

    # ---- 报告 --------------------------------------------------------------
    if not args.quiet:
        print(f"样式表规则列：{len(rules)}    预设：{len(mine)}")
        print(f"能命中渲染规则的预设：{rendered}")
        print(f"纯元数据预设（样式表本就不渲染）：{len(meta_only)}")
        print()
        for n in notes:
            print(f"  · {n}")
        print()
        if not problems:
            print("✅ 一致：每个预设写出的标签样式表都认，样式表能画的每个值都有预设")
        else:
            print(f"❌ 发现 {len(problems)} 处不一致：")
            for p in problems:
                print(f"  {p}")
    else:
        for p in problems:
            print(p)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
