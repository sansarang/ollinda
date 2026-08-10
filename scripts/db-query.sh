#!/usr/bin/env bash
# 프로덕션 DB 읽기 전용 조회 — railway ssh 경유 (2026-08-10 사장님 승인).
#
# ★ 읽기 전용이 원칙이 아니라 구조다: sqlite URI mode=ro 라 INSERT/UPDATE/DELETE는
#   sqlite 차원에서 거부된다(fail-closed). 쓰기는 반드시 코드+골든 경유 —
#   수동 SQL 쓰기는 헌법 금지 조항(2026-08-03 이관 사고).
#
# 준비물(1회): railway login · ssh keys add(무암호 키) · railway ssh config
#   → ~/.ssh/railway_ed25519 + ~/.ssh/config 의 Host railway-shopcast 블록.
#
# 사용:  ./scripts/db-query.sh "SELECT name, industry FROM tenants"
#        ./scripts/db-query.sh "SELECT piece_id, target_kw FROM blog_publishes LIMIT 5"
set -euo pipefail
SQL="${1:?SQL 필요 (읽기 전용)}"

echo "$SQL" | ssh -i ~/.ssh/railway_ed25519 -o IdentitiesOnly=yes railway-shopcast '
python3 -c "
import json, sqlite3, sys
sql = sys.stdin.read().strip()
c = sqlite3.connect(\"file:/data/shopcast.sqlite?mode=ro\", uri=True)
c.row_factory = sqlite3.Row
try:
    rows = [dict(r) for r in c.execute(sql).fetchall()]
except Exception as e:
    print(\"쿼리 오류:\", e); raise SystemExit(1)
out = rows[:200]
print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
if len(rows) > 200:
    print(f\"... 총 {len(rows)}행 중 200행만 표시\")
"'
