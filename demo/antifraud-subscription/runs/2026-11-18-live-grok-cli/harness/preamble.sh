#!/usr/bin/env bash
cd /workspace/hack/live/clean2/decision-ledger
echo "=== Clean-room подготовка (коммит 7ba0640, economics_2027.json без готовых планов) | $(date '+%F %T %Z') ==="
set -x
git log --oneline
git status --short
ls -a; ls examples/antifraud-subscription
ls -la .cases 2>/dev/null || echo "Чистый старт подтверждён"
# тесты из исходного коммита 0d16a19 (вынесены из дерева агента), прогон на временной копии
rm -rf /tmp/cr && cp -r . /tmp/cr && cp -r ../withheld/tests /tmp/cr/tests && (cd /tmp/cr && make test 2>&1 | tail -3); rm -rf /tmp/cr
/home/box/.local/bin/agent --version
set +x
