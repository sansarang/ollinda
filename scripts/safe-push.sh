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

# 🧪 골든 전체 통과 없이는 push하지 않는다(2026-08-03 사고: 실패한 테스트가 커밋·배포됐다).
#   원인은 파이프라인 종료코드를 잘못 읽은 것 — 사람 눈이 아니라 게이트가 막아야 한다.
if [ "${SHOPCAST_SKIP_TESTS:-0}" != "1" ]; then
  if ! SHOPCAST_SECRET=test python3 -m pytest tests/ -q >/tmp/_gold.log 2>&1; then
    echo "🛑 골든 실패 — push 중단"; tail -5 /tmp/_gold.log; exit 4
  fi
  echo "✅ 골든 전체 통과 ($(grep -Eo '[0-9]+ passed' /tmp/_gold.log | tail -1))"
fi
git push origin "$(git rev-parse --abbrev-ref HEAD)"
