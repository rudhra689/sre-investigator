from app.db import init_db
from app.detective import investigate
from app.simulator import SCENARIOS, run_scenario

init_db()
passed = 0
for name in SCENARIOS:
    info = run_scenario(name)
    got = investigate()["root_cause"]["service"]
    ok = got == info["expected_root"]
    passed += ok
    print(f"{name:12} expected={info['expected_root']:9} got={got:9} {'PASS' if ok else 'FAIL'}")
print(f"{passed}/{len(SCENARIOS)} correct")