# 发布到 GitHub 的操作手册

> 目标形态：**1 个主仓库 `osm4mc` + 2 个上游 fork 的分支**。
> 本项目对上游的改动放在上游项目的分支里，因此可以随时 `git submodule update --remote` 跟进上游。

```
github.com/AoiAihana/
├── osm4mc                    ← 主仓库（根目录这些内容）
│   ├── openstreetmap-website/    submodule → 下面第 1 个 fork 的 osm4mc 分支
│   └── osm-carto4mc/             submodule → 下面第 2 个 fork 的 minecraft 分支
├── openstreetmap-website     ← fork of openstreetmap/openstreetmap-website
│   └── 分支 osm4mc              （GPL-2.0：本项目对上游的 8 处修改 + 新增文件）
└── carto4mc                  ← fork of openstreetmap-carto/openstreetmap-carto（仓库已改名）
    └── 分支 minecraft           （CC0-1.0：Minecraft 样式）
```

下面命令里的用户名已按 `AoiAihana` 填好。**建议用 SSH**（`git@github.com:...`），
因为本机已经有 SSH 公钥；用 HTTPS 则需要 Personal Access Token。

> ⚠️ **不要把密码或 token 写进任何文件、也不要贴进聊天。** git 推送只需要：
> SSH 公钥（已加到 GitHub → Settings → SSH and GPG keys），或一次性使用的
> Personal Access Token。GitHub 从 2021 年起就不接受账号密码做 git 操作。

---

## 1. 先决条件

**1.1 把 SSH 公钥加到 GitHub**（推送唯一需要的凭据）

```bash
cat ~/.ssh/id_ed25519.pub     # 复制输出（本机已有这把 ed25519 密钥）
```

粘到 **GitHub → Settings → SSH and GPG keys → New SSH key**，然后验证：

```bash
ssh -T git@github.com
# 期望输出：Hi AoiAihana! You've successfully authenticated...
```

> ⚠️ **不要**把账号密码或 token 写进任何文件、也不要贴进聊天。GitHub 从 2021 年起就不接受
> 账号密码做 git 操作。
>
> 本机 `/etc/ssh/ssh_config.d/` 的属主是 `nobody`，ssh 会报
> `Bad owner or permissions on /etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf`。
> 若 `ssh -T` 因此失败，用 `sudo chown -R root:root /etc/ssh/ssh_config.d` 修一下；
> 临时绕过可以加 `-F /dev/null`。

**1.2 配置 git 身份**（`openstreetmap-website` 原本**没有**配置；**已为它设好仓库级身份**，
下面这条是给别的仓库用的）

```bash
git config --global user.name  "AoiAihana"
# ⚠️ 必须用带数字 ID 的形式：你的账号建于 2021-07-23（在 GitHub 改用新格式之后），
#    旧式 "AoiAihana@users.noreply.github.com" 不保证能关联到你的账号。
git config --global user.email "87853202+AoiAihana@users.noreply.github.com"
```

> `osm-carto4mc` 那 9 个提交作者是 `aoiaihana@localhost`，在 GitHub 上不会关联到你的账号。
> 要么接受，要么在仓库里加一个 `.mailmap` 把它们映射到正确邮箱（不改历史也能让 GitHub 显示正确）。

**1.3 跑发布前检查**（必须，见 §5）

```bash
cd /home/aoiaihana/osm
./tools/check-before-publish.sh
```

---

## 2. 三个 GitHub 仓库（**已建好**，核对如下）

