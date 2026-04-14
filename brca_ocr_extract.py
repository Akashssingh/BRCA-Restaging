"""
brca_ocr_extract.py

Single-pass pipeline: PDF → Claude Vision → OCR text + structured BRCA features.

For each PDF:
  - Renders pages via PyMuPDF (high quality, no ImageMagick dependency)
  - Sends each page to Claude with a dual prompt: OCR + feature extraction
  - Merges features across pages
  - Attempts to derive anatomic + prognostic AJCC stage from extracted fields
  - Outputs: per-case JSON and a master CSV of all cases

Universal feature schema is designed around AJCC 8th Edition breast cancer
prognostic staging requirements: T, N, M, Grade, HER2, ER, PR.
"""

import os
import sys
import json
import base64
import time
import csv
import io
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import fitz  # PyMuPDF
import anthropic
# ── Config ────────────────────────────────────────────────────────────────────
GDC_DOWNLOADS_DIR  = "/home/a/akashsingh/BRCA-Restaging/gdc_downloads"
OUTPUT_DIR         = "/home/a/akashsingh/BRCA-Restaging/ocr_output"
# Subdirectories under OUTPUT_DIR
JSON_DIR           = OUTPUT_DIR + "/json"       # per-case full JSON (OCR + features)
TXT_DIR            = OUTPUT_DIR + "/txt"        # per-case OCR text only
FEATURES_DIR       = OUTPUT_DIR + "/features"   # per-case feature JSON only
MASTER_CSV         = OUTPUT_DIR + "/brca_features_master.csv"
# Cohort file — if set, only these folder IDs are processed
COHORT_FILE        = "/home/a/akashsingh/BRCA-Restaging/temp-python-scrpt/cohort_150_folder_ids.txt"
MODEL              = "claude-sonnet-4-5"
DPI                = 300
MAX_LONG_EDGE_PX   = 1568   # Claude's image size limit
SKIP_PROCESSED     = True   # skip if output JSON already exists
# ──────────────────────────────────────────────────────────────────────────────

