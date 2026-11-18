#!/usr/bin/env bash
export RUN_DIR=/workspace/hack/live/clean2/run REPO=/workspace/hack/live/clean2/decision-ledger MODEL=grok-4.7
/workspace/hack/live/clean2/harness/preamble.sh
/workspace/hack/live/clean2/harness/turn.sh chat1-1A /workspace/hack/live/clean2/harness/p1a.txt 7664b298-c6e8-4b27-b744-35c0d7de9894 new
