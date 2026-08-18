#!/usr/bin/env bash
# 배포 전 게이트 — push 직전에 /admin/busy를 확인한다.
#
# 왜 스크립트인가: 같은 사고를 세 번 냈다(2026-07-24, 07-30, 08-02).
#   세 번 다 "확인하겠다"는 규율은 있었고, 세 번 다 확인하지 않았다.
#   규율이 세 번 실패했으면 규율이 아니라 장치가 필요하다.
#
# 사용:  ./scripts/safe-push.sh            # 확인 후 push
#        ./scripts/safe-push.sh --dry      # 확인만
#        SHOPCAST_FORCE_PUSH=1 ./scripts/safe-push.sh   # 사장님이 명시 승인했을 때만
set -euo pipefail

# 🧾 커밋 안 된 변경으로는 push하지 않는다(2026-08-18 사고).
#   이 스크립트는 **커밋하지 않는다** — 원장 파일만 자동 커밋하고 HEAD를 push한다.
#   그런데 골든은 워킹트리를 검사한다. 커밋을 빠뜨리면
#     "✅ 골든 전체 통과" → "push 성공" 이 그대로 뜨는데 **배포물엔 그 변경이 없다.**
#   실제로 죽은 라우트 3건 복구가 이 구멍으로 배포되지 않았고, 배포됐다고 보고할 뻔했다.
#   (면역 검진도 staged_diff()를 보므로, 스테이징이 없으면 빈 diff를 검사한 셈이 된다.)
#   data/ 는 아래에서 스크립트가 직접 커밋하므로 제외한다.
DIRTY="$(git status --porcelain -- . ':(exclude)data' | grep -v '^??' || true)"
if [ -n "$DIRTY" ]; then
  echo "🛑 커밋하지 않은 변경이 있다 — 이대로 push하면 이 변경은 배포되지 않는다:"
  echo "$DIRTY" | sed 's/^/  /'
  echo "→ 먼저 커밋하라:  git add -A && git commit -m \"...\""
  exit 6
fi
UNTRACKED="$(git status --porcelain -- . ':(exclude)data' | grep '^??' || true)"
if [ -n "$UNTRACKED" ] && [ "${SHOPCAST_ALLOW_UNTRACKED:-0}" != "1" ]; then
  echo "🛑 추적되지 않는 파일이 있다 — 로컬에만 있으므로 프로덕션에서 ImportError가 난다:"
  echo "$UNTRACKED" | sed 's/^/  /'
  echo "→ 커밋하거나, 아직 안 쓰는 파일이면 SHOPCAST_ALLOW_UNTRACKED=1 로 넘겨라."
  exit 6
fi

HOST="${SHOPCAST_HOST:-https://ollinda.kr}"
AUTH="${SHOPCAST_ADMIN_AUTH:-}"
[ -z "$AUTH" ] && { echo "❌ SHOPCAST_ADMIN_AUTH(admin:비밀번호)가 없다"; exit 2; }

RESP="$(curl -sf -u "$AUTH" "$HOST/admin/busy")" || {
  echo "❌ busy 확인 실패 — 서버 응답 없음. 확인 못 한 상태로는 push하지 않는다."; exit 3; }

python3 - "$RESP" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
busy, ghosts = d.get("busy") or [], d.get("ghosts") or []
for g in ghosts:
    print(f"  💀 유령(배포 무관): {g.get('tenant')} {g.get('stage')} idle={g.get('idle_sec')}s")
if busy:
    print("🛑 진행 중인 작업이 있다 — 배포하면 죽는다:")
    for b in busy:
        print(f"  · {b.get('type')} / {b.get('tenant')} / {b.get('stage','')} idle={b.get('idle_sec','-')}s")
    sys.exit(1)
print("✅ busy 없음 — 배포해도 죽는 작업이 없다")
PY
GATE=$?

if [ "$GATE" -ne 0 ]; then
  if [ "${SHOPCAST_FORCE_PUSH:-0}" = "1" ]; then
    echo "⚠️  강제 push — 사장님 명시 승인이 있을 때만 정당하다"
  else
    echo "→ 작업이 끝난 뒤 다시 실행하라. 사장님 승인이 있으면 SHOPCAST_FORCE_PUSH=1."
    exit 1
  fi
fi

[ "${1:-}" = "--dry" ] && { echo "(--dry: push 생략)"; exit 0; }

# 🛡 배포 전 면역 검진(2026-08-05) — 이 변경이 과거 사고와 같은 모양인가.
#   기본은 경고다. 차단은 원장상 재발 2회 이상 유형에만(R3) —
#   오탐이 쌓여 검진을 꺼버리게 만드는 것이 최악의 결말이라 기본값을 통과로 둔다.
if [ "${SHOPCAST_SKIP_IMMUNE:-0}" != "1" ]; then
  SHOPCAST_SECRET=test python3 -c "
from app.services.immune import prediag as P
r = P.inspect(P.staged_diff())
print(P.render(r))
raise SystemExit(9 if r.get('blocked') else 0)
" || {
    _rc=$?
    if [ "$_rc" = "9" ] && [ "${SHOPCAST_IMMUNE_OVERRIDE:-0}" != "1" ]; then
      echo "→ SHOPCAST_IMMUNE_OVERRIDE=1 로 넘길 수 있습니다."; exit 5
    fi
  }
fi

# 📒 사고 원장 갱신(2026-08-05) — 원장은 git 이력에서 파생되는데 배포 이미지엔 .git이 없다.
#   그래서 배포 시점에 코드 트리에서 다시 뽑아 싣는다. 서버는 읽기만 한다.
if [ "${SHOPCAST_SKIP_IMMUNE:-0}" != "1" ]; then
  SHOPCAST_SECRET=test python3 -c '
from app.services.immune import ledger as L
d = L.build()
n = L.write(d)
print("📒 원장 갱신 — %d행(확정 %d · 구전 %d)" % (n, d["confirmed"], d["hearsay"]))
from app.services.immune import report as R
m = R.snapshot(d["rows"])["months"][0]
print("📊 지표 — %s: 커밋 %d건, 사장님 발견 %d건 (100커밋당 %s)"
      % (m["month"], m["commits"], m["user_found"], m["per100"]))
' && {
    if ! git diff --quiet -- data/ 2>/dev/null || [ -n "$(git ls-files -o --exclude-standard data/)" ]; then
      git add data/incidents.jsonl data/immune_metrics.json && git commit -q -m "원장 갱신(자동) — 사고가 항체가 되는 폐루프"         && echo "  → 원장 변경분을 커밋했습니다"
    fi
  }
fi

# 🧪 골든 전체 통과 없이는 push하지 않는다(2026-08-03 사고: 실패한 테스트가 커밋·배포됐다).
#   원인은 파이프라인 종료코드를 잘못 읽은 것 — 사람 눈이 아니라 게이트가 막아야 한다.
if [ "${SHOPCAST_SKIP_TESTS:-0}" != "1" ]; then
  if ! SHOPCAST_SECRET=test python3 -m pytest tests/ -q >/tmp/_gold.log 2>&1; then
    echo "🛑 골든 실패 — push 중단"; tail -5 /tmp/_gold.log; exit 4
  fi
  echo "✅ 골든 전체 통과 ($(grep -Eo '[0-9]+ passed' /tmp/_gold.log | tail -1))"
fi
git push origin "$(git rev-parse --abbrev-ref HEAD)"
