import io
import json
import os
import re

import pdfplumber
import pytesseract
import streamlit as st
from openai import OpenAI
from pdf2image import convert_from_bytes
from PIL import Image

import kb
import patterns
from rules import LEVELS, max_level, urgency, verify

client = OpenAI(base_url="https://api.featherless.ai/v1", api_key=os.environ["FEATHERLESS_API_KEY"])
MODEL = os.environ.get("FEATHERLESS_MODEL", "Qwen/Qwen2.5-72B-Instruct")

EXTRACT_PROMPT = """You extract lab values from medical report text (may contain OCR typos).
Reply ONLY with valid JSON (no markdown):
{"findings": [{"name": "test name", "value": "number as written", "unit": "unit", "normal_range": "reference range as written"}]}
Fix obvious typos in test NAMES (e.g. "HbAtc" -> "HbA1c"). Copy numbers exactly. Do not judge or explain."""

TRANSLATE_PROMPT = """Translate every string VALUE in this JSON into simple, natural Bangla.
Keep the JSON structure, keys and list lengths EXACTLY the same. Keep test names, numbers and units in English. Reply ONLY with JSON."""


def call_json(system, user, max_tokens=2500):
    resp = client.chat.completions.create(
        model=MODEL, temperature=0.1, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    if not getattr(resp, "choices", None):
        raise RuntimeError(f"No result from model '{MODEL}': {resp}")
    raw = resp.choices[0].message.content
    return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))


# ---------- reading files ----------
def extract_text(file):
    data = file.read()
    name = file.name.lower()
    if name.endswith(".txt"):
        return data.decode("utf-8", errors="ignore")
    if name.endswith(".pdf"):
        text = ""
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        if len(text.strip()) < 30:
            for img in convert_from_bytes(data, dpi=200):
                text += pytesseract.image_to_string(img) + "\n"
        return text
    return pytesseract.image_to_string(Image.open(io.BytesIO(data)))


# ---------- helpers ----------
def trend_text(history):
    if len(history) < 2:
        return ""
    first, last = history[0], history[-1]
    vals = " -> ".join(str(f.get("value")) for f in history)
    if last["status"] == "normal" and first["status"] != "normal":
        t = "back in the normal range"
    elif last["status"] == "unclear" or first["status"] == "unclear":
        return f"Over your reports: {vals}"
    elif last["deviation"] < first["deviation"] - 0.02:
        t = "moving closer to normal"
    elif last["deviation"] > first["deviation"] + 0.02:
        t = "moving further from normal"
    else:
        t = "about the same"
    return f"Over your reports: {vals} ({t})"


def uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def build(reports, symptoms, emergency):
    """Everything below is deterministic. Returns (fixed, text): fixed = never translated, text = translatable."""
    latest, history, stats = {}, {}, {"verified": 0, "unchecked": 0, "ocr_suspect": 0}
    for checked, s in reports:
        for k in stats:
            stats[k] += s[k]
        for f in checked:
            key = f.get("key") or str(f.get("name")).lower()
            latest[key] = f
            history.setdefault(key, []).append(f)
    allf = list(latest.values())
    abnormal = [f for f in allf if f["status"] in ("high", "low")]
    pats = patterns.detect(latest)

    level = urgency(allf)
    if emergency:
        level = "urgent"
    infos = [(f, kb.info(f.get("key"), f["status"])) for f in abnormal]

    high = [f["name"] for f in abnormal if f["status"] == "high"]
    low = [f["name"] for f in abnormal if f["status"] == "low"]
    normal = [f["name"] for f in allf if f["status"] == "normal"]
    parts = []
    if high:
        parts.append("High: " + ", ".join(high) + ".")
    if low:
        parts.append("Low: " + ", ".join(low) + ".")
    if normal:
        parts.append("Normal: " + ", ".join(normal) + ".")
    if not abnormal:
        parts.append("No out-of-range values were found among the tests that could be checked.")
    summary = " ".join(parts)

    when = {
        "routine": "Show this report at your next regular check-up.",
        "see_doctor_soon": "Book a doctor visit within the next few days to two weeks and take this report.",
        "urgent": "Contact a doctor within 24 hours. If you feel very unwell, go to an emergency department now.",
    }[level]
    if emergency:
        when = "You selected a warning symptom. Please go to the nearest emergency department or call emergency services now. Do not wait."

    doctors = uniq([i["doctor"] for _, i in infos] + [p["doctor"] for p in pats])
    tests = uniq([t for _, i in infos for t in i["tests"]] + [t for p in pats for t in p["tests"]])
    home = uniq([h for _, i in infos for h in i["home"]])
    meds = uniq([i["medicine_info"] for _, i in infos])
    flags = uniq([r for _, i in infos for r in i["red_flags"]] +
                 ["Chest pain", "Severe breathing difficulty", "Fainting or confusion"])
    questions = [f"What could be causing my {f['status']} {f['name']}?" for f in abnormal[:6]]
    if tests:
        questions.append("Which of these extra tests do I need, and how soon?")
    if pats:
        questions.append("Do these results fit together in a pattern, and what should I do about it?")
    questions.append("Which medicines or supplements, if any, do I need, and which should I avoid?")
    if symptoms:
        questions.append("I also have: " + ", ".join(symptoms) + ". Could they be linked to these results?")

    findings_text = []
    findings_fixed = []
    for f in allf:
        i = kb.info(f.get("key"), f["status"]) if f["status"] in ("high", "low") else None
        findings_text.append({
            "meaning": (i["meaning"] if i else
                        "Inside the normal range." if f["status"] == "normal" else "Could not be checked automatically."),
            "causes": "; ".join(i["causes"]) if i and i["causes"] else "",
            "trend": trend_text(history.get(f.get("key") or str(f.get("name")).lower(), [])),
        })
        findings_fixed.append(dict(
            icon={"normal": "✅", "high": "⬆️", "low": "⬇️", "unclear": "❔"}[f["status"]],
            line=f"**{f.get('name')}**: {f.get('value')} {f.get('unit', '')} (normal: {f.get('normal_range') or 'built-in range'})",
            check=f["check"] if ("OCR" in f["check"] or f["status"] == "unclear") else ""))

    text = dict(
        summary=summary, when=when,
        findings=findings_text,
        patterns=[{"title": p["title"], "text": p["text"]} for p in pats],
        doctors=doctors, tests=tests, home=home, meds=meds, flags=flags, questions=questions,
    )
    fixed = dict(level=level, stats=stats, findings=findings_fixed, symptoms=symptoms)
    return fixed, text


