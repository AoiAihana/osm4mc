# osm4mc —— 版权与开源许可声明

**项目名**：`osm4mc`（暂定）
**适用范围**：本仓库 —— openstreetmap-website + openstreetmap-carto 的组合部署，
外加本项目自行开发的工具、导入器与上游分支
**最后更新**：2026-10-06
**本文件自身的许可**：[CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/)（见 §1）

> ## 无隶属关系声明
>
> - 本项目**与 OpenStreetMap Foundation 无任何隶属关系**，也未获其背书、赞助或授权。
>   「OpenStreetMap」「OSM」「OSMF」是 OpenStreetMap Foundation 的商标，
>   本项目仅在**描述性**意义上使用它们（说明本软件处理的是 OpenStreetMap 数据）。
>   项目名 `osm4mc` 中的 “osm” 同样是描述性的，不表示与 OSMF 存在关联。
> - 本项目**与 Mojang Studios / Microsoft 无任何隶属关系**，也未获其背书、赞助或授权。
>   「Minecraft」是 Mojang Studios 的商标。本项目的 Minecraft 风格**样式表、图标、标签与
>   图层**均为**按该视觉风格自行编写/绘制**，**不包含任何 Minecraft 游戏素材文件**，
>   也不代表 Mojang 对本项目的认可。品牌使用的风险提示见 §8。
> - 本项目**不分发 OpenStreetMap 数据**。`gis` / API 数据库、外部 shapefile、字体等
>   都由使用者自行导入或下载，属本地运行时状态（见 §4、§5）。

这份文件逐项说明本工作区里每个组件的**著作权归属**与**开源许可**，并划清四条边界：

1. 哪些是**本项目自己写的**，完全开放（CC0-1.0）；
2. 哪些是**上游开源项目**，按它们各自的许可（主要是 GPL-2.0 与 CC0-1.0）；
3. 哪些是**第三方数据/素材**，受各自的许可或**保留所有权利**约束（§4、§6）；
4. 哪些是**本地运行时状态**，不属于上述任何一类，也**不应对外分发**。

**怎么读**

| 章节 | 内容 | 与你最相关的结论 |
|---|---|---|
| §1 | 本项目自撰内容 | **CC0-1.0**，可用可改可商用，无需署名（有 3 类例外） |
| §2 | 仓库内带的上游项目 | openstreetmap-website = **GPL-2.0**；openstreetmap-carto = **CC0-1.0** |
| §3 | 容器镜像里的第三方软件 | 各按各的许可，多为 MIT / Apache-2.0 / GPL / LGPL / ISC |
| §4 | **数据** | **OpenStreetMap 数据是 ODbL-1.0，不是 CC0**；另有一处第三方数据来源需注意 |
| §5 | 字体 | OFL-1.1 / Bitstream-Vera / GPL-2.0+，**再分发必须带上许可文本**（当前尚未满足） |
| §6 | 「悠日计划」素材 | **保留所有权利** ★ 待权利人填写，另列了待确认的候选素材 |
| §7 | 对外展示的归属要求 | 地图上必须保留 `© OpenStreetMap contributors` |
| §8 | 商标 | 「OpenStreetMap」是 OSMF 的商标，与数据许可无关 |
| §9 | 免责声明 | 本文件不是法律意见 |

> ⚠️ **一句话提醒**：本仓库的**代码**可以按 CC0 完全开放，但**数据、字体、以及 §6 的素材不行**。
> `gis` 数据库、`backup/*.dump`、`data/` 里的 shapefile 都源自 OpenStreetMap，受 **ODbL-1.0**
> 约束（§4）；`data/fonts/` 里的 104 个字体文件再分发时必须附上各自的许可文本（§5）；
> `openstreetmap-website/vendor/id-tagging-schema/` 是第三方数据集，按 **ISC** 处理（§2.4）。

---

## 1. 本项目自行编写的内容 —— CC0-1.0

### 1.1 范围

下面这些文件是本项目自行编写/修改的（即借助 DeepSeek Harness 等工具创建与编辑的部分），
**不是**任何上游项目的源码，也**不是**数据。它们全部按 **CC0-1.0** 开放：

**编排、配置与文档（仓库根）**

```
docker-compose.yml              组合编排：单 PostgreSQL + web / sync / tileserver / dyn2xyz / edge
docker-compose.prod.yml         生产环境的覆盖项
.env                            本地配置（端口、库名、同步间隔，无机密）
.env.prod.example               生产配置模板（不含真实的 SECRET_KEY_BASE）
.gitignore                      忽略规则（机密、数据、生成物；发布前由下面那个脚本校验）
.gitattributes                  换行与 GitHub 语言统计设置
LICENSE                         CC0-1.0 全文（只覆盖本项目自撰内容）
README.md                       GitHub 首页（含无隶属关系声明）
README-COMBINED.md              组合部署的完整文档
PUBLISHING.md                   发布到 GitHub 的操作手册
COPYRIGHT.md                    本文件
给人写的readme.txt               运维备忘
carto之后的计划.txt              后续开发计划
h.html                          一次性检查页面
```

**服务镜像与入口**

```
sync/Dockerfile                 同步服务镜像
sync/entrypoint.sh
sync/osm_sync.py                API 库 -> osm2pgsql -> gis 库 的增量同步
sync/reliable_run.py            给上游下载脚本加超时/重试的包装
tileserver/Dockerfile           渲染服务镜像
tileserver/entrypoint.sh
tileserver/renderd.conf         renderd 配置
tileserver/apache-tiles.conf    mod_tile 的 Apache 虚拟主机
tileserver/ports.conf           Apache 监听 8080
web/start.sh                    网站容器入口：iD 补丁校验 / 资源重建 / 守卫启动 / Puma
edge/                           nginx 反向代理（唯一对外入口，按路径分发 web / tileserver / dyn2xyz）
dyn2xyz/                        Dynmap 瓦片 -> 标准 XYZ 瓦片 的无状态代理（app / config / tests / tools）
```

**工具（`tools/`）**

```
switch-env.sh                   测试/生产环境切换
e2e-test.sh                     端到端验证（API 写入 -> 同步 -> 出图）
backup-db.sh                    gis 库定时备份
copy-env-data.sh                在测试/生产之间复制数据库（pg_dump / pg_restore）
check-id-basemaps.sh            iD 底图清单核对
check-id-preset-wiring.sh       iD 预设接线核对
check-id-presets-vs-carto.py    预设与 carto 样式的一致性核对
check-relation-member-loss.sh   关系成员丢失检测
guard-relation-members.py       关系成员守卫（宿主机版）
check-before-publish.sh         发布前检查（机密 / 大文件 / 权利未确认素材 / 许可文本）
prod-init.rb                    ★ 见 §1.3(2)
reset-password.rb               ★ 见 §1.3(2)
backup/backup.conf              备份配置
```

**导入器与素材工具**

```
natural-import/                 自然地貌（natural=*）导入（scripts/ 入库；out/ 不入库）
railway-import/                 帕拉伦国有铁路导入（scripts/ 入库；out/ 不入库）
sfrt-import/                    舒芙蕾地铁导入（scripts/ 入库；out/ 不入库）
```

**以下两个目录按权利人决定「不随本仓库发布」**，因此既不在上面的 CC0 范围内，也不在 §6 的
保留所有权利范围内 —— 它们只存在于本地（`.gitignore` 已整目录排除）：