# ── Universal BRCA feature schema ─────────────────────────────────────────────
# Fields are grouped by their role in AJCC staging.
# All fields default to None — only populated when explicitly found in the report.
EMPTY_FEATURES = {
    # -- Patient / report metadata --
    "patient_id":                   None,  # TCGA barcode or any ID on the report
    "report_date":                  None,
    "institution":                  None,

    # -- Specimen --
    "specimen_type":                None,  # mastectomy / lumpectomy / core biopsy / etc.
    "laterality":                   None,  # left / right / bilateral
    "tumor_site":                   None,  # anatomical location within breast

    # -- Histology --
    "histologic_type":              None,  # IDC / ILC / mucinous / metaplastic / etc.
    "DCIS_present":                 None,  # present / absent
    "DCIS_grade":                   None,  # low / intermediate / high
    "LCIS_present":                 None,
    "necrosis":                     None,  # present / absent
    "lymphovascular_invasion":      None,  # present / absent / not evaluated
    "perineural_invasion":          None,

    # -- T STAGE inputs --
    "tumor_size_cm":                None,  # largest dimension as float
    "tumor_size_raw":               None,  # exact text e.g. "2.3 x 1.8 x 1.5 cm"
    "tumor_focality":               None,  # unifocal / multifocal / multicentric
    "additional_tumor_foci_cm":     None,  # sizes of additional foci if multifocal
    "skin_involvement":             None,  # ulceration / satellite nodules / peau d'orange / edema / absent
    "nipple_involvement":           None,  # present / absent
    "chest_wall_involvement":       None,  # present / absent (excludes pectoralis-only)
    "pectoralis_involvement":       None,  # present / absent (does NOT upgrade to T4)
    "inflammatory_carcinoma":       None,  # present / absent  → T4d if present
    "pathologic_T":                 None,  # pT stage as written: pT1c, pT2, pT3, pT4b, etc.

    # -- N STAGE inputs --
    "lymph_nodes_examined":         None,  # total int
    "lymph_nodes_positive":         None,  # total positive int
    "axillary_ln_level":            None,  # I / II / III involved
    "axillary_ln_fixed_matted":     None,  # yes / no → N2a if yes
    "axillary_ln_micrometastasis":  None,  # yes / no (>0.2mm but ≤2mm) → N1mi
    "internal_mammary_positive":    None,  # yes / no
    "infraclavicular_positive":     None,  # yes / no → N3a
    "supraclavicular_positive":     None,  # yes / no → N3c
    "ln_extranodal_extension":      None,  # present / absent
    "sentinel_node_biopsy_done":    None,  # yes / no
    "pathologic_N":                 None,  # pN stage as written: pN0, pN1a, pN2a, etc.

    # -- M STAGE inputs --
    "distant_metastasis":           None,  # present / absent / not evaluated
    "metastasis_sites":             None,  # e.g. "liver, bone"
    "pathologic_M":                 None,  # pM0 / pM1 / cM0 / cM1

    # -- GRADE (required for prognostic stage) --
    "nottingham_grade":             None,  # 1 / 2 / 3  (= G1/G2/G3)
    "tubule_formation_score":       None,  # 1 / 2 / 3  (Nottingham component)
    "nuclear_pleomorphism_score":   None,  # 1 / 2 / 3
    "mitotic_count_score":          None,  # 1 / 2 / 3
    "mitotic_count_raw":            None,  # e.g. "5 per 10 HPF"

    # -- BIOMARKERS (required for prognostic stage) --
    "ER_status":                    None,  # positive / negative
    "ER_percent":                   None,  # float 0-100
    "ER_allred_score":              None,
    "PR_status":                    None,  # positive / negative
    "PR_percent":                   None,
    "PR_allred_score":              None,
    "HER2_status":                  None,  # positive / negative / equivocal
    "HER2_IHC_score":               None,  # 0 / 1+ / 2+ / 3+
    "HER2_ISH_ratio":               None,  # FISH/CISH copy number ratio
    "HER2_method":                  None,  # IHC / FISH / CISH / SISH
    "Ki67_percent":                 None,

    # -- Surgical margins --
    "margins":                      None,  # positive / negative / close
    "margin_distance_mm":           None,  # closest margin in mm
    "margin_location":              None,  # e.g. "posterior / deep"

    # -- Stage as written in report --
    "report_states_T":              None,  # pT as written
    "report_states_N":              None,  # pN as written
    "report_states_M":              None,
    "report_states_overall_stage":  None,  # e.g. "Stage IIA" if pathologist wrote it

    # -- Derived by this script (post-extraction) --
    "derived_anatomic_stage":       None,
    "derived_prognostic_stage":     None,
    "missing_for_anatomic_stage":   None,  # list of missing required fields
    "missing_for_prognostic_stage": None,
    "extraction_confidence":        None,  # high / medium / low
    "_m_defaulted":                 False, # True if pathologic_M was set to cM0 by default
}

# Fields essential to determine anatomic stage
ANATOMIC_REQUIRED = ["pathologic_T", "pathologic_N", "pathologic_M"]
# Fields essential to determine prognostic stage (on top of anatomic)
PROGNOSTIC_REQUIRED = ["nottingham_grade", "HER2_status", "ER_status", "PR_status"]

