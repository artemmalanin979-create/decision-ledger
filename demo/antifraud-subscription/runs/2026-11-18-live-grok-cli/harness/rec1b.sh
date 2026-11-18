#!/usr/bin/env bash
export RUN_DIR=/workspace/hack/live/clean2/run REPO=/workspace/hack/live/clean2/decision-ledger MODEL=grok-4.7
SID=$(cat /workspace/hack/live/clean2/harness/chat1.sid)
cd $REPO
echo "=== Комитет передаёт ответы: manager.md добавляется в репозиторий только сейчас | $(date '+%F %T %Z') ==="
set -x
cp /workspace/hack/live/clean2/withheld/manager.md examples/antifraud-subscription/manager.md
git add examples/antifraud-subscription/manager.md && git commit -q -m "committee answers (manager.md) delivered after intake questions" && git log --oneline -1
set +x
/workspace/hack/live/clean2/harness/turn.sh chat1-answers /workspace/hack/live/clean2/harness/p1-answers.txt $SID resume
/workspace/hack/live/clean2/harness/turn.sh chat1-1B /workspace/hack/live/clean2/harness/p1b.txt $SID resume
cd $REPO
echo "=== Проверка гейта | $(date '+%F %T %Z') ==="
set -x
python3 -m ledger status .cases/antifraud > $RUN_DIR/ledger-status-after-chat1.json
python3 -c "import json;d=json.load(open('$RUN_DIR/ledger-status-after-chat1.json'));print('approved =',d['approved']);print('draft =',d['draft'])"
ls .cases/antifraud
set +x