```
coastline/                      海岸线提取工具链（本地工程目录，内含 OAuth 令牌 coastline/.token）
mcmap/                          Minecraft 在线地图瓦片 vs GIS XYZ 瓦片 的研究产出（含第三方仓库克隆）
```

**加到 openstreetmap-website 目录里的本项目文件**（该目录本身是上游仓库，见 §2）

```
README-ID-PRESETS.md                    iD minecraft 预设的说明
app/controllers/id_imagery_controller.rb
app/controllers/id_presets_controller.rb
config/id_imagery.yml                   本项目自定的 iD 底图清单（替代上游 1300+ 第三方源）
config/id_presets_minecraft.yml         minecraft:* 预设的**唯一手写源文件**
config/id_minecraft/                    由上面的 YAML + 基础 schema 生成（见 §2.4）
lib/id_minecraft_presets.rb             运行时装配预设/底图资源映射
scripts/build_id_minecraft_presets.rb   预设构建脚本
scripts/verify_id_minecraft_presets.sh  预设端到端验证
docker/patch-id-member-loss.py          给 vendored iD 打补丁（见 §2.3）
docker/guard-relation-members.py        关系成员守卫（容器内版）
docker/provision-guard-token.rb         守卫用的长期 API 令牌
docker/test-add-member.mjs              iD 成员算法的离线回归测试
```

### 1.2 CC0 1.0 公有领域贡献声明

在适用法律允许的最大范围内，上述内容的作者与贡献者已将本作品的**全部著作权、相关权利与
邻接权（包括但不限于数据库权利）放弃**，将其贡献（dedicate）至公有领域。

这意味着你可以在全球范围内、无期限、不可撤销地：

- 复制、修改、分发、演绎、汇编本作品，**包括商业用途**；
- **无需署名**、无需取得许可、无需支付任何费用。

本作品按「原样」提供，**不附带任何形式的明示或默示担保**，包括但不限于对适销性、特定用途
适用性及不侵权的担保。作者与贡献者不对因使用本作品而产生的任何索赔、损害或其他责任负责。

- SPDX 标识：`CC0-1.0`
- 完整法律文本：<https://creativecommons.org/publicdomain/zero/1.0/legalcode>

### 1.3 ⚠️ 四类**不属于** CC0 的例外

CC0 只能覆盖「有权处分」的著作权。下面几类要么著作权不属于本项目，要么是 GPL 衍生作品
（**GPL 的传染性不允许把衍生作品改成 CC0**），要么是第三方数据，请勿把它们当成 CC0：

#### (1) 对 openstreetmap-website 上游文件的修改 —— 仍是 GPL-2.0

本仓库改了上游的 **8 个文件**（明细见 §2.3）。这些文件是 GPL-2.0 程序的**衍生作品**，
只能在 GPL-2.0 下分发，**不能用 CC0 覆盖**。本项目不额外主张任何权利，它们随
openstreetmap-website 一起按 GPL-2.0 提供。

> 注意区分：**同一个目录里的另一些文件**是本项目新加的（见 §1.1 最后一段），它们不是上游
> 文件，因此按 CC0 处理；但其中 `config/id_minecraft/` 含有第三方 schema 的内容，见 §2.4。

#### (2) `tools/prod-init.rb` 与 `tools/reset-password.rb` —— 保守起见按 GPL-2.0

这两个 Ruby 脚本通过 `rails runner` 在 openstreetmap-website 的**进程内**运行，并直接引用该
应用的模型（`User`、`Doorkeeper::Application`）、`Settings` 等。它们是否构成 GPL-2.0 程序的
衍生作品在法律上属于边界情形，因此本文件**保守地**把它们并入 openstreetmap-website 一并按
GPL-2.0 处理，而不是 CC0。若你希望放宽，请自行取得法律意见后修改本节。

#### (3) 第三方数据、上游源码与 vendored 数据集

- 上游项目源码（§2）按各自的许可；
- 手工 vendored 的 `id-tagging-schema`（§2.4）按 **ISC**，其内容也会出现在本项目生成的
  `config/id_minecraft/*.json` 里；
- 补丁后的 `node_modules/@openstreetmap/id/dist/iD.js` 仍是 **ISC** 作品（iD），
  补丁脚本本身是 CC0，但**不要把打过补丁的 iD bundle 当成 CC0 对外分发**（§2.3 末）；
- OpenStreetMap 数据与外部 shapefile（§4）按 **ODbL-1.0**；
- 字体（§5）按各自许可，再分发须附许可文本；
- `railway-import/` 与 `sfrt-import/` 的**输入数据来自第三方网站**，见 §4.3。

#### (4) 本地运行时状态与机密 —— 不受 CC0 约束，也不应对外分发

```
coastline/  mcmap/             整目录不发布（本地工程/研究资料；coastline/.token 是 OAuth 令牌）
.env.prod                      含 SECRET_KEY_BASE
.prod-admin-password           管理员口令
data/                          字体、shapefile、瓦片、复制件、渲染数据
backup/*.dump                  gis / API 库的完整副本（内含 ODbL 数据，单文件可达 GB 级）
backup/copy-*/                 tools/copy-env-data.sh 在环境之间复制数据库留下的目录
openstreetmap-website/tmp/     含 guard.token（守卫用的长期 API 令牌，机密！）
.docker/  .npmcache/  .work/  .qa-verifier/   本地缓存与中间产物
*_import/out/                  三个导入器的产物（含第三方来源数据，权利未确认）
dyn2xyz/.venv/                 Python 虚拟环境（内含大量第三方包）
```

### 1.4 关于「用 CC0 授权软件」的提示

Creative Commons 自己**不建议**把 CC0 用于软件，主要原因是 CC0 不包含明示的专利授权，
也不处理担保问题。本项目按维护者要求使用 CC0；如果你需要一份更适合软件的许可，
可考虑 `0BSD`（与 CC0 精神最接近的宽松许可，含专利与担保条款的常规处理）或 `MIT`。
改用许可需要同时修改本节与 §1.1 的范围。

---

## 2. 仓库内的上游项目（完整源码随仓库提供）

### 2.1 openstreetmap-website

| 项目 | 值 |
|---|---|
| 上游 | <https://github.com/openstreetmap/openstreetmap-website> |
| 本仓库所在 commit | `caaef96cd569e0599da60c4678eb9af070c50f45`（2026-05-25） |
| `git describe` | `live-48-gcaaef96cd` |
| 许可 | **GPL-2.0**（GPL 第 2 版；见 `openstreetmap-website/LICENSE`） |
| 著作权 | OpenStreetMap Foundation 及 openstreetmap-website 贡献者 |
| 上游声明 | README：「This software is licensed under the GNU General Public License 2.0」 |
| 本地改动 | **8 个上游文件被修改**，另有若干本项目新增文件（见 §2.3） |

**网站内置的 iD 编辑器与前端的第三方组件见 §3.5。**

### 2.2 openstreetmap-carto（本地目录 `osm-carto4mc`）

