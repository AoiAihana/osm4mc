#!/usr/bin/env bash
#
# 发布前检查：把「一旦 push 就麻烦」的东西逐条列出来。
#
# 只读，不改任何东西。每次 push 之前跑一次：
#     ./tools/check-before-publish.sh
#
# 它检查的是「会被提交的文件」——也就是 git 会收录的那些：已经跟踪的 + 尚未跟踪
# 但**没有**被 .gitignore 挡住的。还没 git init 也能跑。
#
# 退出码：0 = 可以发布（可能有警告）；1 = 有必须先解决的问题。
#
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
ROOT=$PWD

PASS=0; WARN=0; FAIL=0
pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
# 算出「会被提交的文件清单」
# ---------------------------------------------------------------------------
IN_REPO=0
git rev-parse --git-dir >/dev/null 2>&1 && IN_REPO=1

if [ "$IN_REPO" = 1 ]; then
    PUBLISHABLE=$({ git ls-files; git ls-files --others --exclude-standard; } | sort -u)
else
    # 还没 git init：在一个临时目录里建一个真仓库，把 work tree 指向本目录，
    # 这样 git 会套用本目录的 .gitignore，而不会在这里创建 .git
    TMPD=$(mktemp -d 2>/dev/null) || TMPD=""
    if [ -n "$TMPD" ]; then
        git -C "$TMPD" init -q 2>/dev/null
        PUBLISHABLE=$(GIT_DIR="$TMPD/.git" GIT_WORK_TREE="$ROOT" \
            git ls-files --others --exclude-standard 2>/dev/null | sort -u)
        rm -rf "$TMPD"
    else
        PUBLISHABLE=""
    fi
fi

N_FILES=$(printf '%s\n' "$PUBLISHABLE" | grep -c . || true)
printf '\033[1m发布前检查\033[0m  %s\n' "$ROOT"
printf '  会被提交的文件：%s 个' "$N_FILES"
[ "$IN_REPO" = 1 ] && printf '（已是 git 仓库）\n' || printf '（尚未 git init，按 .gitignore 推算）\n'

if [ "$N_FILES" -eq 0 ]; then
    printf '\n  无法计算文件清单，后续检查跳过。若已 git init，请先在仓库根目录运行。\n'
    exit 1
fi

matches() { printf '%s\n' "$PUBLISHABLE" | grep -E "$1" || true; }
count_matches() { matches "$1" | grep -c . || true; }

# ---------------------------------------------------------------------------
head_ "1. 机密文件（绝不能进仓库）"
SECRET_PAT='(\.token$|(^|/)token\.txt$|\.env\.prod$|\.prod-admin-password$|guard\.token$|(^|/)id_rsa|\.pem$|\.p12$|credentials\.yml\.enc|master\.key$|(^|/)\.netrc$)'
if [ "$(count_matches "$SECRET_PAT")" -gt 0 ]; then
    matches "$SECRET_PAT" | sed 's/^/      /'
    fail "有敏感文件会被提交（见上）"
else
    pass "没有敏感文件会被提交"
fi

# ---------------------------------------------------------------------------
head_ "2. 不该发布的目录 / 生成物"
BAD_PAT='^(coastline/|mcmap/|backup/.*\.dump|data/|\.work/|\.npmcache/|\.docker/|\.qa-verifier/|(natural-import|railway-import|sfrt-import)/out/|dyn2xyz/\.venv/|.*\.venv/|.*\.dump$)'
if [ "$(count_matches "$BAD_PAT")" -gt 0 ]; then
    matches "$BAD_PAT" | head -10 | sed 's/^/      /'
    fail "有 $(count_matches "$BAD_PAT") 个不该发布的文件会被提交（见上，最多显示 10 个）"
else
    pass "数据 / 备份 / 虚拟环境 / 生成物 均已被忽略"
fi

# 软链：git 只记录「目标路径字符串」（所以不含机密内容），但目标通常是本机路径，
# 别人克隆后就是死链。导入器会把 token.txt / l0cache 软链到 .work/ 下，必须排除。
SYMLINKS=$(while IFS= read -r f; do [ -L "$f" ] && echo "$f"; done <<< "$PUBLISHABLE")
if [ -n "$SYMLINKS" ]; then
    printf '%s\n' "$SYMLINKS" | head -10 | while IFS= read -r f; do
        printf '      %-46s -> %s\n' "$f" "$(readlink "$f")"
    done
    warn "$(printf '%s\n' "$SYMLINKS" | grep -c .) 个软链会被提交（内容不含机密，但克隆后是死链；建议加入 .gitignore）"
else
    pass "没有软链会被提交"
fi

# ---------------------------------------------------------------------------
head_ "3. 大文件（GitHub 单文件硬上限 100 MB）"
BIG50=""; BIG5=""
while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -f "$f" ] || continue
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    if [ "$sz" -gt 52428800 ]; then BIG50="$BIG50$f|$sz\n"
    elif [ "$sz" -gt 5242880 ]; then BIG5="$BIG5$f|$sz\n"; fi
done <<< "$PUBLISHABLE"

