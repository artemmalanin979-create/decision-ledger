import json,sys
for l in open(sys.argv[1]):
    try:e=json.loads(l)
    except:continue
    if e.get('type')=='tool_call':
        s=json.dumps(e,ensure_ascii=False)
        print(s[:400])
    if e.get('type')=='end':
        print('END',json.dumps({k:e.get(k) for k in ('total_cost_usd','usage')},ensure_ascii=False)[:500])
