#!/usr/bin/env bash
# Usage: turn.sh <label> <prompt-file> <session-uuid> <new|resume>
# Runs ONE real turn of the Grok Build CLI (agent) headless, visible on the recorded terminal.
set -uo pipefail
LABEL="$1"; PFILE="$2"; SID="$3"; MODE="$4"
REPO="${REPO:?}"
RUN_DIR="${RUN_DIR:?}"
MODEL="${MODEL:-grok-4.6}"
cd "$REPO"
B=$'\e[1;36m'; Y=$'\e[1;33m'; N=$'\e[0m'
echo "${B}=== ${LABEL} | $(date '+%F %T %Z') | model=${MODEL} | session=${SID} ===${N}"
echo "${Y}>>> Сообщение человека (отправлено как есть):${N}"
cat "$PFILE"; echo
echo "${B}--- ответ агента ---${N}"
START=$(date +%s)
if [ "$MODE" = new ]; then SESS=(--session-id "$SID"); else SESS=(--resume "$SID"); fi
/home/box/.local/bin/agent -p "$(cat "$PFILE")" -m "$MODEL" "${SESS[@]}" --always-approve \
  --disallowed-tools ask_user_question --output-format streaming-json 2>"$RUN_DIR/${LABEL}.stderr.txt" \
  | tee "$RUN_DIR/${LABEL}.stream.jsonl" | python3 /workspace/hack/live/harness/pretty.py
RC=${PIPESTATUS[0]}
END=$(date +%s)
echo "${B}=== ${LABEL} завершён | $(date '+%F %T %Z') | exit=${RC} | wall=$((END-START)) s ===${N}"
printf '{"label":"%s","session":"%s","model":"%s","start":"%s","end":"%s","wall_s":%d,"exit":%d}\n' \
  "$LABEL" "$SID" "$MODEL" "$(date -d @$START --iso-8601=seconds)" "$(date -d @$END --iso-8601=seconds)" $((END-START)) $RC >> "$RUN_DIR/turns.jsonl"
