#!/usr/bin/env bash
export RUN_DIR=/workspace/hack/live/clean2/run REPO=/workspace/hack/live/clean2/decision-ledger MODEL=grok-4.7
cd $REPO
echo "=== Чат №3: поступают новые данные (later/) — отдельный коммит | $(date '+%F %T %Z') ==="
set -x
mkdir -p examples/antifraud-subscription/later
cp /workspace/hack/live/clean2/withheld/later/megafon_family_change_20261029.md examples/antifraud-subscription/later/
git add examples/antifraud-subscription/later && git commit -q -m "new data: MegaFon notice 29.10.2026 (later/) arrives for session 3" && git log --oneline -4
python3 -c "import json,subprocess;d=json.loads(subprocess.check_output(['python3','-m','ledger','status','.cases/antifraud']));a=d['approved'];print('approved =',a['option'],a['sha'])"
set +x
/workspace/hack/live/clean2/harness/turn.sh chat3 /workspace/hack/live/clean2/harness/p3.txt 07bd6c1a-0072-43c8-bdf1-6b65b0659243 new
cd $REPO
echo "=== Проверка после Чата №3 (стоп перед вторым утверждением) | $(date '+%F %T %Z') ==="
set -x
python3 -m ledger status .cases/antifraud > $RUN_DIR/ledger-status-after-chat3.json
python3 -c "import json;d=json.load(open('$RUN_DIR/ledger-status-after-chat3.json'));a=d['approved'] or {};dr=d['draft'] or {};print('approved =',a.get('revision'),a.get('option'),a.get('sha'));print('draft =',dr);print('approved_impact keys =',list((d.get('approved_impact') or {}).keys()))"
set +x