| 项目 | 值 |
|---|---|
| 上游 | <https://github.com/openstreetmap-carto/openstreetmap-carto> |
| 本仓库所在 commit | `0a1363914cb0bfcf057d4ac76b62687f02185938`（2026-10-04） |
| 本地分支 / `git describe` | `minecraft` / `v6.1.0-12-g0a136391` |
| 许可 | **CC0-1.0**（`LICENSE.txt`；原文明确「For avoidance of doubt, this includes the cartographic design」，即**制图设计本身**也在 CC0 范围内） |
| 著作权 | Andy Allan 及贡献者；原始制图基于 Steve Chilton 等人 |
| 本地目录 | `osm-carto4mc/`（2026-09-30 由 `openstreetmap-carto/` 改名；remote 仍指向官方仓库） |
| 本地改动 | 1 个文件被修改（`symbols/man_made/tower_defensive.svg`）＋ 3 个未跟踪文件（`symbols/shop/{greengrocer,pet,seafood}.svg.png`，疑为误生成的中间产物，建议清理） |

配色、符号（`symbols/`）与图案（`patterns/`）都由该项目的 CC0-1.0 覆盖，仓库内**没有**
逐文件的例外声明。本项目在该 `minecraft` 分支上追加的符号与图例同样按 CC0 处理。

> 备注：若干部落图案是用 `jsdotpattern`（一个 **AGPL-3.0** 的生成工具，作者 imagico）**生成**
> 的，生成参数记录在 `symbols/generating_patterns/*.md`。工具的许可原则上不约束其输出，
> 且 openstreetmap-carto 项目本身把产物一并声明为 CC0；此处仅作来源备注。

### 2.3 本仓库对上游的改动记录

#### (a) 对 openstreetmap-website 已跟踪文件的修改（8 个，均为 GPL-2.0）

| 文件 | 改动 |
|---|---|
| `config/layers.yml` | 只保留 standard 图层，`tileUrl` 指向本机瓦片服务；移除 CyclOSM / CycleMap / TransportMap / Tracestrack / HOT / Shortbread / OpenMapTiles 等第三方或商业图层；补充说明该文件在 Sprockets 编译期被内联进前端 bundle |
| `config/environments/production.rb` | `assume_ssl` / `force_ssl` 改为可由 `Settings` 覆盖（本地 HTTP 实例需要关掉） |
| `config/initializers/doorkeeper.rb` | `force_ssl_in_redirect_uri` 放宽：development、回环地址（`127.0.0.1`/`::1`）以及 `force_ssl` 为 false 时的本机 `server_url` 均豁免，便于本地 OAuth（iD / JOSM）注册 |
| `config/initializers/content_security_policy.rb` | 从 `config/layers.yml` 自动推导瓦片来源，注入 `img-src` / `connect-src` |
| `config/routes.rb` | 挂载本项目新增的 `/id/imagery` 与 `/id/minecraft/presets` 等路由（供 iD 取自定义底图与预设） |
| `app/assets/javascripts/id.js` | 把 iD 的 asset map 指向本项目的底图/预设控制器，而不是上游的 `dist/data/*.json` |
| `app/views/site/id.html.erb` | 同上：改写 iD 的 assetPath 映射，使编辑器只加载本项目的底图与预设 |
| **`app/controllers/api/maps_controller.rb`** | **修复一处数据损坏级 bug**：`Relation.nodes/ways` 作用域内部是 `joins(:relation_members)`，而原代码又对**同一关联**用 `includes`，导致 Rails 不再单独预加载、**返回的关系只带命中 bbox 的成员**；编辑器因此拿到残缺成员表，一经保存就把其余成员整表删除。改为 `preload`（见下方说明） |

> **关于 `maps_controller.rb` 的修复**：这不是「顺手改的样式」，而是本实例曾经反复丢数据的根因。
> 实测：库中 `relation 117` 有 32 个成员，而 `map?bbox=` 只返回了落在该 bbox 内的 4 个。
> 编辑器（iD）看到的关系是「完整的」，因此既不会提示「有成员未下载」，也不会阻止保存 ——
> 保存即永久删除其余成员。G104 道路关系一次掉了 20 条 `ref=G104` 的 way（约 9.6 km），
> 海岸线关系也丢过多次。**若你要把这类改动向上游提交，这一条最有价值。**

#### (b) 加到 openstreetmap-website 目录里的本项目新增文件

见 §1.1 最后一段。它们按 **CC0-1.0** 处理，但有两个附带说明：

1. `config/id_minecraft/*.json` 是**生成产物**，内容包含第三方 schema（ISC）的片段 → 见 §2.4；
2. 补丁后的 iD bundle 是 **ISC** 作品 → 见下方。

#### (c) 对 vendored iD 的运行时补丁

`web/start.sh` 在每次启动时都会调用 `docker/patch-id-member-loss.py`，对
`node_modules/@openstreetmap/id/dist/iD.js` 打两处补丁（幂等、只增不减）：

| 补丁 | 位置（bundle 行号） | 治什么 |
|---|---|---|
| **A** | 80640 | `merge_polygon` 用 `osmJoinWays` 重建成员表时会丢掉**非 way 成员**与**未加载进 graph 的成员**；改为只补不删 |
| **B** | 31095 | `split` 的 `isArea` 分支会把 way 成员**换成 relation 成员**；改为两半都留成 way 成员 |

**许可影响**：补丁脚本本身是本项目作品（CC0）；iD 是 **ISC** 作品，**打过补丁的 `iD.js`
仍然是 ISC 作品**，其著作权属于 iD Contributors。`web/start.sh` 还会把所有历史
`public/assets/id-*.js` 覆盖成带补丁的内容，这些产物同样按 ISC 处理。

### 2.4 手工 vendored 的第三方数据集：`id-tagging-schema`

