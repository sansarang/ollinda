#!/usr/bin/env bash
# 프로덕션 DB 스냅샷 당기기 — sqlite MCP·로컬 탐색용 (2026-08-10 사장님 5종 MCP 지시).
#
# 라이브 DB는 Railway 볼륨 안이라 로컬 MCP가 못 닿는다 → 서버에서 sqlite backup API로
# '일관된 스냅샷'을 만든 뒤 통째로 내려받는다(WAL 중간 상태 복사 위험 회피).
# 스냅샷은 읽기 탐색용 사본 — 여기에 쓰는 것은 프로덕션에 아무 영향 없다(그리고 gitignore).
#
# 사용: ./scripts/db-snapshot.sh   → .prod-snapshot.sqlite 갱신(수 초)
set -euo pipefail
cd "$(dirname "$0")/.."

ssh -i ~/.ssh/railway_ed25519 -o IdentitiesOnly=yes railway-shopcast \
  "python3 -c \"
import sqlite3
src = sqlite3.connect('file:/data/shopcast.sqlite?mode=ro', uri=True)
dst = sqlite3.connect('/tmp/snap.sqlite')
src.backup(dst)
dst.close()
\" && cat /tmp/snap.sqlite && rm -f /tmp/snap.sqlite" > .prod-snapshot.sqlite.tmp

mv .prod-snapshot.sqlite.tmp .prod-snapshot.sqlite
echo "✅ .prod-snapshot.sqlite $(du -h .prod-snapshot.sqlite | cut -f1) — $(date '+%H:%M:%S') 기준 일관 스냅샷"
