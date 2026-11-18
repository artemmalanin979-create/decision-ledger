#!/usr/bin/env python3
"""Render Grok CLI streaming-json to a readable terminal view. Raw JSON is saved separately by tee."""
import sys, json, textwrap
DIM='\033[2m'; C='\033[1;35m'; G='\033[32m'; Y='\033[1;33m'; N='\033[0m'
intext=False; inthought=False
def nl():
    global intext, inthought
    if intext or inthought: sys.stdout.write(N+'\n'); intext=inthought=False
for line in sys.stdin:
    try: e=json.loads(line)
    except Exception: print(line.rstrip()); continue
    t=e.get('type')
    if t=='text':
        if inthought: nl()
        if not intext: sys.stdout.write('\n'); intext=True
        sys.stdout.write(e.get('data',''))
    elif t=='thought':
        if intext: nl()
        if not inthought: sys.stdout.write(DIM+'💭 '); inthought=True
        sys.stdout.write(e.get('data','').replace('\n',' '))
    elif t=='tool_call':
        nl(); ri=e.get('rawInput') or {}
        arg=ri.get('command') or ri.get('path') or ri.get('file_path') or ri.get('url') or ri.get('query') or ri.get('pattern') or json.dumps(ri,ensure_ascii=False)
        arg=str(arg).replace('\n',' ⏎ ')
        print(f"{C}▶ {e.get('toolName')}{N} {arg[:300]}")
    elif t=='tool_call_update' and e.get('status') in ('completed','failed'):
        txt=''
        for c in e.get('content') or []:
            cc=c.get('content') if isinstance(c,dict) else None
            if isinstance(cc,dict) and cc.get('type')=='text': txt+=cc.get('text','')
        lines=[l for l in txt.splitlines() if l.strip()]
        col=G if e['status']=='completed' else Y
        for l in lines[:6]: print(f"{DIM}  │ {l[:200]}{N}")
        if len(lines)>6: print(f"{DIM}  │ … (+{len(lines)-6} строк){N}")
        print(f"{col}  └ {e['status']}{N}")
    elif t=='end':
        nl(); u=e.get('usage',{})
        print(f"\n{Y}■ end: stop={e.get('stopReason')} turns={e.get('num_turns')} model={list((e.get('modelUsage') or {}).keys())} "
              f"in={u.get('input_tokens')} cache_read={u.get('cache_read_input_tokens')} out={u.get('output_tokens')} reasoning={u.get('reasoning_tokens')} total={u.get('total_tokens')} cost_usd={e.get('total_cost_usd')}{N}")
    sys.stdout.flush()
nl()