| 项目 | 值 |
|---|---|
| 位置 | `openstreetmap-website/vendor/id-tagging-schema/` |
| 上游 | <https://github.com/openstreetmap/id-tagging-schema> |
| 版本 | **6.19.2**（记录在 `base.json`：`{"version":"6.19.2","range":"^6.13.4","requested_at":"2026-10-01T15:40:18Z"}`） |
| 取用方式 | `https://cdn.jsdelivr.net/npm/@openstreetmap/id-tagging-schema@<版本>/`，由 `scripts/build_id_minecraft_presets.rb` 下载 |
| 许可 | **ISC**（[包元数据](https://cdn.jsdelivr.net/npm/@openstreetmap/id-tagging-schema@6.19.2/package.json) 的 `"license": "ISC"`） |
| 著作权 | id-tagging-schema contributors |
| 再分发义务 | **ISC 要求保留版权声明与许可文本** —— 目前 `vendor/id-tagging-schema/` 里**没有** LICENSE 文件，见下方建议 |

**受影响的产物**：`config/id_minecraft/{presets,fields,preset_categories,preset_defaults}.min.json`
与 `config/id_minecraft/translations/*`、`icon-names.txt`（3297 个图标名）都是**基于该 schema
再加工**的结果。按 `manifest.json` 记录，其中 minecraft 部分为预设 106 个（总 1837）、
字段 21 个（总 765）、分类 5 个（总 22）。

**建议**：把上游的 ISC 许可文本放到 `vendor/id-tagging-schema/LICENSE.txt`（或在
`README-ID-PRESETS.md` 里写明来源与许可），以满足 ISC 的再分发要求。

> 备注：`@openstreetmap/id` 2.40 与 `id-tagging-schema` 6.19.2 之间存在**上游既有的版本错位**
> （见 `README-ID-PRESETS.md`），与本项目的许可声明无关。

---

## 3. 运行时第三方组件（在容器镜像里，不随仓库源码提供）

本节列出的组件**不在本仓库里**，而是由各 `Dockerfile` 在构建镜像时从 Ubuntu / Debian /
npm / RubyGems / PyPI 安装。它们各自受自己的许可约束。
**版本号为 2026-10-06 从运行中的镜像实测所得。**

### 3.1 基础镜像

| 镜像 | 用于 | 说明 |
|---|---|---|
| `ubuntu:24.04`（noble） | `tileserver`、`sync` | **没有**单一许可：是成千上万个独立发行版软件包的集合 |
| `postgres:14` | `db` | `debian:bookworm-slim` + PGDG 的 PostgreSQL 14；同样是软件包集合 |
| `ruby:3.3-bookworm` | `web` | `debian:bookworm` 上的 `buildpack-deps` + 从源码编译的 Ruby |
| `nginx:alpine` | `edge` | nginx（`BSD-2-Clause`）+ Alpine 基础包集合 |
| `python:3.12-slim` | `dyn2xyz` | Python 3.12 + Debian slim 包集合；镜像里只有标准库 + Pillow，无编译工具链 |

> 发行版镜像整体**没有** SPDX 标识。正确表述是：「本镜像基于 Ubuntu 24.04 LTS (noble) /
> Debian 12 (bookworm) / Alpine 构建，内含众多各自独立授权的组件；每个组件受其自身许可约束，
> 镜像整体没有单一许可。」

### 3.2 渲染 / 瓦片服务（`tileserver` 镜像）

| 组件 | 版本 | SPDX | 著作权 |
|---|---|---|---|
| Apache HTTP Server（`apache2`） | 2.4.58 | `Apache-2.0` | The Apache Software Foundation |
| renderd / libapache2-mod-tile | 0.6.1-2build4 | `GPL-2.0-or-later` | 2007–2021 mod_tile 贡献者（Jon Burgess、Kai Krueger、Frederik Ramm 等） |
| Mapnik（`libmapnik3.1t64`，随 mod_tile 装入） | 3.1.0+ds-7ubuntu2 | `LGPL-2.1-or-later` | Artem Pavlenko、Dane Springmeyer、Mapnik 贡献者；内含 AGG / BSD-3-Clause / BSL-1.0 等 |
| carto（npm，CartoCSS 编译器） | 1.2.0（已固定） | `Apache-2.0` | Mapbox |
| Node.js | 18.19.1 | `MIT` | Node.js 贡献者；发行版包内另有 OpenSSL / ICU / V8 / zlib 等许可 |
| npm | 9.2.0 | `Artistic-2.0` | npm, Inc. / GitHub Inc. |
| postgresql-client | 14 | `GPL-2.0-or-later`（**打包**部分） | PostgreSQL 软件本体是 `PostgreSQL` 许可；Debian 打包脚本为 GPL-2+ |
| curl | 8.5.0 | `curl` | Daniel Stenberg 及贡献者 |
| unzip | 6.0 | `Info-ZIP` | Info-ZIP（Mark Adler、Jean-loup Gailly 等） |
| ca-certificates | — | `MPL-2.0`（Mozilla CA 数据）+ `GPL-2.0-or-later`（打包脚本） | Mozilla Contributors / Debian 贡献者 |
| 字体包 | `fonts-noto-core` 20201225-2、`fonts-dejavu` 2.37-8、`fonts-unifont` 1:15.1.01-1build1 等 | 见 §5 | — |

### 3.3 数据同步（`sync` 镜像）

| 组件 | 版本 | SPDX | 著作权 |
|---|---|---|---|
| osm2pgsql | 1.11.0 | `GPL-2.0-or-later` | 2006–2023 osm2pgsql 贡献者（Jon Burgess、Sarah Hoffmann、Jochen Topf 等） |
| Python 3 | 3.12.3 | `Python-2.0`（PSF） | Guido van Rossum 及 Python 贡献者 |
| psycopg2（`python3-psycopg2`） | 2.9.9 | `LGPL-3.0-or-later`（附 OpenSSL 链接例外） | Federico Di Gregorio、Daniele Varrazzo 等 |
| PyYAML（`python3-yaml`） | 6.0.1 | `MIT` | Ingy döt Net、Kirill Simonov |
| requests（`python3-requests`） | 2.31.0 | `Apache-2.0` | Kenneth Reitz 及贡献者 |
| GDAL（`gdal-bin`） | 3.8.4 | `MIT`（Debian 记作 Expat）+ 大量逐文件例外 | Even Rouault、Frank Warmerdam、OSGeo 贡献者 |
| curl / unzip / ca-certificates | — | 同 §3.2 | — |

### 3.4 数据库（`db` 镜像）

| 组件 | 版本 | SPDX | 著作权 |
|---|---|---|---|
| PostgreSQL | 14.23 | `PostgreSQL` | PostgreSQL Global Development Group；部分 © 1994 The Regents of the University of California |
| PostGIS（`postgresql-14-postgis-3`） | 3.6 | `GPL-2.0-or-later` | PostGIS Developers（Refractions Research、Paul Ramsey、Sandro Santilli、Regina Obe、Oslandia 等）；内含 Apache-2.0 / BSD-3-Clause / ISC / LGPL-2+ / public-domain 等 |
| PostGIS 扩展 `postgis`、`hstore` | 3.6 / 1.8 | 同上 / `PostgreSQL` | — |

### 3.5 网站与 API（`web` 镜像）

#### 3.5.1 系统包

| 组件 | 版本 | SPDX | 著作权 |
|---|---|---|---|
| Ruby | 3.3.11 | `Ruby OR BSD-2-Clause`（双许可） | まつもとゆきひろ（Matz）及 Ruby 贡献者 |
| RubyGems | — | `Ruby OR MIT`（双许可） | Chad Fowler、Rich Kilmer、Jim Weirich 等 |
| Bundler | — | `MIT` | André Arko、Samuel Giddins、David Rodríguez 等 |
| Yarn | 1.22.22 | `BSD-2-Clause` | Yarn Contributors |
| Node.js | 18.20.4 | `MIT` | Node.js 贡献者 |
| OpenJDK 17（`default-jre-headless`） | 17 | `GPL-2.0-only WITH Classpath-exception-2.0` | Oracle 及 OpenJDK 贡献者 |
| Osmosis | — | **`public-domain`**（公有领域；`GPL-3.0+` 仅出现在部分文件/打包） | © 2007-2008 Brett Henderson 及贡献者 |
| Firefox ESR | — | `MPL-2.0`（部分文件另有 MPL-1.1 / GPL-2.0 / LGPL-2.1 / BSD 等） | Mozilla Foundation 及贡献者 |
| Xvfb（`xorg-server`） | — | `MIT`（X.Org 版本） | X.Org Foundation 及贡献者 |
| mesa-utils / libgl1-mesa-dri | — | `MIT`（另有 SGI-B-2.0、HPND 等逐文件例外） | Brian Paul、Mesa 贡献者 |

#### 3.5.2 前端 JavaScript（直接依赖；版本与许可读自容器内各包 `package.json`）

| 包 | 版本 | SPDX | 著作权 |
|---|---|---|---|
| `@openstreetmap/id`（**iD 编辑器**） | 2.40.0 | `ISC` | iD Contributors |
| `leaflet` | 1.9.4 | `BSD-2-Clause` | Volodymyr Agafonkin；CloudMade |
| `maplibre-gl` | 5.24.0 | `BSD-3-Clause` | MapLibre contributors |
| `@maplibre/maplibre-gl-leaflet` | 0.1.3 | `ISC` | MapLibre contributors；Mapbox |
| `@maptiler/maplibre-gl-omt-language` | 0.0.3 | `ISC`（仅元数据声明，包内未附许可文件） | Martin Ždila |
| `@mapbox/mapbox-gl-rtl-text` | 0.4.0 | `BSD-2-Clause` | Mapbox |
| `@mapbox/polyline` | 1.2.1 | **包内未声明**（上游仓库为 `BSD-3-Clause`） | Development Seed |
| `bootstrap-icons` | 1.13.1 | `MIT` | The Bootstrap Authors |
| `js-cookie` | 3.0.7 | `MIT` | Klaus Hartl、Fagner Brack |
| `i18n-js` | 4.5.3 | `MIT` | Nando Vieira |
| `make-plural` | 8.1.0 | `Unicode-DFS-2016` | Eemeli Aro；Unicode, Inc. |
| `osm-community-index` | 6.0.0 | `ISC` | osm-community-index contributors |
| `tag2link` | 2026.5.6 | `ISC` | Simon Legner |
| `leaflet.locatecontrol` | 0.90.0 | `MIT` | Dominik Moritz |

另有 9 个 `devDependencies`（eslint 及插件、`@herb-tools/linter`、类型定义等），仅构建期使用，
多为 `MIT`。

手工内置在 `openstreetmap-website/vendor/assets/` 的文件（来源由上游 `Vendorfile` 固定）：

| 文件 | 版本 | SPDX | 著作权 |
|---|---|---|---|
| `jquery/jquery.throttle-debounce.js` | 1.1 | `MIT OR GPL-2.0-only`（双许可） | © 2010 "Cowboy" Ben Alman |
| `leaflet/leaflet.locationfilter.js` / `.css` | 0.1 | `MIT` | © 2012 Tripbirds.com；Robert Kajic |
| `leaflet/leaflet.osm.js` | 0.1.0 | `BSD-2-Clause` | © 2012 John Firebaugh |

> **另有 §2.4 的 `@openstreetmap/id-tagging-schema` 6.19.2（ISC）**，它是手工 vendored 的，
> 不在 `node_modules` 里。

#### 3.5.3 Ruby 依赖

`openstreetmap-website/Gemfile.lock` 里有 **118 个直接依赖**，按许可分组：

| SPDX | 数量 | 需要注意的 |
|---|---|---|
| `MIT` | 93 | Rails 8.1.3、Doorkeeper 5.9.1、minitest、capybara、rubocop、danger、simplecov 等 |
| `Ruby OR BSD-2-Clause` | 8 | 标准库 gem：benchmark、cgi、debug、digest、open3、timeout、uri、webrick |
| `Apache-2.0` | 7 | addressable、aws-sdk-s3、ffi-libarchive、opentelemetry-*、selenium-webdriver |
| `BSD-2-Clause` | 3 | annotaterb、**pg 1.6.3**、rexml |
| `BSD-3-Clause` | 1 | puma 8.0.1 |
| `ISC` | 1 | rinku 2.0.6 |
| `MIT OR Apache-2.0` | 1 | marcel 1.2.1 |
| **`GPL-2.0-only`** | 1 | **quad_tile 1.0.1**（唯一一个 GPL 依赖） |
| 非 SPDX 字符串 | 2 | active_record_union 1.4.0（"Public Domain"）、brakeman 8.0.4（"Brakeman Public Use License"，非 OSI 认证） |
| 元数据缺失 | 1 | openstreetmap-deadlock_retry 1.3.1（gemspec 未填 license；上游 LICENSE 为 MIT） |

> 传递依赖（非直接依赖）未逐一列出，完整清单见 `Gemfile.lock`。
> 另注：上游**不使用 rspec**，测试框架是 minitest（MIT）。

### 3.6 本项目自带服务的外部依赖

这几个服务是本项目自己写的（§1.1），但它们运行时依赖第三方库：

| 服务 | 依赖 | SPDX / 许可 |
|---|---|---|
| `dyn2xyz` | Pillow（`requirements.txt`：`Pillow>=10.0,<13.0`） | `MIT-CMU`（HPND 变体） |
| `dyn2xyz` 运行环境 | `dyn2xyz/.venv/` 里的 pip、requests、urllib3、certifi、idna、platformdirs 等 | 多为 `MIT` / `BSD-2-Clause` / `Apache-2.0` / `MPL-2.0`；**属本地虚拟环境，不随仓库分发** |
| `coastline` | numpy、scipy、shapely、scikit-image（见 `coastline/.venv/`） | `BSD-3-Clause`（numpy / scipy / scikit-image）、`BSD-3-Clause`（shapely，GEOS 为 `LGPL-2.1-or-later`） |
| `coastline` 运行环境 | `coastline/.venv/` | 同上，本地环境 |
| `mcmap` | 见 `mcmap/tools/`（研究脚本） | 本项目代码为 CC0；其 `sources/` 是第三方软件的**文件清单**（非代码） |
| 导入器（`natural-import` / `railway-import` / `sfrt-import`） | 见各自 `scripts/` 的 import | 本项目代码为 CC0；**输入数据是第三方内容**，见 §4.3 |

### 3.7 如何自行核对镜像内的许可

Debian / Ubuntu 要求**每个软件包**自带版权与许可文本，因此镜像内就能查到权威答案：

```bash
export DOCKER_CONFIG=/home/aoiaihana/osm/.docker

# 列出某个镜像里装了什么
docker run --rm --entrypoint sh osm-tileserver:latest -c 'dpkg -l | less'

# 看某个包的许可（DEP-5 机器可读格式）
docker run --rm --entrypoint sh osm-tileserver:latest -c \
    'grep -E "^(Files|Copyright|License):" /usr/share/doc/renderd/copyright'

# 常见许可全文
docker run --rm --entrypoint sh osm-tileserver:latest -c 'ls /usr/share/common-licenses/'
```

npm、RubyGems 与 PyPI 的依赖：

```bash
docker compose -p osm-prod -f docker-compose.yml -f docker-compose.prod.yml exec -T web \
    sh -c 'grep -m1 "\"license\"" /app/node_modules/<包名>/package.json'
npm view <package> license                      # 权威来源就是 registry
curl -s https://rubygems.org/api/v1/gems/<gem>.json | grep -i license
curl -s https://pypi.org/pypi/<包名>/json | python3 -c 'import json,sys;print(json.load(sys.stdin)["info"]["license"])'
```

---

## 4. 数据 —— 这一节最容易被忽略

> **本仓库不分发任何数据。** 下面这些数据都只存在于本地的数据库与 `data/` 目录里，
> **不在 git 仓库中**（见 §1.3(4) 与 `.gitignore`）。目前的数据库内容**暂不发布**；
> 日后如需要，会**单独建一个仓库**（或走 GitHub Releases）并单独声明其 ODbL 义务。
> 本节的意义是：**谁**拿到这些本地数据，**谁**就要承担下面的义务。

### 4.1 OpenStreetMap 数据：**ODbL-1.0**（不是 CC0）

本实例的编辑数据（API 库 `openstreetmap`）与渲染数据（`gis` 库）都源自 OpenStreetMap。

| 项 | 值 |
|---|---|
| 许可 | **ODbL-1.0**（Open Database License 1.0） |
| 著作权 | © OpenStreetMap contributors |
| 许可全文 | <https://opendatacommons.org/licenses/odbl/1-0/> |
| 版权页 | <https://www.openstreetmap.org/copyright> |

**ODbL 的「传染性」针对的是数据库，不是图片**：

- `gis` 库本身是 OSM 数据的**衍生数据库**（Derivative Database），受 ODbL 约束；
- 因此 **`backup/*.dump` 也含 ODbL 数据**——它就是你 `gis` 库的完整副本；
- 如果你把这些数据库（或 dump）对外提供，必须按 ODbL 提供，并保留归属声明；
- 只在**内部**使用、不对外分发时，不产生对外提供义务。

### 4.2 外部 shapefile：也是 **ODbL-1.0**

bootstrap 时从 <https://osmdata.openstreetmap.de/>（由 FOSSGIS e.V. 运营）下载的四份数据
（`water_polygons`、`simplified_water_polygons`、`icesheet_polygons`、`icesheet_outlines`，
共约 1.4 GB，是 `gis` 库 99% 的体积）：

> 该站 License 页原文：*"Data for download on this site is derived from OpenStreetMap data and
> Copyright OpenStreetMap contributors. As required by this license all files containing data
> directly based on the original OpenStreetMap data are available under the same license as the
> original OpenStreetMap data, the Open Database License (ODbL)."*

→ 结论：**ODbL-1.0**，© OpenStreetMap contributors。
来源：<https://osmdata.openstreetmap.de/info/license.html>

### 4.3 ⚠️ 第三方数据来源：`railwaymap.big-brother.top`

`railway-import/`（帕拉伦国有铁路）与 `sfrt-import/`（舒芙蕾地铁）是**按第三方网站
`railwaymap.big-brother.top` 的数据**在自己的实例里绘制的（见各自 `README.md`）。

**许可上的两点提醒**：

1. **输入数据的权利不属于本项目**，也不属于 OpenStreetMap。把它导入 OSM 数据库后，
   结果数据按 §4.1 以 ODbL 提供 —— 但这**不能**追溯性地使原始第三方数据的许可变得合规。
   如果你是这些数据的权利人，或已获得许可，请**在 §6.1 里写明授权方式**；
   如果不是，建议不要对外分发 `railway-import/out/`、`sfrt-import/out/` 与相关 OSM 数据导出。
2. 本项目目录下的**脚本**（`scripts/*.py`）是本项目作品，按 CC0 处理；`out/` 里的
   **产物与核对图**可能含有第三方数据的内容，按「待确认」处理（见 §6.1）。

### 4.4 渲染出来的瓦片（PNG）：可以是任何许可

ODbL 把「用数据库内容生成的图像、音视频、文本」定义为 **Produced Work**。
ODbL §4.5(b) 明确规定：用数据库内容制作 Produced Work **不构成** Derivative Database，
因此 §4.4 的「相同方式共享」**不适用**——**你渲染出的 PNG 瓦片可以按你喜欢的任何许可发布**。

但仍有两点义务：

1. **必须显示归属**（ODbL §4.3）：要让接触该 Produced Work 的人知道内容来自 OSM 数据库、
   且该数据库按 ODbL 提供。本实例的前端已自动显示（见 §7）。
2. **底层数据库仍需按 ODbL 提供**：依据 OSMF 董事会 2014-06-06 认可的
   [Produced Work 指引](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Produced_Work_-_Guideline)，
   「if you publish a produced work, the underlying database has to be published as well」。
   本实例是私有部署、未对外发布瓦片时，不触发这一条。

### 4.5 Natural Earth：公有领域

openstreetmap-carto 的部分图层使用 <https://www.naturalearthdata.com/> 的数据：

- 许可：**公有领域**，不强制署名（可选的礼貌署名：*Made with Natural Earth.*）；
- 作者：Tom Patterson、Nathaniel Vaughn Kelso；
- 条款：<https://www.naturalearthdata.com/about/terms-of-use/>
- 该页同时说明，Natural Earth 自身包含若干第三方出版的版本（Washington Post、EC JRC IES、
  XNR Productions、International Mapping Associates），这些方**不主张**知识产权。

---

## 5. 字体

字体文件在本工作区有**两份来源**：

1. **Ubuntu 发行版包**（`tileserver/Dockerfile` 里安装）：`fonts-noto-core`（20201225-2）、
   `fonts-noto-extra`、`fonts-noto-cjk`、`fonts-noto-color-emoji`、`fonts-noto-unhinted`、
   `fonts-dejavu-core` / `fonts-dejavu`（2.37-8）、`fonts-unifont`（1:15.1.01-1build1）；
2. **bootstrap 时直接下载**到 `data/fonts/`（**104 个文件**，约 98 MB），由 openstreetmap-carto 的
   `scripts/get-fonts.py` 完成。

| 字体 | 来源 | SPDX / 许可 | 著作权 |
|---|---|---|---|
| Noto（core / extra / unhinted，`.ttf`） | `fonts-noto-*` 包 + `github.com/notofonts/noto-fonts` | **`OFL-1.1`** | Copyright 2018 The Noto Project Authors |
| Noto CJK（`NotoSansCJKjp-*.otf`） | `fonts-noto-cjk` + `github.com/notofonts/noto-cjk` | **`OFL-1.1`**（Debian 记作 `SIL-1.1`，即同一份 SIL Open Font License 1.1） | Copyright 2010-2012 Google Corporation |
| Noto Color Emoji（**字体文件**） | `fonts-noto-color-emoji` | **`OFL-1.1`**（该包 `Files: *` 是 Apache-2.0，但 `Files: fonts/*` 是 `SIL-1.1`） | Copyright 2013-2017 Google Inc. |
| Noto Emoji 黑白（`NotoEmoji-*.ttf`） | `archive.org/download/noto-emoji` | **`OFL-1.1`** | Copyright 2013 Google LLC |
| DejaVu（`fonts-dejavu-*`） | 发行版包 | **Bitstream Vera**（DejaVu 的改动置于公有领域；无 SPDX 标识） | Copyright (c) 2003 by Bitstream, Inc. |
| GNU Unifont（`fonts-unifont`） | 发行版包 | **`GPL-2.0-or-later` + GNU 字体嵌入例外** | Jungshik Shin、Roman Czyborra、Qianqian Fang、Paul Hardy 等（集体） |
| **Hanazono / 花園フォント（`HanaMinA.ttf`、`HanaMinB.ttf`）** | `mirrors.dotsrc.org/.../hanazono-20170904.zip` | **双许可**：Hanazono Font License **或** `OFL-1.1`（建议按 OFL-1.1 处理） | Copyright (c) 2011, GlyphWiki Project (kamichi@fonts.jp)，保留字体名 "Hanazono Font"、"HanaMinA"、"HanaMinB"、"花園フォント"、"花園明朝A"、"花園明朝B" |

> ⚠️ **`HanaMinA/B.ttf` 不是 Noto 字体**，是 GlyphWiki 项目的花園フォント（Hanazono），
> 许可与 Noto 不同，容易在清点时漏掉。

### 5.1 再分发时的义务（**当前尚未满足**）

`data/fonts/` 目录里**只有字体二进制，没有任何许可文本**（2026-10-06 复核：104 个文件，
无 `LICENSE*` / `*.txt`）。而上述每一种字体的许可都要求在再分发时**附带版权声明与许可全文**：

- OFL-1.1 §2（Noto 全系、Hanazono 的 OFL 分支）：每份副本须包含版权声明与许可文本，
  可以放在独立文件、人类可读的头部，或机器可读的元数据字段里；§3 规定**保留字体名**
  （改名后才能发布修改版）；§5 要求衍生字体继续使用 OFL-1.1；
- Bitstream Vera（DejaVu）：版权与商标声明、许可声明必须包含在字体软件的所有副本中；
  修改版必须改名去掉 "Bitstream"/"Vera"；不得单独出售字形；
- GPL-2.0+（GNU Unifont）：再分发字体二进制须随附 GPLv2 全文与版权声明。注意
  **字体嵌入例外只放宽「在文档中嵌入字体」的场景，并不免除再分发字体文件本身的义务**。

**建议**：在 `data/fonts/` 下放一个 `THIRD-PARTY-LICENSES.txt`，逐字体写明版权行并附许可全文
（或指向 `/usr/share/common-licenses/` 与上游 URL）。若对外分发整个工作区，这一步是必需的。

---

## 6. 「悠日计划」使用的素材 —— 保留所有权利

> ★★★ **本节待手动填写** ★★★
>
> 本节用于登记「悠日计划」中使用的、**著作权不属于本仓库 CC0 范围**的素材。这些素材由权利人
> **保留全部权利（All Rights Reserved）**：未经权利人书面许可，不得复制、修改、再分发、
> 公开传播或用于商业用途。
>
> 维护者请补齐 §6.1 表格，并在填完后**删除本提示框**。在删除前请至少保留一个
> 「待填写」标记，以免外人误以为此处已经完整声明。

### 6.1 需要权利人确认的候选素材

下面这些内容**权利归属不明或可能不属于 CC0**，在权利人明确之前**不要**把它们当作 CC0 使用。
请逐项确认后填入 §6.2 的表格（或直接确认「属于本项目 CC0 范围」并移出本节）：

| 候选 | 位置 | 目前已知 |
|---|---|---|
| 导入数据与核对图 | `railway-import/out/`、`sfrt-import/out/` | 数据来源为 `railwaymap.big-brother.top`（见 §4.3）；权利人**待确认**（`out/` 已在 `.gitignore` 中） |
| 其它素材 | `data/` 里由「悠日计划」提供的任何图块/纹理 | **待确认**（`data/` 不入库） |

> **已移出本节**（权利人已决定删除或不发布，因此不再是待确认项）：
>
> - `art/`（Minecraft 主题 SVG 图标）——**已删除**；
> - `coastline/`（含 `ref/THEMAP_0618.jpg`）与 `mcmap/`（含 `sources/`）——**整目录不随仓库发布**，见 §1.1。

### 6.2 素材清单（待填写）

| # | 文件 / 素材 | 用途 | 著作权人 | 来源 | 授权方式 | 授权范围 / 有效期 | 联系方式 |
|---|---|---|---|---|---|---|---|
| 1 | `待填写` | 悠日计划 | `待填写` | `待填写` | 保留所有权利 | `待填写` | `待填写` |
| 2 | | | | | | | |
| 3 | | | | | | | |
| 4 | | | | | | | |
| 5 | | | | | | | |

<!-- TODO(手动编辑)：在 §6.2 表格中补齐每一项。
     如有对应的授权书 / 合同 / 邮件，请一并归档，并在本表「来源」或下方写明文件路径。 -->

### 6.3 与开源部分的边界

- 本节素材与 §1 的 CC0 内容、§2 的上游项目在**文件层面互不重叠**。
  如实际存在重叠（例如某个开源文件被「悠日计划」修改后使用），**必须在此明确写出该文件路径**，
  否则会同时满足两套互相冲突的许可条件。
- 若某项素材被**嵌入**到衍生作品（例如带水印的瓦片、导出的图片、文档）中，则该衍生作品的
  再分发必须同时满足本节的限制。
- 「悠日计划」的**名称、标识（logo）**如已注册为商标，请在此一并注明注册号与地域。

### 6.4 使用限制摘要（待确认）

- [ ] 是否允许非商业用途的再分发？`待填写`
- [ ] 是否允许修改？`待填写`
- [ ] 是否要求署名？署名格式：`待填写`
- [ ] 是否允许用于训练机器学习模型？`待填写`

---

## 7. 对外展示时的归属要求

只要你的实例对外提供地图或数据，就必须保留归属。本实例的归属展示已经可用，机理如下：

| 位置 | 实现 | 说明 |
|---|---|---|
| 地图右下角（Leaflet attribution 控件） | `app/assets/javascripts/leaflet.map.js:16`：`if (credit) layerOptions.attribution = makeAttribution(credit)` | **只有图层在 `layers.yml` 里带 `credit` 字段时才会设置归属** |
| 归属文案的组成 | 同文件 `makeAttribution()`（第 59–79 行） | 依次拼接：`© OpenStreetMap contributors`（链到本站 `/copyright`）→ `credit.donate ? " ♥️ " : ". "` → `credit` 本身的链接 → OSMF 使用条款链接 |
| 文案来源 | `config/locales/en.yml`：`copyright_text: "© %{copyright_link}"` + `openstreetmap_contributors: "OpenStreetMap contributors"` | 各语言有各自的翻译 |
| API 响应 | `api/_root_attributes.json.jbuilder` 输出 `Settings.attribution_url` | 默认 `http://www.openstreetmap.org/copyright` |
| 版权页 | `/copyright`（`app/views/site/copyright.html.erb`） | 上游自带的完整 ODbL 说明页，归属链接就指向它 |

本实例的地图上实际显示的是（`layers.yml` 里 standard 图层带 `credit`）：

```
© OpenStreetMap contributors ♥️ Make a Donation. Website and API terms
```

**必须保留**：`© OpenStreetMap contributors`（或 OSMF 归属指引认可的等价形式）。

> ⚠️ **一个容易踩的坑**：归属是**跟着 `credit` 字段走的**（`leaflet.map.js:16` 的
> `if (credit)`）。如果你为了去掉那个不准确的「Make a Donation」而**直接把 `credit` 整段删掉**，
> 地图上会**连归属一起消失**，从而违反 ODbL §4.3。正确做法是**改写** `credit`，
> 而不是删除它。

**建议修改**（本实例目前仍是上游默认值，可选）：

- `config/layers.yml` 里 standard 图层的 `credit` 仍是 `make_a_donation`
  （`id: "make_a_donation"` + `donate: true`，链接到 `supporting.openstreetmap.org` 的捐款页，
  并因此显示 ♥️）。这对**本实例**并不准确，可改成指向本站说明页，例如把
  `id` 换成自己的文案键、去掉 `donate: true`；**但必须保留 `credit` 字段本身**。
- `Settings.attribution_url` 默认指向 `openstreetmap.org`；自建实例更适合指向本站的 `/copyright`
  （地图控件里的归属链接**已经**指向本站 `/copyright`，与此设置项是两处独立的展示）。

参考：<https://osmfoundation.org/wiki/Licence/Attribution_Guidelines>

---

## 8. 商标（与数据许可无关）

「OpenStreetMap」「OSM」「OSMF」「SotM」以及放大镜标识是 **OpenStreetMap Foundation 的商标**，
独立于 ODbL 之外（ODbL §2.3(c) 也明确排除了商标）。要点：

- **描述性使用是免费的**：可以 factually 说明「基于 OpenStreetMap 的地图」；
- 为满足 ODbL 归属要求而使用 OSM 文字商标是**被明确允许**的，所以
  `© OpenStreetMap contributors` 可以直接写；
- 如果在 OSM 项目之外使用这些标识，商标政策 §2.2 建议在首次出现处加一句声明，例如：
  *「OpenStreetMap 是 OpenStreetMap Foundation 的商标。本项目与 OpenStreetMap Foundation
  无隶属关系，也未获其背书。」*
- 若实例**对外公开**，§5.1 要求不能与 openstreetmap.org 混淆（标识、配色、命名、域名都要有
  明显区别）；§4.1 规定域名中包含 OSM 商标需要商标许可；
- 不要把 OSM 商标用在公司/组织的注册名称里（§5.5）。

### 8.1 Minecraft（本项目的主要外观风格）

本项目含大量 **Minecraft 主题**的内容：`minecraft:*` 标签与 iD 预设、
`osm-carto4mc` 的 `minecraft` 分支（样式表与符号）、`minecraft-ocean` / `minecraft-land` 图层。

> **声明**：本项目**与 Mojang Studios / Microsoft 无任何隶属关系**，也未获其背书、赞助或授权。
> 「Minecraft」是 Mojang Studios 的商标。

- 本项目的图标与符号是**按该视觉风格自行编写/绘制**的，**不包含任何 Minecraft 游戏素材文件**。
- 这**不构成** Mojang 的授权。若对外发布，其品牌使用应自行参照
  [Minecraft Usage Guidelines](https://www.minecraft.net/en-us/usage-guidelines) 评估
  （该指引要求在使用 Minecraft 品牌时声明无隶属关系 —— 本文件与 `README.md` 已作此声明）。
- 权利人已删除 `art/`（一批 Minecraft 主题 SVG 图标），因此本仓库不再包含该批素材。

### 8.2 OSM 品牌与「作为上游分支」的关系

本项目对上游的改动以**上游项目的分支**形式发布（见 `PUBLISHING.md`），这一点决定了品牌资源的位置：

| 内容 | 位置 | 处理 |
|---|---|---|
| OSM 网站自带的标识（`osm_logo*.png/svg`）、界面中的「OpenStreetMap」字样 | **只在 `openstreetmap-website` 分支内** | 属于上游项目自身的品牌资源，随该分支发布是正常且必要的；**不需要**删除 |
| openstreetmap-carto 的样式与符号 | `osm-carto4mc` 分支 | CC0，不含 OSM 标识 |
| 本项目自撰内容（`tools/` `sync/` 等） | 主仓库 `osm4mc` | **不含任何 OSM 标识文件**；仅在文档中**描述性**提及 OpenStreetMap |
| 地图上的 `© OpenStreetMap contributors` | 运行时由 `layers.yml` 的 `credit` 生成 | **必须保留**（ODbL §4.3 的要求，见 §7） |

**结论**：把改动放进上游分支之后，**不需要**从上游分支里删除 OSM 商标内容 —— 那些内容本来
就属于 OpenStreetMap 自己的软件。真正要守的是**主仓库 `osm4mc` 的自我表述**：

1. **必须**声明无隶属关系（已写在 `README.md` 与本节开头）；若对外公开，还要避免与
   openstreetmap.org 在标识、配色、命名、域名上混淆（OSMF 商标政策 §5.1）。
2. 项目名 `osm4mc` 里的 “osm” 属**描述性**使用（说明它处理 OSM 数据），本身可以接受；
   若你希望把风险降到最低，可改用不含 “OSM” 的名字 —— 改名只需改 `README.md`、
   `COPYRIGHT.md`、`PUBLISHING.md` 里的项目名，代码中不依赖它。
3. **不要**把「© OpenStreetMap contributors」以外的 OSM 标识（放大镜 logo、OSMF 字样）
   搬进主仓库的自有文件或自有页面里；上游那条 `credit` 字段**必须保留**（见 §7 的坑）。


参考：<https://osmfoundation.org/wiki/Trademark_Policy>；
OSM 瓦片使用政策**不适用**于自建瓦片（其 §9 明确只约束 `tile.openstreetmap.org`）。

---

## 9. 免责声明

- 本文件由项目维护者与自动化工具整理，**不构成法律意见**。涉及商用、对外分发、商标使用、
  §4.3 的第三方数据、或 §6「悠日计划」素材的处理时，请咨询有资质的律师。
- 第三方组件的版本与许可可能随基础镜像、apt 源、npm/RubyGems/PyPI registry 更新而变化。
  本文件的版本号是**记录时**（2026-10-06）的状态，**没有**在 Dockerfile 里固定 apt 包版本。
- 本文件对第三方许可的描述不改变其原始条款；如有冲突，以各项目的官方许可文本为准。
- 本文件不授予任何商标权，也不授予任何未在本文件中明确列出的权利。

---

## 10. 本文件的维护方法

**核对镜像内的包许可**（最权威，直接读发行版自带的 DEP-5 文件）：

```bash
export DOCKER_CONFIG=/home/aoiaihana/osm/.docker
docker run --rm --entrypoint sh osm-tileserver:latest -c \
  'grep -E "^(Files|Copyright|License):" /usr/share/doc/<包名>/copyright'
```

**核对上游项目的许可与改动**：

```bash
git -C openstreetmap-website  log -1 --format='%H %ci %d'
git -C openstreetmap-website  status --porcelain     # 应为 8 个 M + 若干 ??
git -C osm-carto4mc           log -1 --format='%H %ci %d'
git -C osm-carto4mc           describe --tags
git -C osm-carto4mc           status --porcelain
```

**核对 vendored 数据集与前端依赖**：

```bash
cat openstreetmap-website/vendor/id-tagging-schema/base.json      # 记录的 schema 版本
cat openstreetmap-website/config/id_minecraft/manifest.json       # 生成产物的版本与计数
docker compose -p osm-prod -f docker-compose.yml -f docker-compose.prod.yml \
    exec -T web sh -c 'grep -m1 "\"license\"" /app/node_modules/<包名>/package.json'
curl -s https://rubygems.org/api/v1/gems/<gem>.json | grep -i license
```

**工作区组件清点**（本文件 §1.1 的清单靠它维护）：

```bash
ls -1AF /home/aoiaihana/osm
for d in */; do n=$(find "$d" -type f 2>/dev/null | wc -l); printf '%-20s %s\n' "$d" "$n"; done
```

**更新本文件时请一并修改**：

1. 文首的「最后更新」日期；
2. §2.1 / §2.2 里两个上游项目的 commit、分支与 `git describe`；
3. §2.3 的改动列表（若又改了 openstreetmap-website 的上游文件）；
4. §2.4 的 `id-tagging-schema` 版本（`base.json` 变化时）；
5. §3 各表的版本号（基础镜像滚动升级后）；
6. §5 的字体清单（若 `get-fonts.py` 的字体列表变化）；
7. §6.1 的候选素材（权利人确认后移入 §6.2 表格）。