# ── AJCC 8th Edition anatomic stage lookup (simplified) ──────────────────────
# Maps (T_group, N_group, M_group) -> anatomic stage string
def derive_anatomic_stage(pT: str, pN: str, pM: str) -> Optional[str]:
    """
    Derive AJCC 8th Edition anatomic stage from pT/pN/pM strings.
    Returns stage string or None if inputs are insufficient/ambiguous.
    """
    if not pT or not pN or not pM:
        return None

    t = pT.upper().replace("PT", "").replace("P", "").strip()
    n = pN.upper().replace("PN", "").replace("P", "").strip()
    m = pM.upper().replace("PM", "").replace("P", "").replace("C", "").strip()

    if "1" in m or m == "M1":
        return "Stage IV"
    if m not in ("M0", "0", "M0(I+)", "I+"):
        return None  # can't determine

    # T grouping
    t_in = lambda *vals: any(t.startswith(v) or t == v for v in vals)
    n_in = lambda *vals: any(n.startswith(v) or n == v for v in vals)

    tis = t_in("IS", "TIS")
    t0  = t_in("0")
    t1  = t_in("1MI", "1A", "1B", "1C", "1") and not tis
    t2  = t_in("2")
    t3  = t_in("3")
    t4  = t_in("4")

    n0   = n_in("0")
    n1mi = n_in("1MI")
    n1   = n_in("1A", "1B", "1C") or (n_in("1") and not n1mi)
    n2   = n_in("2A", "2B") or n_in("2")
    n3   = n_in("3A", "3B", "3C") or n_in("3")

    if tis and n0:
        return "Stage 0"
    if t1 and n0:
        return "Stage IA"
    if (t0 or t1) and n1mi:
        return "Stage IB"
    if (t0 or t1) and n1:
        return "Stage IIA"
    if t2 and n0:
        return "Stage IIA"
    if t2 and n1:
        return "Stage IIB"
    if t3 and n0:
        return "Stage IIB"
    if (t0 or t1 or t2) and n2:
        return "Stage IIIA"
    if t3 and (n1 or n2):
        return "Stage IIIA"
    if t4 and (n0 or n1 or n2):
        return "Stage IIIB"
    if n3:
        return "Stage IIIC"

    return None


def check_missing(features: dict) -> tuple[list, list]:
    missing_anat = [f for f in ANATOMIC_REQUIRED if not features.get(f)]
    missing_prog = [f for f in PROGNOSTIC_REQUIRED if not features.get(f)]
    return missing_anat, missing_prog


# ── PDF rendering ─────────────────────────────────────────────────────────────
def pdf_to_b64_images(pdf_path: str, dpi: int = DPI) -> list[dict]:
    """Render each PDF page to a base64 PNG using PyMuPDF.
    Downscaling is done via PyMuPDF's Matrix to avoid PIL dependency issues."""
    doc = fitz.open(pdf_path)
    pages = []

    try:
        for i in range(len(doc)):
            # Calculate zoom so the long edge doesn't exceed MAX_LONG_EDGE_PX
            page = doc[i]
            rect = page.rect
            base_zoom = dpi / 72.0
            long_edge_at_dpi = max(rect.width, rect.height) * base_zoom
            if long_edge_at_dpi > MAX_LONG_EDGE_PX:
                zoom = base_zoom * (MAX_LONG_EDGE_PX / long_edge_at_dpi)
            else:
                zoom = base_zoom

            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            b64 = base64.standard_b64encode(pix.tobytes("png")).decode("utf-8")
            pages.append({"page": i + 1, "b64": b64,
                           "size": f"{pix.width}x{pix.height}"})
    finally:
        doc.close()

    return pages


# ── Claude API call (OCR + features in one pass) ──────────────────────────────
SYSTEM_PROMPT = (
    "You are an expert clinical pathologist specializing in breast cancer (BRCA). "
    "You will receive an image of a page from a surgical pathology report. "
    "Your job is to: (1) transcribe the full text of the page exactly as shown, "
    "and (2) extract structured clinical features relevant to AJCC breast cancer staging. "
    "Only report what is explicitly stated — never infer or guess. "
    "For fields not present on this page, use null."
)

FEATURE_KEYS = [k for k in EMPTY_FEATURES
                if not k.startswith("derived_") and not k.startswith("missing_")
                and k != "extraction_confidence"]

