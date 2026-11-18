#!/usr/bin/env bash
export RUN_DIR=/workspace/hack/live/clean2/run REPO=/workspace/hack/live/clean2/decision-ledger MODEL=grok-4.7
cd $REPO
echo "=== Чат №3, продолжение того же сеанса 07bd6c1a-0072-43c8-bdf1-6b65b0659243: утверждение Решения №2. Состояние до хода | $(date '+%F %T %Z') ==="
set -x
python3 -c "import json,subprocess;d=json.loads(subprocess.check_output(['python3','-m','ledger','status','.cases/antifraud']));a=d['approved'];print('approved =',a['revision'],a['option'],a['sha']);print('draft =',d['draft'])"
set +x
/workspace/hack/live/clean2/harness/turn.sh chat3b /workspace/hack/live/clean2/harness/p3b-msg.txt 07bd6c1a-0072-43c8-bdf1-6b65b0659243 resume
cd $REPO
echo "=== Проверка после утверждения Решения №2 | $(date '+%F %T %Z') ==="
set -x
python3 -m ledger status .cases/antifraud > $RUN_DIR/ledger-status-after-chat3b.json
python3 -c "import json;d=json.load(open('$RUN_DIR/ledger-status-after-chat3b.json'));a=d['approved'] or {};print('approved =',a.get('revision'),a.get('option'),a.get('sha'),a.get('by'),a.get('reason'));print('expected sha = e15c1b214e7949f9d7dc6b5a16d8d6c4fa64a6fb4467d4bba6be8c9bd5ce4529');print('history =',d.get('history'));print('draft =',d.get('draft'))"
set +x
