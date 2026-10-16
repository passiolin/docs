#!/usr/bin/env bash
# 集客 AC 巡检(inspect-ac):AC01 服务可用 / AC02 AP 在线 / AC03 终端数异常 / AC04 版本一致 / AC05 终端对账
# 默认只读巡检,退出码:0 健康 / 1 存在 P0·P1 / 2 巡检自身故障
# --fix 为处置动作(AC03 异常 AP 按模板重推配置):按巡检体系授权红线,仅运维负责人明确指示后运行
# 用法:AC_PASS='管理密码' ./ac-inspect.sh [--fix]
set -euo pipefail

AC_BASE="${AC_BASE:-http://192.168.0.1:8080}"
AC_PASS="${AC_PASS:?用法: AC_PASS='xxx' $0 [--fix]}"
THRESHOLD="${THRESHOLD:-3}"          # AC03:clientnum <= THRESHOLD 判异常
UPTIME_GRACE="${UPTIME_GRACE:-600}"  # AC03 豁免:在线不足 600 秒视为刚重启,只记豁免不判异常
FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
fault() { log "巡检自身故障:$*"; exit 2; }

# ---- 1. 登录(getrandom 拿盐 → md5(盐+密码) → sysauth 换 token)= AC01 服务可用 ----
random=$(curl -fsS "$AC_BASE/api/getrandom" | jq -er '.random') || fault "AC 管理口不可达($AC_BASE)"
hash=$(printf '%s' "${random}${AC_PASS}" | md5sum | awk '{print $1}')
login_resp=$(curl -fsS -X POST "$AC_BASE/api/sysauth" \
	-H 'Content-Type: application/json' -d "{\"password\":\"$hash\"}") || fault "sysauth 请求失败"
token=$(jq -er '.token | select(length > 0)' <<<"$login_resp") || fault "登录失败:$(jq -r '.msg' <<<"$login_resp")"
log "AC01 服务可用:登录成功"

# ---- 2. 取数:AP 列表 + AC 概览(AC05 对账用,取不到只降级 AC05) ----
list=$(curl -fsS "$AC_BASE/api/ac/apsearch?pagenum=1&numperpage=500&groupid=all&_t=$(date +%s%3N)" \
	-H "sysauth: $token") || fault "apsearch 请求失败"
jq -e '.ret == 1' <<<"$list" >/dev/null || fault "apsearch 返回异常:$(jq -r '.msg' <<<"$list")"

ov=$(curl -fsS "$AC_BASE/api/status/overview?simplify=no&_t=$(date +%s%3N)" \
	-H "sysauth: $token" 2>/dev/null || true)
[ -n "$ov" ] && jq -e '.acstatus' <<<"$ov" >/dev/null 2>&1 || ov=""

# ---- 3. 逐项判定 ----
p1="" p2="" exempt="" fix_plan=""

# AC02 AP 在线率:任一离线 P1
offline=$(jq -r '.aplist[] | select(.online != "yes") | "\(.name)(\(.ip))"' <<<"$list" | paste -sd、 -)
if [ -n "$offline" ]; then
	p1+="| AC02 | AP 在线率 | 离线:$offline | 0 台离线 | P1 | 查 POE 供电/网线,重电无效再查 AP 自身 |"$'\n'
fi

# AC03 终端数异常:在线且 clientnum <= 阈值;在线不足 UPTIME_GRACE 秒记豁免(刚重启,终端还没回流)
while IFS=$'\t' read -r kind name num mac tpl; do
	[ -n "$kind" ] || continue
	case "$kind" in
	EXEMPT)
		exempt+="| AC03 | $name | 终端数 $num,在线 $((mac / 60)) 分钟,判为刚重启 |"$'\n'
		;;
	HIT)
		p1+="| AC03 | $name | 终端数异常 | $num | ≤ $THRESHOLD | P1 | 按模板 $tpl 重推配置(--fix,须授权);复发则重启 AP |"$'\n'
		fix_plan+="$tpl	$mac	$name"$'\n'
		;;
	esac
done <<<"$(jq -r --argjson th "$THRESHOLD" --argjson grace "$UPTIME_GRACE" '
	.aplist[]
	| select(.online == "yes" and (.clientnum | tonumber) <= $th)
	| if (.uptime | tonumber) < $grace
	  then "EXEMPT\t\(.name)\t\(.clientnum)\t\(.uptime | tonumber)\t"
	  else "HIT\t\(.name)\t\(.clientnum)\t\(.mac)\t\(.template)" end
