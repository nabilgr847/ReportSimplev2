"""Rule-based safety layer. The LLM never decides high/low/normal or urgency: this code does."""
import re

# key -> aliases (regex, word-boundary matched) and fallback adult ranges (used only if the report has no range)
# order matters: ldl/hdl before "cholesterol"
TESTS = {
    "ldl": dict(names=["ldl"], rng={"any": (None, 100)}),
    "hdl": dict(names=["hdl"], rng={"any": (40, None), "female": (50, None)}),
    "triglycerides": dict(names=["triglyceride", "tg"], rng={"any": (None, 150)}),
    "total_chol": dict(names=["total cholesterol", "cholesterol"], rng={"any": (None, 200)}),
    "hemoglobin": dict(names=["hemoglobin", "haemoglobin", "hgb", "hb"],
                       rng={"any": (12.0, 17.0), "male": (13.0, 17.0), "female": (12.0, 15.5)}),
    "rbc": dict(names=["rbc", "red blood"], rng={"any": (4.2, 5.9)}),
    "wbc": dict(names=["wbc", "white blood", "tlc"], rng={"any": (4000, 11000)}),
    "platelet": dict(names=["platelet", "plt"], rng={"any": (150000, 410000)}),
    "hba1c": dict(names=["hba1c", "hbatc", "hbaic", "a1c", "hb a1c", "glycated", "glycosylated"], rng={"any": (4.0, 5.6)}),
    "glucose": dict(names=["fasting blood sugar", "fasting glucose", "fasting plasma", "fbs", "blood sugar", "glucose"],
                    rng={"any": (70, 100)}),
    "creatinine": dict(names=["creatinine"], rng={"any": (0.7, 1.3), "female": (0.6, 1.1)}),
    "alt": dict(names=["alt", "sgpt"], rng={"any": (None, 45)}),
    "ast": dict(names=["ast", "sgot"], rng={"any": (None, 40)}),
    "bilirubin": dict(names=["bilirubin"], rng={"any": (None, 1.2)}),
    "tsh": dict(names=["tsh"], rng={"any": (0.4, 4.0)}),
    "vitamin_d": dict(names=["vitamin d", "25-oh", "25 oh"], rng={"any": (30, 100)}),
    "b12": dict(names=["b12", "cobalamin"], rng={"any": (200, 900)}),
    "uric_acid": dict(names=["uric acid"], rng={"any": (None, 7.0), "female": (None, 6.0)}),
    "potassium": dict(names=["potassium"], rng={"any": (3.5, 5.1)}),
    "sodium": dict(names=["sodium"], rng={"any": (135, 145)}),
}

# Absolute "critical" limits (low, high). Beyond these -> urgent regardless of % deviation.
CRITICAL = {
    "hemoglobin": (7.0, None), "platelet": (20000, None), "glucose": (50, 400),
    "potassium": (3.0, 6.0), "sodium": (125, 155), "creatinine": (None, 3.0),
}


def canon(name):
    n = str(name or "").lower()
    for key, t in TESTS.items():
        for a in t["names"]:
            if re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", n):
                return key
    return None


def to_num(s):
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_range(s):
    s = str(s or "").replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s*[-–]\s*(\d+\.?\d*)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"[<≤]\s*(\d+\.?\d*)", s)
    if m:
        return None, float(m.group(1))
    m = re.search(r"[>≥]\s*(\d+\.?\d*)", s)
    if m:
        return float(m.group(1)), None
    return None


def fallback_range(key, sex="any"):
    t = TESTS.get(key)
    if not t:
        return None
    return t["rng"].get(sex) or t["rng"]["any"]


def status(v, lo, hi):
    if lo is not None and v < lo:
        return "low"
    if hi is not None and v > hi:
        return "high"
    return "normal"


def verify(findings, sex="any"):
    """Return (checked_findings, stats). Status is set ONLY by this code."""
    out, stats = [], {"verified": 0, "unchecked": 0, "ocr_suspect": 0}
    for f in findings:
        f = dict(f)
        f["key"] = canon(f.get("name"))
        f["check"] = ""
        v = to_num(f.get("value"))
        rng, src = parse_range(f.get("normal_range")), "report range"
        if rng is None:
            rng, src = fallback_range(f["key"], sex), "built-in table"
        if v is None or rng is None:
            f.update(status="unclear", deviation=0.0, check="Not auto-verified (no readable range)")
            stats["unchecked"] += 1
            out.append(f)
            continue
        lo, hi = rng
        s = status(v, lo, hi)
        f["status"] = s
        f["deviation"] = ((v - hi) / hi if s == "high" and hi else (lo - v) / lo if s == "low" and lo else 0.0)
        f["check"] = f"Checked by rules ({src})"
        stats["verified"] += 1
        # critical absolute limits
        c = CRITICAL.get(f["key"])
        f["critical"] = bool(c and ((c[0] is not None and v < c[0]) or (c[1] is not None and v > c[1])))
        # possible OCR decimal loss: 41 instead of 4.1
        decimals = any(b is not None and b != int(b) for b in (lo, hi))
        if s != "normal" and decimals and v == int(v) and status(v / 10, lo, hi) == "normal":
            f["check"] += f" | Possible OCR error: maybe {v / 10:g}. Check the original."
            stats["ocr_suspect"] += 1
        out.append(f)
    return out, stats


LEVELS = ["routine", "see_doctor_soon", "urgent"]


def urgency(findings):
    worst = "routine"
    for f in findings:
        if f.get("status") in ("high", "low"):
            worst = "see_doctor_soon"
            limit = 1.0 if f["status"] == "high" else 0.7
            if f.get("deviation", 0) >= limit or f.get("critical"):
                return "urgent"
    return worst


def max_level(*levels):
    return max(levels, key=LEVELS.index)