if [ -n "$BIG50" ]; then
    printf "$BIG50" | while IFS='|' read -r f sz; do printf '      %8s  %s\n' "$(numfmt --to=iec "$sz")" "$f"; done
    fail "有文件超过 50 MB（GitHub 会很慢，且接近 100 MB 上限）"
else
    pass "没有超过 50 MB 的文件"
fi
if [ -n "$BIG5" ]; then
    printf "$BIG5" | head -5 | while IFS='|' read -r f sz; do printf '      %8s  %s\n' "$(numfmt --to=iec "$sz")" "$f"; done
    warn "$(printf "$BIG5" | grep -c .) 个文件超过 5 MB（见上，最多显示 5 个）"
else
    pass "没有超过 5 MB 的文件"
fi

# ---------------------------------------------------------------------------
head_ "4. 嵌套 git 仓库（会被记成 submodule 而非收录文件）"
NESTED=""
for d in */; do
    d=${d%/}
    [ -d "$d/.git" ] || continue
    if [ "$IN_REPO" = 1 ] && git ls-files -s "$d" 2>/dev/null | grep -q '^160000'; then
        pass "$d/ 已登记为 submodule（gitlink）"
    else
        NESTED="$NESTED$d\n"
    fi
done
if [ -n "$NESTED" ]; then
    printf "$NESTED" | sed 's/^/      /'
    if [ "$IN_REPO" = 1 ]; then
        warn "以上目录含 .git 但还不是 gitlink —— git add 时只会记录一个 commit 指针，里面的文件不会被收录（这是预期行为，见 PUBLISHING.md §4）"
    else
        warn "以上目录含 .git：git add 时会被记成 submodule 指针，里面的文件不会被收录。要单独发布它们（作为上游项目的分支），见 PUBLISHING.md §3"
    fi
else
    pass "没有未处理的嵌套 git 仓库"
fi

# ---------------------------------------------------------------------------
head_ "5. 权利未确认的素材（COPYRIGHT.md §6.1）"
RIGHTS_PAT='^(coastline/ref/|mcmap/sources/)'
n=$(count_matches "$RIGHTS_PAT")
if [ "$n" -gt 0 ]; then
    # 按目录汇总，避免逐文件刷屏
    matches "$RIGHTS_PAT" | awk -F/ '{print $1"/"$2}' | sort | uniq -c \
        | while read -r c d; do printf '      %-22s %s 个文件\n' "$d" "$c"; done
    warn "$n 个「权利未确认」的素材会被提交 —— 权利人确认前请勿发布（见 COPYRIGHT.md §6.1）"
else
    pass "没有权利未确认的素材会被提交"
fi
[ -d art ] && warn "art/ 仍存在 —— 若未确认权利，请删除或加入 .gitignore"

# ---------------------------------------------------------------------------
head_ "6. 第三方许可文本"
if [ -d openstreetmap-website/vendor/id-tagging-schema ]; then
    if find openstreetmap-website/vendor/id-tagging-schema -maxdepth 1 -iname 'LICENSE*' | grep -q .; then
        pass "vendor/id-tagging-schema 已带许可文本"
    else
        fail "vendor/id-tagging-schema 缺许可文本（该数据集是 ISC，随仓库发布必须附上，见 COPYRIGHT.md §2.4）"
    fi
else
    warn "找不到 vendor/id-tagging-schema（若已改成 submodule 或未 fetch，可忽略）"
fi

# ---------------------------------------------------------------------------
head_ "7. 必需的发布文件"
for f in LICENSE README.md COPYRIGHT.md .gitignore; do
    [ -f "$f" ] && pass "$f 存在" || fail "$f 缺失"
done
[ -f .gitattributes ] && pass ".gitattributes 存在" || warn ".gitattributes 缺失（不影响功能，但语言统计会很难看）"

# ---------------------------------------------------------------------------
head_ "8. 体积概览"
if [ "$IN_REPO" = 1 ]; then
    printf '  工作区：%s\n' "$(du -sh --exclude=.git . 2>/dev/null | cut -f1)"
    printf '  .git   ：%s\n' "$(du -sh .git 2>/dev/null | cut -f1)"
else
    printf '  （尚未 git init，无法给出 .git 体积）\n'
fi
printf '  会被提交的文件总大小：%s\n' "$(
    printf '%s\n' "$PUBLISHABLE" | while IFS= read -r f; do
        [ -f "$f" ] && stat -c %s "$f"
    done | awk '{s+=$1} END {printf "%d", s+0}' | numfmt --to=iec 2>/dev/null || echo '?')"

printf '\n  按顶层目录统计（文件数）：\n'
printf '%s\n' "$PUBLISHABLE" | awk -F/ 'NF>1{print $1"/"} NF==1{print "(根目录)"}' \
    | sort | uniq -c | sort -rn | head -14 | awk '{printf "      %-22s %s\n", $2, $1}'

# ---------------------------------------------------------------------------
printf '\n\033[1m结论\033[0m  通过 %d · 警告 %d · 必须解决 %d\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf '  \033[31m还不能发布\033[0m —— 先解决上面标 ✗ 的问题。\n'
    exit 1
fi
if [ "$WARN" -gt 0 ]; then
    printf '  \033[33m可以发布，但有警告\033[0m —— 请逐条确认。\n'
    exit 0
fi
printf '  \033[32m可以发布\033[0m\n'