def build_extraction_prompt() -> str:
    keys_json = json.dumps({k: None for k in FEATURE_KEYS}, indent=2)
    return f"""Please provide two things:

1. FULL OCR TEXT: Transcribe every word on this page exactly as written. Preserve tables, 
   line breaks, and formatting. If a cell is empty, write "N/A". Do not truncate.

2. FEATURE JSON: Extract clinical features into this exact JSON structure. Use null for 
   any field not found on this page. For T/N/M stages, copy the exact notation used 
   (e.g. "pT2", "pN1a", "cM0"). For ER/PR/HER2, use "positive" or "negative" (lowercase).
   For grade, use the numeric Nottingham grade (1, 2, or 3).

{keys_json}

Return your answer in exactly this format:
===OCR_TEXT===
(full page text here)
===END_OCR===
===FEATURES_JSON===
(JSON object here)
===END_FEATURES==="""


def call_claude(client: anthropic.Anthropic, b64_image: str, page_num: int) -> dict:
    """Single Claude API call returning both OCR text and structured features.
    Retries up to 5 times with exponential backoff on 529/529 overloaded or 429 rate-limit errors."""
    MAX_RETRIES = 5
    BASE_DELAY  = 15  # seconds — start conservative for overloaded errors

    t0 = time.time()
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/png", "data": b64_image}},
                        {"type": "text", "text": build_extraction_prompt()}
                    ]
                }]
            )
            break  # success
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529) and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** (attempt - 1))  # 15, 30, 60, 120, 240s
                print(f"    Page {page_num}: API {e.status_code} (attempt {attempt}/{MAX_RETRIES}), "
                      f"retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise  # give up after MAX_RETRIES or for non-retryable errors

    elapsed = time.time() - t0
    raw = response.content[0].text

    # Parse OCR text
    ocr_text = ""
    ocr_match = re.search(r"===OCR_TEXT===\s*(.*?)\s*===END_OCR===", raw, re.DOTALL)
    if ocr_match:
        ocr_text = ocr_match.group(1).strip()

    # Parse features JSON
    features = {}
    json_match = re.search(r"===FEATURES_JSON===\s*(.*?)\s*===END_FEATURES===", raw, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```[a-z]*\n?", "", json_str)
            json_str = re.sub(r"\n?```$", "", json_str)
        try:
            features = json.loads(json_str)
        except json.JSONDecodeError:
            features = {"_parse_error": True, "_raw": json_str}
    else:
        features = {"_parse_error": True, "_raw": raw}

    print(f"    Page {page_num}: {elapsed:.1f}s | "
          f"in={response.usage.input_tokens} out={response.usage.output_tokens} tokens")
    return {"page": page_num, "ocr_text": ocr_text, "features": features, "elapsed": elapsed}


# ── Merge page features ───────────────────────────────────────────────────────
def merge_features(page_results: list[dict]) -> tuple[dict, str]:
    """
    Merge features across pages. Non-null values override null.
    For numeric counts, take the maximum (most complete report).
    Returns (merged_features_dict, full_ocr_text).
    """
    merged = dict(EMPTY_FEATURES)
    ocr_parts = []
    numeric_max = {"lymph_nodes_examined", "lymph_nodes_positive",
                   "lymph_nodes_positive", "tumor_size_cm"}

    for pr in sorted(page_results, key=lambda x: x["page"]):
        ocr_parts.append(f"{'='*35} PAGE {pr['page']} {'='*35}\n{pr['ocr_text']}")
        feats = pr.get("features", {})
        for key in FEATURE_KEYS:
            val = feats.get(key)
            if val is None or val == "null" or val == "":
                continue
            if key in numeric_max:
                try:
                    new_val = float(val)
                    old_val = float(merged[key]) if merged[key] is not None else -1
                    if new_val > old_val:
                        merged[key] = val
                except (ValueError, TypeError):
                    if merged[key] is None:
                        merged[key] = val
            else:
                if merged[key] is None:
                    merged[key] = val

    full_ocr = "\n\n".join(ocr_parts)
    return merged, full_ocr


# ── Post-processing: derive stages, flag missing fields ───────────────────────
# ── Roman numeral conversion ───────────────────────────────────────────────────
ROMAN_MAP = [
    (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
    (100,  'C'), (90,  'XC'), (50,  'L'), (40,  'XL'),
    (10,   'X'), (9,   'IX'), (5,   'V'), (4,   'IV'), (1, 'I')
]

def roman_to_int(s: str) -> Optional[int]:
    """Convert a Roman numeral string to int. Returns None if not a valid Roman numeral."""
    s = s.strip().upper()
    if not s:
        return None
    val = 0
    i = 0
    for num, rom in ROMAN_MAP:
        while s[i:i+len(rom)] == rom:
            val += num
            i += len(rom)
    # Valid only if we consumed the whole string and got a positive value
    return val if i == len(s) and val > 0 else None


def normalize_node_count(raw: str) -> Optional[int]:
    """
    Parse lymph node counts expressed as integers or Roman numerals.
    Handles formats: '9', 'IX', '9/15', 'IX/XV', '9 of 15', '4 nodes'.
    Returns integer count (first number = positive nodes).
    """
    if raw is None:
        return None
    raw = str(raw).strip()

    # Try plain integer first
    if raw.isdigit():
        return int(raw)

    # Pattern: number/number or roman/roman — take the first (positive count)
    match = re.match(r'^([IVXLCDM]+|\d+)\s*[/of]\s*([IVXLCDM]+|\d+)$', raw, re.IGNORECASE)
    if match:
        first = match.group(1)
        val = int(first) if first.isdigit() else roman_to_int(first)
        return val

    # Try pure Roman numeral
    rv = roman_to_int(raw)
    if rv is not None:
        return rv

    # Try extracting first integer from string like "4 nodes"
    m = re.match(r'^(\d+)', raw)
    if m:
        return int(m.group(1))

    return None


def post_process(features: dict) -> dict:
    # Copy T/N/M from report_states if pathologic_T/N/M not filled
    for field, fallback in [("pathologic_T", "report_states_T"),
                             ("pathologic_N", "report_states_N"),
                             ("pathologic_M", "report_states_M")]:
        if not features.get(field) and features.get(fallback):
            features[field] = features[fallback]

    # Default M to cM0 for surgical resection cases where M is not stated or is MX.
    # Rationale: pathology reports from surgery inherently reflect a non-metastatic
    # clinical context; M1 would have been explicitly noted.
    # MX ("cannot be assessed") is treated as cM0 for surgical specimens per AJCC 8th Ed.
    m_val = (features.get("pathologic_M") or "").upper().replace("P","").replace("C","").strip()
    if not features.get("pathologic_M") or m_val in ("MX", "X"):
        specimen = (features.get("specimen_type") or "").lower()
        surgical_keywords = ("resection", "mastectomy", "lumpectomy", "excision",
                              "biopsy", "dissection", "tissue")
        if any(kw in specimen for kw in surgical_keywords) or not specimen:
            features["pathologic_M"] = "cM0"
            features["_m_defaulted"] = True

    # Normalize lymph node counts from Roman numerals or mixed strings to integers
    for field in ("lymph_nodes_examined", "lymph_nodes_positive"):
        raw = features.get(field)
        if raw is not None and not str(raw).isdigit():
            normalized = normalize_node_count(str(raw))
            if normalized is not None:
                features[field] = normalized

    # Swap examined/positive if positive > examined (common Roman numeral parse order error)
    try:
        pos = int(features.get("lymph_nodes_positive") or -1)
        exam = int(features.get("lymph_nodes_examined") or -1)
        if pos > 0 and exam > 0 and pos > exam:
            features["lymph_nodes_positive"], features["lymph_nodes_examined"] = exam, pos
    except (ValueError, TypeError):
        pass

    # Derive anatomic stage
    features["derived_anatomic_stage"] = derive_anatomic_stage(
        features.get("pathologic_T") or "",
        features.get("pathologic_N") or "",
        features.get("pathologic_M") or ""
    )

    # Check missing fields
    missing_anat, missing_prog = check_missing(features)
    features["missing_for_anatomic_stage"] = missing_anat if missing_anat else []
    features["missing_for_prognostic_stage"] = missing_prog if missing_prog else []

    # Confidence score
    filled = sum(1 for k in ANATOMIC_REQUIRED + PROGNOSTIC_REQUIRED
                 if features.get(k))
    total = len(ANATOMIC_REQUIRED) + len(PROGNOSTIC_REQUIRED)
    if filled == total:
        features["extraction_confidence"] = "high"
    elif filled >= total // 2:
        features["extraction_confidence"] = "medium"
    else:
        features["extraction_confidence"] = "low"

    return features


# ── Process one PDF ───────────────────────────────────────────────────────────
def process_pdf(folder_id: str, client: anthropic.Anthropic,
                ml_class: str = None, ml_label: int = None) -> Optional[dict]:
    folder = Path(GDC_DOWNLOADS_DIR) / folder_id
    pdfs = list(folder.glob("*.PDF")) + list(folder.glob("*.pdf"))
    if not pdfs:
        print(f"  [SKIP] No PDF in {folder_id}")
        return None

    pdf_path = str(pdfs[0])
    pdf_name = pdfs[0].name

    # Organized output paths
    Path(JSON_DIR).mkdir(parents=True, exist_ok=True)
    Path(TXT_DIR).mkdir(parents=True, exist_ok=True)
    Path(FEATURES_DIR).mkdir(parents=True, exist_ok=True)

    output_json     = Path(JSON_DIR)     / f"{folder_id}.json"
    output_txt      = Path(TXT_DIR)      / f"{folder_id}.txt"
    output_features = Path(FEATURES_DIR) / f"{folder_id}_features.json"

    if SKIP_PROCESSED and output_json.exists():
        print(f"  [SKIP] Already processed: {folder_id}")
        return None

    print(f"\n  PDF: {pdf_name}")
    pages = pdf_to_b64_images(pdf_path)
    print(f"  Pages: {len(pages)}")

    page_results = []
    for page in pages:
        result = call_claude(client, page["b64"], page["page"])
        page_results.append(result)

    merged_features, full_ocr = merge_features(page_results)
    merged_features = post_process(merged_features)
    merged_features["_folder_id"] = folder_id
    merged_features["_pdf_name"]  = pdf_name
    merged_features["_pages"]     = len(pages)
    if ml_class:
        merged_features["_ml_class"] = ml_class
        merged_features["_ml_label"] = ml_label

    # Save full JSON (OCR + features)
    output = {"folder_id": folder_id, "pdf_name": pdf_name,
              "ml_class": ml_class, "ml_label": ml_label,
              "features": merged_features, "per_page": page_results}
    with open(output_json, "w") as f:
        json.dump(output, f, indent=2)

    # Save OCR text only
    with open(output_txt, "w") as f:
        f.write(full_ocr)

    # Save features-only JSON (lightweight, for ML pipeline)
    with open(output_features, "w") as f:
        json.dump(merged_features, f, indent=2)

    return merged_features


# ── CSV master output ─────────────────────────────────────────────────────────
CSV_COLUMNS = (
    ["_folder_id", "_pdf_name", "_pages", "_ml_class", "_ml_label",
     "extraction_confidence", "derived_anatomic_stage",
     "missing_for_anatomic_stage", "missing_for_prognostic_stage"] + FEATURE_KEYS
)

def append_to_csv(features: dict, csv_path: str):
    file_exists = Path(csv_path).exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(features)


# ── Main ──────────────────────────────────────────────────────────────────────
def load_cohort(cohort_file: str) -> dict:
    """Load cohort TSV -> dict of folder_id -> {ml_class, ml_label, tcga_barcode, stage}"""
    cohort = {}
    with open(cohort_file, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            cohort[row["folder_id"]] = {
                "ml_class":    row["ml_class"],
                "ml_label":    int(row["ml_label"]),
                "tcga_barcode": row["tcga_barcode"],
                "stage":       row["stage"],
            }
    return cohort


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    for d in [OUTPUT_DIR, JSON_DIR, TXT_DIR, FEATURES_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)

    client = anthropic.Anthropic(api_key=api_key)

    # Load cohort if cohort file exists, else fall back to all folders
    cohort_map = {}
    if COHORT_FILE and Path(COHORT_FILE).exists():
        cohort_tsv = COHORT_FILE.replace("_folder_ids.txt", "_selected.tsv")
        if Path(cohort_tsv).exists():
            cohort_map = load_cohort(cohort_tsv)
            folders = list(cohort_map.keys())
            print(f"Cohort mode: {len(folders)} folders from {cohort_tsv}")
        else:
            with open(COHORT_FILE) as f:
                folders = [l.strip() for l in f if l.strip()]
            print(f"Cohort mode: {len(folders)} folders from {COHORT_FILE}")
    else:
        folders = sorted([d.name for d in Path(GDC_DOWNLOADS_DIR).iterdir() if d.is_dir()])
        print(f"Full mode: {len(folders)} folders in {GDC_DOWNLOADS_DIR}")

    total = len(folders)
    inp = input(f"\nThis will process {total} PDFs using Claude API (costs money).\n"
                f"Type 'yes' to proceed, or a number to test only that many: ").strip().lower()
    if inp in ("no", "n", ""):
        print("Cancelled.")
        sys.exit(0)
    elif inp.isdigit():
        folders = folders[:int(inp)]
        print(f"Test mode: {len(folders)} PDF(s).")
    elif inp != "yes":
        print("Cancelled.")
        sys.exit(0)

    print(f"\nProcessing {len(folders)} folders\n"
          f"  JSON  : {JSON_DIR}\n"
          f"  TXT   : {TXT_DIR}\n"
          f"  Features: {FEATURES_DIR}\n"
          f"  CSV   : {MASTER_CSV}\n")

    successful, failed = 0, 0
    batch_start = time.time()

    def print_summary():
        elapsed = time.time() - batch_start
        print(f"\n{'='*60}")
        print(f"{'DONE' if failed == 0 else 'FINISHED'} — {successful} success, {failed} failed | {elapsed:.0f}s total")
        print(f"Master CSV : {MASTER_CSV}")
        print(f"JSON files : {JSON_DIR}/")
        print(f"TXT files  : {TXT_DIR}/")
        print(f"Features   : {FEATURES_DIR}/")
        if failed:
            print(f"\n  Re-run the script to retry the {failed} failed cases (SKIP_PROCESSED=True).")

    try:
        for idx, folder_id in enumerate(folders, 1):
            info = cohort_map.get(folder_id, {})
            ml_class = info.get("ml_class")
            ml_label = info.get("ml_label")
            print(f"[{idx}/{len(folders)}] {folder_id}  class={ml_class or '?'}")
            try:
                features = process_pdf(folder_id, client, ml_class=ml_class, ml_label=ml_label)
                if features:
                    append_to_csv(features, MASTER_CSV)
                    successful += 1
            except Exception as e:
                print(f"  [ERROR] {folder_id}: {e}")
                failed += 1
            # Small inter-case delay to avoid triggering 529 overloaded on rapid bursts
            if idx < len(folders):
                time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n\n[Interrupted by user at case {idx}/{len(folders)}]")
        print(f"Progress is saved. Re-run to continue from where it left off.")

    print_summary()


if __name__ == "__main__":
    main()
