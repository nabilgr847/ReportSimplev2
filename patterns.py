"""Rule-based pattern detection across all reports. Not a diagnosis: points to what to discuss with a doctor."""


def _v(latest, key):
    f = latest.get(key)
    return None if not f else f


def _st(latest, key):
    f = latest.get(key)
    return f.get("status") if f else None


def _num(latest, key):
    from rules import to_num
    f = latest.get(key)
    return to_num(f.get("value")) if f else None


def detect(latest):
    """latest: key -> verified finding. Returns list of dict(title, text, doctor, tests)."""
    out = []
    g, a1c = _num(latest, "glucose"), _num(latest, "hba1c")

    # blood sugar (ADA-style cut-offs, fixed numbers)
    if (g is not None and g >= 126) or (a1c is not None and a1c >= 6.5):
        out.append(dict(title="Blood sugar in the diabetes range",
                        text="Your sugar values are in the range where doctors usually check for diabetes. Only a doctor can confirm it, usually with a repeat test.",
                        doctor="Endocrinologist / physician", tests=["Repeat fasting glucose", "OGTT", "Urine ketones"]))
    elif (g is not None and 100 <= g < 126) or (a1c is not None and 5.7 <= a1c < 6.5):
        out.append(dict(title="Blood sugar above normal (prediabetes range)",
                        text="Values are above normal but below the diabetes range. Diet and activity changes often help. Ask your doctor about a recheck.",
                        doctor="General physician", tests=["HbA1c", "Fasting glucose in 3 months"]))

    # anemia
    hb_low = _st(latest, "hemoglobin") == "low"
    rbc_low = _st(latest, "rbc") == "low"
    if hb_low and rbc_low:
        out.append(dict(title="Pattern fits with anemia",
                        text="Low hemoglobin and low red blood cells together fit with anemia. The cause (iron, B12, blood loss, other) needs to be found.",
                        doctor="General physician / hematologist", tests=["Ferritin, iron studies", "B12, folate", "Blood film"]))
    elif hb_low:
        out.append(dict(title="Low hemoglobin (possible anemia)",
                        text="Low hemoglobin can mean anemia. A doctor should look for the cause.",
                        doctor="General physician", tests=["Ferritin, iron studies", "B12, folate"]))
    if hb_low and _st(latest, "b12") == "low":
        out.append(dict(title="Low hemoglobin with low B12",
                        text="Low B12 can cause anemia. Ask your doctor whether this is the cause.",
                        doctor="General physician", tests=["MCV", "Folate"]))

    # lipids + sugar
    lipid_hi = any(_st(latest, k) == "high" for k in ("total_chol", "ldl", "triglycerides"))
    sugar_hi = _st(latest, "glucose") == "high" or _st(latest, "hba1c") == "high"
    if lipid_hi and sugar_hi:
        out.append(dict(title="High sugar and high blood fats together",
                        text="High sugar together with high cholesterol/fats raises long-term heart and vessel risk. Your doctor should look at your overall risk (blood pressure, weight, smoking, family history).",
                        doctor="General physician / cardiologist", tests=["Blood pressure", "ECG (doctor advises)", "Kidney tests"]))
    elif lipid_hi:
        out.append(dict(title="High blood fats (cholesterol)",
                        text="Raised cholesterol or triglycerides raise long-term heart risk. Lifestyle changes help; your doctor decides if more is needed.",
                        doctor="General physician", tests=["Full lipid profile", "TSH"]))

    if _st(latest, "wbc") == "high":
        out.append(dict(title="Raised white blood cells",
                        text="This often means infection or inflammation. If you have fever or pain, see a doctor.",
                        doctor="General physician", tests=["Differential count", "CRP/ESR", "Urine routine exam"]))
    if _st(latest, "creatinine") == "high":
        out.append(dict(title="Possible kidney strain",
                        text="High creatinine can mean the kidneys need a check, or dehydration. A repeat test and urine test help decide.",
                        doctor="Nephrologist / physician", tests=["eGFR", "Urine routine exam", "Urine ACR"]))
    if any(_st(latest, k) == "high" for k in ("alt", "ast", "bilirubin")):
        out.append(dict(title="Liver tests raised",
                        text="Raised liver tests can have many causes (fatty liver, hepatitis, medicines, alcohol). A doctor should review.",
                        doctor="Physician / hepatologist", tests=["Liver profile", "Hepatitis B/C", "Liver ultrasound"]))
    if _st(latest, "tsh") in ("high", "low"):
        out.append(dict(title="Thyroid test outside range",
                        text="TSH is outside the range. Free T4 helps tell if the thyroid is under- or overactive.",
                        doctor="Physician / endocrinologist", tests=["Free T4", "Anti-TPO"]))
    if _st(latest, "vitamin_d") == "low":
        out.append(dict(title="Low vitamin D",
                        text="Common and usually manageable. Ask your doctor about sunlight, food and supplement advice.",
                        doctor="General physician", tests=["Calcium", "Phosphate"]))
    return out