def digits(x):
    return sorted(re.findall(r"\d+", json.dumps(x, ensure_ascii=False)))


def same_shape(a, b):
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(same_shape(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(same_shape(x, y) for x, y in zip(a, b))
    return isinstance(b, str)


def translate(text):
    """LLM only translates fixed text. Fall back to English if structure or numbers change."""
    try:
        t = call_json(TRANSLATE_PROMPT, json.dumps(text, ensure_ascii=False), max_tokens=4000)
        if same_shape(text, t) and digits(text) == digits(t):
            return t, True
    except Exception:
        pass
    return text, False


# ---------- UI ----------
st.set_page_config(page_title="ReportSimple", page_icon="🩺")
st.title("🩺 ReportSimple")
st.caption("Understand your medical reports in plain language, with safe next steps.")

lang = st.selectbox("Language", ["English", "Bangla"])
sex = st.selectbox("Sex (only used if your report has no range)", ["Not specified", "Male", "Female"])
uploads = st.file_uploader("Upload one or more reports (oldest first)", type=["pdf", "png", "jpg", "jpeg", "txt"],
                           accept_multiple_files=True)
pasted = st.text_area("...or paste report text", height=150)

EMERGENCY = ["Chest pain or pressure", "Severe difficulty breathing", "Fainting or confusion",
             "Vomiting blood or black stool", "Sudden weakness on one side or trouble speaking"]
OTHER = ["Fever", "Tiredness or weakness", "Dizziness", "Very thirsty / urinating a lot",
         "Unexplained weight loss", "Swelling of legs or face", "Yellow eyes or skin", "Persistent vomiting"]
with st.expander("Do you have any symptoms? (optional)"):
    sym_em = st.multiselect("Warning symptoms", EMERGENCY)
    sym_ot = st.multiselect("Other symptoms", OTHER)

texts = []
for u in uploads or []:
    with st.spinner(f"Reading {u.name}..."):
        t = extract_text(u)
    texts.append((u.name, t))
    with st.expander(f"Text read from {u.name} (check if correct)"):
        st.text(t)
if pasted.strip():
    texts.append(("pasted text", pasted))

if st.button("Explain", type="primary") and texts:
    sexv = {"Male": "male", "Female": "female"}.get(sex, "any")
    reports = []
    with st.spinner("Reading values and checking them with rules..."):
        try:
            for name, t in texts:
                ex = call_json(EXTRACT_PROMPT, t, max_tokens=1800)
                reports.append(verify(ex.get("findings", []), sexv))
            fixed, text = build(reports, sym_em + sym_ot, bool(sym_em))
            if lang == "Bangla":
                text, ok = translate(text)
                if not ok:
                    st.info("Bangla translation could not be verified, so English is shown.")
        except Exception as e:
            st.error(f"Something went wrong: {e}")
            st.stop()

    badge = {"routine": "🟢 Routine", "see_doctor_soon": "🟡 See a doctor soon", "urgent": "🔴 Urgent: see a doctor promptly"}
    st.subheader(badge[fixed["level"]])
    st.write(text["when"])
    st.write(text["summary"])

    if text["patterns"]:
        st.subheader("Patterns to discuss with your doctor")
        st.caption("Not a diagnosis. These are possible patterns only a doctor can confirm.")
        for p in text["patterns"]:
            st.markdown(f"**{p['title']}**\n\n{p['text']}")

    st.subheader("Findings")
    s = fixed["stats"]
    st.caption(f"Status set by rules for {s['verified']} tests; {s['unchecked']} could not be auto-verified")
    for fx, tx in zip(fixed["findings"], text["findings"]):
        st.markdown(f"{fx['icon']} {fx['line']}. {tx['meaning']}")
        if tx["causes"]:
            st.caption("Common causes: " + tx["causes"])
        if tx["trend"]:
            st.caption("📈 " + tx["trend"])
        if fx["check"]:
            st.caption(fx["check"])

    if text["tests"]:
        st.subheader("Extra tests your doctor may suggest")
        for t in text["tests"]:
            st.markdown(f"- {t}")
    if text["home"]:
        st.subheader("General self-care (not treatment)")
        for h in text["home"]:
            st.markdown(f"- {h}")
    if text["meds"]:
        st.subheader("About medicines")
        for m in text["meds"]:
            st.markdown(f"- {m}")
        st.caption("This app does not prescribe or give doses. Never start, stop or change a medicine without a doctor.")
    if text["flags"]:
        st.subheader("Go to a doctor or emergency right away if")
        for r in text["flags"]:
            st.markdown(f"- {r}")

    st.subheader("Ask your doctor")
    for q in text["questions"]:
        st.markdown(f"- {q}")
    if text["doctors"]:
        st.subheader("Who to see")
        st.write(", ".join(text["doctors"]))

st.divider()
st.error("Always consult a qualified doctor. This app explains reports; it does not diagnose, prescribe, or replace medical care.")