| 仓库 | 类型 | 默认分支 | 现状（2026-10-06 查得） |
|---|---|---|---|
| [`AoiAihana/openstreetmap-website`](https://github.com/AoiAihana/openstreetmap-website) | fork 自官方 | `master` | 只有上游 `master`；待推 `osm4mc` 分支 |
| [`AoiAihana/carto4mc`](https://github.com/AoiAihana/carto4mc) | fork 自官方（**已改名**） | `master` | 只有上游分支；待推 `minecraft` 分支 |
| [`AoiAihana/osm4mc`](https://github.com/AoiAihana/osm4mc) | **新建**（非 fork） | `main` | 里面有一个 GitHub 自动生成的 `Initial commit`（只有 75 字节的占位 `README.md`） |

> ⚠️ **`osm4mc` 的占位提交会让第 6 步的推送被拒**（非快进 / 无关历史）。
> 因为那只是个 75 字节的占位 README，直接**强推覆盖**即可 —— 见 §6。
>
> ⚠️ **carto 的 fork 已经改名为 `carto4mc`**，所以所有 URL 都要用新名
> （`.gitmodules` 与本地的 `mine` remote 都已更新）。

---

## 3. 上游分支（**本地已建好**，你只需 fork + push）

### 3.1 openstreetmap-website → 分支 `osm4mc` ✅ 已提交

本地状态：

```
分支    osm4mc（基于上游 master，1 个提交）
提交    8a1cda2c1  "osm4mc: 本地化部署改造与 iD minecraft 定制"
作者    87853202+AoiAihana@users.noreply.github.com
内容    41 个文件，+7276 / −132（8 个上游文件被改 + 14 项新增，目录展开后 41 个文件）
工作区  干净（0 项改动）
remote  mine → git@github.com:AoiAihana/openstreetmap-website.git（已配好）
```

想看细节：`git -C openstreetmap-website show 8a1cda2c1 --stat`。

**你只需要推送**：

```bash
cd /home/aoiaihana/osm/openstreetmap-website
git push -u mine osm4mc
```

> 可选但推荐：`api/maps_controller.rb` 那处修复（`includes` → `preload`）是**上游的真 bug**，
> 值得单独开一个 PR 回馈上游。开 PR 时从本分支 cherry-pick 那一个文件即可。

### 3.2 openstreetmap-carto → 分支 `minecraft` ✅ 已存在

本地状态：

```
分支    minecraft（相对 v6.1.0 有 12 个提交，其中 9 个是本项目的 minecraft 样式工作）
工作区  干净（0 项改动）—— 已按决定清理完毕
remote  mine → git@github.com:AoiAihana/carto4mc.git（已配好）
```

**你只需要推送**：

```bash
cd /home/aoiaihana/osm/osm-carto4mc
git push -u mine minecraft
```

> **关于 `.git/hooks/pre-push`**：这个仓库原来装了一个「**拒绝一切推送**」的钩子
> （2026-10-01，当时这个 clone 只用来跟上游同步）。现在它已改成
> **「拦官方上游、放行自己的 fork」**：
>
> | 目标 | 结果 |
> |---|---|
> | `mine` → `AoiAihana/carto4mc` | ✓ 放行 |
> | `origin` → `openstreetmap-carto/openstreetmap-carto`（SSH 或 HTTPS 形式） | ✗ 拒绝 |
>
> 原始版本备份在 `.git/hooks/pre-push.orig`；想彻底去掉守卫就 `rm .git/hooks/pre-push`。
> `openstreetmap-website` 里**没有**任何钩子。

> **分支名想改也可以**。你说过 fork「改过名」，如果指的是**分支**也要改名（比如
> `minecraft` → `carto4mc`），在本仓库里先改，再告诉我同步更新 `.gitmodules` 与手册：
>
> ```bash
> git branch -m minecraft carto4mc     # 本地改名
> git push -u mine carto4mc            # 推新名
> git push mine --delete minecraft     # 删掉远端旧名（若已推过）
> ```
>
> 目前**远端只有上游分支**，`minecraft` 还没推上去，所以现在改名是最省事的时候。

> **那 12 个提交的作者**：其中 9 个是 `aoiaihana <aoiaihana@localhost>`，
> 在 GitHub 上**不会关联到你的账号**（`dsh <dsh@local>` 那个同理）。
> 历史已推上去之后改作者需要重写历史，所以两个选择：
>
> 1. **接受现状**（提交能用，只是头像/账号不显示）；
> 2. 加一个 `.mailmap` 把它们映射到正确邮箱 —— 不改历史也能让 GitHub 显示正确：
>    ```
>    AoiAihana <87853202+AoiAihana@users.noreply.github.com> aoiaihana <aoiaihana@localhost>
>    AoiAihana <87853202+AoiAihana@users.noreply.github.com> dsh <dsh@local>
>    ```

**已处理的两项**（记录备查）：

| 项 | 处理 |
|---|---|
| `symbols/man_made/tower_defensive.svg`（Inkscape 重存版） | 已回退到上游原版；你的版本备份在 `.work/svg-backup/tower_defensive.svg.inkscape`（不入库） |
| `symbols/shop/{greengrocer,pet,seafood}.svg.png`（3 个误生成的 12×12 PNG） | 已删除 |

> **推送后建议改一下 fork 的默认分支**：GitHub 上打开 fork → Settings → Default branch，
> 把它从上游的 `master` 改成 `osm4mc`（website）/ `minecraft`（carto）。否则访客打开
> 你的 fork 会看到上游代码，而不是你的改动。

---

## 4. 把根目录变成 `osm4mc` 仓库，并挂上两个 submodule

⚠️ 两个目录**已经存在且各自是 git 仓库**，所以不要用 `git submodule add`（会报路径已存在）。
用下面的**手工登记**方式：`git add <目录>` 会自动记成 gitlink（mode 160000），**不会**把上游文件收进主仓库。
（已实测：`git add` 会提示 *"adding embedded git repository"*，这是预期行为。）

```bash
cd /home/aoiaihana/osm

git init -b main

# --- 登记两个 submodule ---
# ⚠️ 这里必须用 **HTTPS**，不要用 git@github.com: —— .gitmodules 里的 URL 是给
#    **别人 clone 时**用的，SSH 形式会要求每个克隆者都持有密钥；HTTPS 才能匿名拉取。
#    （你自己推送仍走 SSH，那是各仓库的 remote，与这里无关。）
git config -f .gitmodules submodule.openstreetmap-website.path   openstreetmap-website
git config -f .gitmodules submodule.openstreetmap-website.url    https://github.com/AoiAihana/openstreetmap-website.git
git config -f .gitmodules submodule.openstreetmap-website.branch osm4mc

git config -f .gitmodules submodule.osm-carto4mc.path   osm-carto4mc
git config -f .gitmodules submodule.osm-carto4mc.url    https://github.com/AoiAihana/carto4mc.git
git config -f .gitmodules submodule.osm-carto4mc.branch minecraft

git add .gitmodules

# --- 记录两个 gitlink（警告 "adding embedded git repository" 是正常的）---
git add openstreetmap-website osm-carto4mc

# --- 收本项目自己的内容（.gitignore 会挡掉机密、数据、生成物）---
git add -A
git status --short | head -40     # 确认没有意外文件

git commit -m "osm4mc: 初始提交"
```

**验证 gitlink 生效**：

```bash
git ls-files -s openstreetmap-website osm-carto4mc
# 期望看到 mode 160000 的两行，而不是几千个 100644
```

> **前提**：submodule 里记录的 commit 必须已经存在于 fork 上，否则别人 clone 不下来。
> 所以第 3 步必须先做完。**推送前先确认**：
>
> ```bash
> git -C openstreetmap-website ls-remote mine osm4mc     # 应回一个 SHA
> git -C osm-carto4mc          ls-remote mine minecraft  # 应回一个 SHA
> ```
>
> 这两个 SHA 必须与 `git ls-files -s` 里记录的一致，否则克隆者会卡在
> `fatal: remote error: upload-pack: not our ref`。

---

## 5. 发布前检查（每次 push 之前都跑）

```bash
./tools/check-before-publish.sh
```

它会检查：机密文件是否会被跟踪、有没有超大文件、有没有把该忽略的目录收进来、
有没有**软链**会被提交、有没有嵌套 `.git` 没处理、权利未确认的素材、缺失的第三方许可文本。

**当前预期结果**（2026-10-06 实测）：

```
会被提交的文件：103 个        总大小：约 770 KB
结论：通过 12 · 警告 1 · 必须解决 0
```

那 1 个警告是「嵌套 git 仓库」—— 正是第 4 步要处理的 submodule 指针，属预期。

**另外手工确认一次**：

```bash
# 敏感文件（含导入器软链进来的 token.txt）
git ls-files | grep -E '\.token$|token\.txt$|\.env\.prod$|\.dump$|guard\.token' \
  && echo "★ 有敏感文件被跟踪！" || echo "OK：没有敏感文件"

# 软链（git 只存目标路径、不含内容，但克隆后是死链）
git ls-files -s | awk '$1=="120000"{print "  软链: "$4}'

# 规模
git ls-files | wc -l                        # 应为 103 左右（含 2 个 gitlink）
du -sh .git                                 # 应远小于 100 MB
git count-objects -vH | grep size-pack
```

> **导入器的两个软链**：`railway-import/scripts/{token.txt,l0cache}` 在本地被软链到
> `.work/railwaymap/` 下，已在 `.gitignore` 里排除。它们是**上传令牌**与 **L0 瓦片缓存**：
> 前者按 `railway-import/README.md` 自行准备，后者首次运行会自动生成，**都不应入库**。
> （已实测：即使误提交，git 也只会存软链的目标路径字符串，**不会**把令牌内容写进仓库。）

---

## 6. 推送主仓库

⚠️ `osm4mc` 是**新建**仓库，远端 `main` 上已有 GitHub 自动生成的历史：`Initial commit`
（加了 2 行的占位 `README.md`）→ `Delete README.md`（**净结果是一棵空树**）。
它与本地 `main` **没有共同祖先**，直接 `git push` 会被拒绝，所以必须强推。

```bash
cd /home/aoiaihana/osm

git remote add origin git@github.com:AoiAihana/osm4mc.git   # 已配好
git fetch origin

# 确认远端是什么（2026-10-06 实测：两个提交，净空树）
git log --oneline origin/main
git log --stat origin/main

# 强推覆盖。用 --force-with-lease：万一远端在你 fetch 之后又变了，它会拒绝而不是盲推。
git push -u origin main --force-with-lease
```

> 远端那两个提交**没有任何内容损失**（README 加了又被删掉，树是空的），强推只丢掉
> 这两条占位历史。想留个记录的话，它们仍能在 GitHub 的仓库活动页看到，也可以从
> 「Deleted branches / force-push 前的 commit」里找回（GitHub 会保留一段时间的悬空对象）。

> **若你想保留那个占位提交**，改用：
>
> ```bash
> git pull --allow-unrelated-histories origin main
> # 解决 README.md 冲突（保留本地这份 72 行的），然后：
> git commit -m "merge: 并入 GitHub 的占位提交"
> git push -u origin main
> ```

推完后在 GitHub 上确认三件事：

1. **README 正常显示**（我们这份带组成表与无隶属关系声明，而不是 75 字节的占位）；
2. **许可证徽标为 CC0-1.0**（来自根目录 `LICENSE`）；
3. **两个 submodule 是可点击的链接**，分别指向 `AoiAihana/openstreetmap-website` 与
   `AoiAihana/carto4mc`。

---

## 7. 之后怎么跟进上游更新

submodule 里 **`origin` = 官方上游**、**`mine` = 你自己的 fork**。典型流程（以 website 为例）：

```bash
cd openstreetmap-website

git fetch origin                      # 上游最新
git rebase origin/master              # 把你的 osm4mc 提交叠到上游最新之上
git push --force-with-lease mine osm4mc   # rebase 改写了历史，必须 force（用 with-lease 更安全）

cd ..                                 # 回到主仓库
git add openstreetmap-website         # 记录新的 gitlink
git commit -m "chore: 跟进 openstreetmap-website 上游"
git push origin main
```

carto 同理，把 `origin/master` 换成 `origin/master`、分支换成 `minecraft`
（它基于 tag `v6.1.0`，更常见的是 `git rebase origin/master` 或 `git rebase v6.2.0` 之类的新 tag）。

> 注意：**不要**用 `git submodule update --remote` 来「跟随上游」—— 它跟随的是
> `.gitmodules` 里写的 `branch`（也就是**你自己 fork 的分支**），不会碰上游。
> 想更新主仓库记录的指针，用上面的 `git add <submodule> && git commit` 即可。

---

## 8. 万一推错了（机密进了历史）

`.gitignore` **不会**影响已经被跟踪的文件，所以一旦提交（尤其已 push）就要按「已泄露」处理：

1. **先轮换机密**：重新生成 `SECRET_KEY_BASE`（`./tools/switch-env.sh` 会生成）、
   作废 `coastline/.token` 与守卫令牌（删掉 `openstreetmap-website/tmp/guard.token` 并重启 web 会自动重发）。
2. 再清理历史：`git filter-repo --path <文件> --invert-paths`（或 BFG），然后强推。
3. 通知协作者重新 clone。

> 这也是为什么第 5 步要在**每次 push 之前**跑。

---

## 9. 已排除与仍待确认的内容

**已按权利人决定排除**（`.gitignore` 整目录，不会进仓库）：

```
coastline/       本地工程目录（内含 OAuth 令牌）
mcmap/           研究产出（含第三方仓库克隆）
data/            字体 / shapefile / 瓦片
backup/*         除 backup.conf 外的全部（含 GB 级 dump）
.work/  .qa-verifier/  .npmcache/  .docker/  dyn2xyz/.venv/
*/out/           三个导入器的产物
openstreetmap-website/tmp/   含守卫的 API 令牌
.env.prod  .prod-admin-password
**/token.txt  **/l0cache     导入器软链进来的上传令牌 / L0 缓存（见 §5）
```

**已补齐**：`openstreetmap-website/vendor/id-tagging-schema/LICENSE.md`（ISC 全文），
该文件是上游分支里的新增文件，会随第 3 步的分支提交一起推上去。

**仍待确认**（见 [COPYRIGHT.md](COPYRIGHT.md) §6.1）：三个导入器 `out/` 里的数据来自
`railwaymap.big-brother.top`。`out/` 已排除，因此**不影响本次发布**；但日后若要单独发布
这些数据，需要先确认授权。

**三个「个人文件」请你决定要不要发**（目前**会**被提交）：

| 文件 | 内容 | 说明 |
|---|---|---|
| `给人写的readme.txt` | 2.4 KB 的制作者说明 | 文件开头写明「请任何人工智能不要读取以下内容」——它是给人看的，公开与否由你定 |
| `carto之后的计划.txt` | 19 KB 的后续开发计划 | 公开相当于把路线图发出去，看你的意愿 |
| `h.html` | 32 KB 的网页存档 | 只是抓下来的页面快照，没有保留价值 |

不想发的话，把它们加进 `.gitignore`（或直接 `rm`），然后重跑 `./tools/check-before-publish.sh` 确认。