' <<<"$list")"

# AC04 固件版本一致性:版本不唯一或有低版本标记 P2
if [ "$(jq -r '[.aplist[].version] | unique | length' <<<"$list")" -gt 1 ] \
	|| jq -e '.aplist[] | select(.is_low_version == true)' <<<"$list" >/dev/null; then
	versions=$(jq -r '[.aplist[].version] | unique | join(",")' <<<"$list")
	p2+="| AC04 | 固件版本一致性 | 版本:$versions | 全网单一版本 | P2 | 低版本走升级流程,变更窗口执行 |"$'\n'
fi

# AC05 终端总数对账:Σ(aplist.clientnum) vs overview.acstatus.client_num
if [ -n "$ov" ]; then
	sum_aps=$(jq -r '[.aplist[] | (.clientnum | tonumber)] | add' <<<"$list")
	total_ac=$(jq -r '.acstatus.client_num | tonumber' <<<"$ov")
	diff=$((sum_aps - total_ac)); [ "$diff" -lt 0 ] && diff=$((-diff))
	limit=$((total_ac / 5)); [ "$limit" -lt 5 ] && limit=5
	if [ "$diff" -gt "$limit" ]; then
		p2+="| AC05 | 终端总数对账 | AP 侧 $sum_aps vs AC 侧 $total_ac | 差 ≤ $limit 台 | P2 | 两接口有取样时间差,持续偏差查假死终端 |"$'\n'
	fi
else
	p2+="| AC05 | 终端总数对账 | 数据缺失(overview 不可达) | — | P2 | 检查 AC 状态接口后复巡 |"$'\n'
fi

# ---- 4. 输出报告(下发声明 + 异常置顶,豁免与全量清单在后) ----
if [ "$FIX" = 1 ] && [ -n "$fix_plan" ]; then
	log "下发配置:有(--fix 处置,推送对象与受理结果见文末处置记录)"
else
	log "下发配置:无(未执行处置,未向 AC 下发任何命令)"
fi
log "---- 全量清单(名称/IP/连接数/状态/版本/模板) ----"
jq -r '.aplist[] | [.name, .ip, .clientnum, .online, .version, .template] | @tsv' <<<"$list" |
	awk -F'\t' '{printf "%-10s %-16s %-6s %-6s %-16s %s\n", $1, $2, $3, $4, $5, $6}'

rc=0
if [ -n "$p1$p2" ]; then
	log "---- 异常项(级别|编号|巡检项|当前值|阈值|处置建议) ----"
	printf '%s' "$p1$p2"
	rc=1
else
	log "巡检通过:AC01–AC05 全部健康"
fi
[ -n "$exempt" ] && { log "---- 豁免项 ----"; printf '%s' "$exempt"; }

# ---- 5. 处置(仅 --fix):AC03 异常 AP 按各自模板分组重推 ----
if [ "$FIX" = 1 ]; then
	if [ -n "$fix_plan" ]; then
		while IFS=$'\t' read -r tmpl maclist names; do
			[ -n "$tmpl" ] || continue
			log "处置:向 $names 重推模板 $tmpl(maclist=$maclist)"
			resp=$(curl -fsS -X POST "$AC_BASE/api/ac/apcmd" \
				-H "sysauth: $token" -H 'Content-Type: application/json' \
				-d "$(jq -nc --arg m "$maclist" --arg t "$tmpl" '{cmd:"settemplate",maclist:$m,template:$t}')") \
				|| resp='{"ret":0,"msg":"apcmd 请求失败"}'
			if jq -e '.ret == 1' <<<"$resp" >/dev/null; then
				log "处置已下发:受理成功,终端回流以下一轮巡检为准"
			else
				log "处置失败:$(jq -r '.msg' <<<"$resp")"
			fi
		done <<<"$(jq -rn --arg p "$fix_plan" '
			$p | split("\n") | map(select(length > 0) | split("\t"))
			| group_by(.[0])[]
			| [.[0][0], (map(.[1]) | join(",")), (map(.[2]) | join(","))] | join("\t")
		' <<<"$fix_plan")"
	else
		log "处置:无 AC03 异常,无需重推"
	fi
fi

log "巡检结束(退出码 $rc)"
exit "$rc"
