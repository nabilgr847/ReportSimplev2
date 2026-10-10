"""Evaluation for the write-up: LLM-only status vs LLM + rule engine, on synthetic reports with known truth.
Run:  python eval.py   (needs FEATHERLESS_API_KEY). Writes eval_results.csv and prints a summary."""
import csv
import json
import os
import random
import re

from openai import OpenAI

from rules import verify

client = OpenAI(base_url="https://api.featherless.ai/v1", api_key=os.environ["FEATHERLESS_API_KEY"])
MODEL = os.environ.get("FEATHERLESS_MODEL", "Qwen/Qwen2.5-72B-Instruct")

TESTS = [("Hemoglobin", "g/dL", 13.0, 17.0, 1), ("WBC Count", "/cumm", 4000, 11000, 0), ("Fasting Blood Sugar", "mg/dL", 70, 100, 0),
         ("HbA1c", "%", 4.0, 5.6, 1), ("Total Cholesterol", "mg/dL", 0, 200, 0), ("LDL Cholesterol", "mg/dL", 0, 100, 0),
         ("Serum Creatinine", "mg/dL", 0.7, 1.3, 1), ("Vitamin D", "ng/mL", 30, 100, 0), ("Platelet Count", "/cumm", 150000, 410000, 0)]
random.seed(7)


def make_case():
    rows, truth = [], {}
    for name, unit, lo, hi, dec in random.sample(TESTS, 6):
        r = random.random()
        mid = (lo + hi) / 2 if lo else hi * 0.7
        if r < 0.35 and lo:
            v = lo * random.uniform(0.5, 0.97)
        elif r < 0.7:
            v = hi * random.uniform(1.03, 1.8)
        else:
            v = random.uniform(lo if lo else hi * 0.5, hi * 0.98)
        v = round(v, dec) if dec else int(v)
        rng = f"< {hi:g}" if not lo else f"{lo:g} - {hi:g}"
        truth[name] = "low" if lo and v < lo else "high" if v > hi else "normal"
        rows.append(f"{name} {v} {unit} {rng}")
    return "\n".join(rows), truth


def ask(system, user):
    r = client.chat.completions.create(model=MODEL, temperature=0.1, max_tokens=1200,
                                       messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return json.loads(re.search(r"\{.*\}", r.choices[0].message.content, re.S).group(0))


LLM_ONLY = 'For each test say if it is high, low or normal. Reply ONLY JSON: {"results": [{"name": "...", "status": "high|low|normal"}]}'
EXTRACT = 'Extract lab values. Reply ONLY JSON: {"findings": [{"name": "", "value": "", "unit": "", "normal_range": ""}]}. Copy numbers exactly. Do not judge.'


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


rows, tot, ok_llm, ok_rule = [], 0, 0, 0
for i in range(30):
    text, truth = make_case()
    try:
        llm = {norm(x["name"]): x["status"] for x in ask(LLM_ONLY, text)["results"]}
        checked, _ = verify(ask(EXTRACT, text)["findings"])
        rule = {norm(f["name"]): f["status"] for f in checked}
    except Exception as e:
        print("skip", i, e)
        continue
    for name, t in truth.items():
        tot += 1
        a, b = llm.get(norm(name)), rule.get(norm(name))
        ok_llm += a == t
        ok_rule += b == t
        rows.append([i, name, t, a, b])
with open("eval_results.csv", "w", newline="") as f:
    csv.writer(f).writerows([["case", "test", "truth", "llm_only", "llm_plus_rules"]] + rows)
print(f"Tests: {tot}\nLLM only accuracy:       {ok_llm / tot:.1%}\nLLM + rule engine:       {ok_rule / tot:.1%}")
