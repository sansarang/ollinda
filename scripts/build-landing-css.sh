#!/usr/bin/env bash
# 랜딩 CSS 빌드 — Tailwind CDN(런타임 JIT 397KB JS)을 빌드된 정적 CSS로 대체(모바일 성능).
# 랜딩·약관·개인정보 페이지와, 랜딩 안에 렌더되는 서버 HTML(티저·순위진단 결과)이
# 전부 app/*.py 문자열에서 나오므로 content는 app 전체를 스캔한다.
# 사용: ./scripts/build-landing-css.sh   (출력: app/static/landing.css)
set -euo pipefail
cd "$(dirname "$0")/.."

cat > /tmp/tw-landing.config.js <<'EOF'
module.exports = {
  content: ["./app/**/*.py"],
  corePlugins: { preflight: true },
};
EOF
cat > /tmp/tw-landing-input.css <<'EOF'
@tailwind base;
@tailwind components;
@tailwind utilities;
EOF

npx -y tailwindcss@3.4.17 -c /tmp/tw-landing.config.js -i /tmp/tw-landing-input.css \
  -o app/static/landing.css --minify
echo "✅ app/static/landing.css $(wc -c < app/static/landing.css | tr -d ' ')B"
