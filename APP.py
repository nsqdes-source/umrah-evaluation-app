import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Tuple

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    from pdf_generator import generate_pdf_report
except Exception:
    generate_pdf_report = None

# =========================================================
# 0) إعدادات المنهجية
# المصدر: منهجية تصنيف شركات العمرة 1448هـ - يوليو 2026
# =========================================================
APP_VERSION = "2.0 - Methodology aligned"
DB_PATH = "umrah_evaluations.db"

# أوزان مؤشرات شركات العمرة كما وردت في بطاقات المؤشرات (مجموعها 100%).
WEIGHTS = {
    "luxury_packages": 7.0,
    "medium_packages": 5.0,
    "economy_packages": 3.0,
    "satisfaction": 10.0,
    "service_quality": 5.0,
    "complaints_on_time": 10.0,
    "enrichment": 10.0,
    "compliance": 10.0,
    "entry_matching": 10.0,
    "arrival_boarding": 5.0,
    "intercity_boarding": 5.0,
    "departure_boarding": 5.0,
    "exit_matching": 10.0,
    "housing_confirmation": 5.0,
}

PACKAGE_TARGETS = {
    "luxury": 8.0,
    "medium": 15.0,
    "economy": 77.0,
}

# وردت قواعد هامش التاريخ صراحة في بطاقات الدخول والخروج.
DATE_TOLERANCE_DAYS_LAND_SEA = 3
DATE_TOLERANCE_HOURS_AIR = 12

# المنهجية المرفقة لا تحدد حدود الفئات A/B/C؛ لذلك أبقينا نطاقات التطبيق
# الموجودة في النسخة السابقة كإعدادات قابلة للتعديل، وليست ادعاءً بأنها واردة في PDF.
TIER_THRESHOLDS = [
    (90.0, "الفئة الماسية (Class A)"),
    (75.0, "الفئة الذهبية (Class B)"),
    (60.0, "الفئة الفضية (Class C)"),
    (0.0, "غير معتمد / يحتاج تحسين"),
]

st.set_page_config(
    page_title="نظام إدارة وتصنيف شركات العمرة 1448هـ",
    page_icon="🕋",
    layout="wide",
)


# =========================================================
# 1) أدوات عامة وطبقة التحقق
# =========================================================
def safe_number(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        value = float(value)
        if value != value:  # NaN
            return default
        return value
    except (TypeError, ValueError):
        return default


def bounded_ratio(numerator, denominator) -> Tuple[float, bool]:
    """يعيد نسبة بين 0 و1، وحالة توفر المقام."""
    numerator = safe_number(numerator)
    denominator = safe_number(denominator)
    if denominator <= 0:
        return 0.0, False
    return max(0.0, min(1.0, numerator / denominator)), True


def validate_data(data: Dict) -> List[str]:
    """تحقق منطقي قبل احتساب النتيجة؛ يمنع النسب غير الممكنة."""
    errors = []

    pairs = [
        ("luxury_pilgrims", "total_entry_pilgrims", "المعتمرين في الباقات الفاخرة", "إجمالي دخول المعتمرين"),
        ("medium_pilgrims", "total_entry_pilgrims", "المعتمرين في الباقات المتوسطة", "إجمالي دخول المعتمرين"),
        ("economy_pilgrims", "total_entry_pilgrims", "المعتمرين في الباقات الاقتصادية", "إجمالي دخول المعتمرين"),
        ("closed_complaints_on_time", "total_complaints", "البلاغات المغلقة ضمن المدة", "إجمالي البلاغات"),
        ("enrichment_beneficiaries", "total_departing_pilgrims", "المستفيدين من الخدمات الإثرائية", "إجمالي المغادرين"),
        ("unaffected_pilgrims", "total_visited_pilgrims", "المعتمرين غير المتأثرين بالمخالفات", "إجمالي المعتمرين في الزيارات"),
        ("matched_entry_records", "total_entry_records", "سجلات القدوم المتطابقة", "إجمالي سجلات القدوم"),
        ("arrival_boarding_orders", "total_entry_pilgrims", "أوامر إركاب الوصول", "دخول المعتمرين"),
        ("intercity_boarding_orders", "total_departing_pilgrims", "أوامر إركاب بين المدن", "المغادرين"),
        ("departure_boarding_orders", "total_departing_pilgrims", "أوامر إركاب المغادرة", "المغادرين"),
        ("matched_exit_records", "total_exit_records", "سجلات المغادرة المتطابقة", "إجمالي سجلات المغادرة"),
        ("confirmed_housing_pilgrims", "total_housing_start_pilgrims", "المعتمرين المؤكد سكنهم", "المعتمرين التي بدأت برامجهم"),
    ]

    for num_key, den_key, num_label, den_label in pairs:
        n = safe_number(data.get(num_key))
        d = safe_number(data.get(den_key))
        if n < 0 or d < 0:
            errors.append(f"لا يمكن أن تكون {num_label} أو {den_label} سالبة.")
        if n > d and d >= 0:
            errors.append(f"{num_label} لا يمكن أن تتجاوز {den_label}.")

    # مجموع فئات الباقات يجب أن يساوي إجمالي الدخول، لأن الباقات تمثل توزيع الدخول.
    total_entry = safe_number(data.get("total_entry_pilgrims"))
    package_sum = sum(safe_number(data.get(k)) for k in ["luxury_pilgrims", "medium_pilgrims", "economy_pilgrims"])
    if total_entry > 0 and abs(package_sum - total_entry) > 0.000001:
        errors.append("إجمالي دخول المعتمرين يجب أن يساوي مجموع الفاخر + المتوسط + الاقتصادي.")

    # المدخلات المئوية يجب أن تبقى بين 0 و100.
    for key, label in [
        ("satisfaction_score_pct", "رضا المعتمرين"),
        ("service_quality_pct", "جودة أداء الخدمة"),
    ]:
        value = safe_number(data.get(key))
        if not 0 <= value <= 100:
            errors.append(f"قيمة {label} يجب أن تكون بين 0 و100.")

    return errors


# =========================================================
# 2) قاعدة البيانات
# =========================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_date TEXT,
            company_name TEXT,
            final_score REAL,
            tier TEXT,
            score_packages REAL,
            score_exp REAL,
            score_prog REAL,
            incentives REAL,
            penalties REAL,
            raw_json TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_evaluation(company_name: str, results: Dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO evaluations
        (eval_date, company_name, final_score, tier, score_packages,
         score_exp, score_prog, incentives, penalties, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            company_name.strip(),
            results["final_score"],
            results["tier"],
            results["score_packages"],
            results["score_exp"],
            results["score_prog"],
            results["total_incentives"],
            results["penalties"],
            json.dumps(results["raw_data"], ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()


init_db()


# =========================================================
# 3) محرك المؤشرات — مطابق لصيغ بطاقات شركات العمرة
# =========================================================
def score_percentage(value_pct: float, weight: float) -> float:
    """تحويل نتيجة مؤشر من 0-100% إلى نقاط الوزن."""
    value_pct = max(0.0, min(100.0, safe_number(value_pct)))
    return (value_pct / 100.0) * weight


def calculate_umrah_company_score(data: Dict) -> Dict:
    validation_errors = validate_data(data)
    if validation_errors:
        raise ValueError("\n".join(validation_errors))

    # -------------------------
    # أ) تنوع باقات الخدمات (15%)
    # الصيغة الرسمية: عدد الدخول ضمن الفئة ÷ إجمالي دخول الشهر × 100.
    # النسب 8%/15%/77% تعامل هنا كأهداف أداء/مرجع، وليس كبديل للصيغة الرسمية.
    # -------------------------
    total_entry = safe_number(data.get("total_entry_pilgrims"))
    lux_ratio, lux_available = bounded_ratio(data.get("luxury_pilgrims"), total_entry)
    mid_ratio, mid_available = bounded_ratio(data.get("medium_pilgrims"), total_entry)
    eco_ratio, eco_available = bounded_ratio(data.get("economy_pilgrims"), total_entry)

    lux_pct = lux_ratio * 100
    mid_pct = mid_ratio * 100
    eco_pct = eco_ratio * 100

    score_luxury = score_percentage(lux_pct, WEIGHTS["luxury_packages"])
    score_medium = score_percentage(mid_pct, WEIGHTS["medium_packages"])
    score_economy = score_percentage(eco_pct, WEIGHTS["economy_packages"])
    score_packages = score_luxury + score_medium + score_economy

    # تحقيق الهدف: للاستخدام الإداري فقط، دون تغيير الصيغة الرسمية.
    package_target_achievement = {
        "luxury": None if not lux_available else min(100.0, lux_pct / PACKAGE_TARGETS["luxury"] * 100.0),
        "medium": None if not mid_available else min(100.0, mid_pct / PACKAGE_TARGETS["medium"] * 100.0),
        "economy": None if not eco_available else min(100.0, eco_pct / PACKAGE_TARGETS["economy"] * 100.0),
    }

    # -------------------------
    # ب) تجربة المعتمر وجودة الخدمة (45%)
    # -------------------------
    satisfaction_pct = safe_number(data.get("satisfaction_score_pct"))
    quality_pct = safe_number(data.get("service_quality_pct"))
    score_satisfaction = score_percentage(satisfaction_pct, WEIGHTS["satisfaction"])
    score_quality = score_percentage(quality_pct, WEIGHTS["service_quality"])

    total_complaints = safe_number(data.get("total_complaints"))
    closed_on_time = safe_number(data.get("closed_complaints_on_time"))
    complaints_ratio, complaints_available = bounded_ratio(closed_on_time, total_complaints)
    complaints_pct = complaints_ratio * 100 if complaints_available else None
    score_complaints = score_percentage(complaints_pct or 0, WEIGHTS["complaints_on_time"])

    total_departing = safe_number(data.get("total_departing_pilgrims"))
    enrichment = safe_number(data.get("enrichment_beneficiaries"))
    enrichment_ratio, enrichment_available = bounded_ratio(enrichment, total_departing)
    enrichment_pct = enrichment_ratio * 100 if enrichment_available else None
    score_enrichment = score_percentage(enrichment_pct or 0, WEIGHTS["enrichment"])

    total_visited = safe_number(data.get("total_visited_pilgrims"))
    unaffected = safe_number(data.get("unaffected_pilgrims"))
    compliance_ratio, compliance_available = bounded_ratio(unaffected, total_visited)
    compliance_pct = compliance_ratio * 100 if compliance_available else None
    score_compliance = score_percentage(compliance_pct or 0, WEIGHTS["compliance"])

    score_exp = score_satisfaction + score_quality + score_complaints + score_enrichment + score_compliance

    # -------------------------
    # ج) الالتزام بالبرنامج (40%)
    # -------------------------
    total_entry_records = safe_number(data.get("total_entry_records"))
    matched_entry = safe_number(data.get("matched_entry_records"))
    entry_ratio, entry_available = bounded_ratio(matched_entry, total_entry_records)
    entry_pct = entry_ratio * 100 if entry_available else None
    score_entry = score_percentage(entry_pct or 0, WEIGHTS["entry_matching"])

    arrival_orders = safe_number(data.get("arrival_boarding_orders"))
    arrival_ratio, arrival_available = bounded_ratio(arrival_orders, total_entry)
    arrival_pct = arrival_ratio * 100 if arrival_available else None
    score_arrival = score_percentage(arrival_pct or 0, WEIGHTS["arrival_boarding"])

    intercity_orders = safe_number(data.get("intercity_boarding_orders"))
    intercity_ratio, intercity_available = bounded_ratio(intercity_orders, total_departing)
    intercity_pct = intercity_ratio * 100 if intercity_available else None
    score_intercity = score_percentage(intercity_pct or 0, WEIGHTS["intercity_boarding"])

    departure_orders = safe_number(data.get("departure_boarding_orders"))
    departure_ratio, departure_available = bounded_ratio(departure_orders, total_departing)
    departure_pct = departure_ratio * 100 if departure_available else None
    score_departure = score_percentage(departure_pct or 0, WEIGHTS["departure_boarding"])

    total_exit_records = safe_number(data.get("total_exit_records"))
    matched_exit = safe_number(data.get("matched_exit_records"))
    exit_ratio, exit_available = bounded_ratio(matched_exit, total_exit_records)
    exit_pct = exit_ratio * 100 if exit_available else None
    score_exit = score_percentage(exit_pct or 0, WEIGHTS["exit_matching"])

    total_housing_pilgrims = safe_number(data.get("total_housing_start_pilgrims"))
    confirmed_housing = safe_number(data.get("confirmed_housing_pilgrims"))
    housing_ratio, housing_available = bounded_ratio(confirmed_housing, total_housing_pilgrims)
    housing_pct = housing_ratio * 100 if housing_available else None
    score_housing = score_percentage(housing_pct or 0, WEIGHTS["housing_confirmation"])

    score_prog = score_entry + score_arrival + score_intercity + score_departure + score_exit + score_housing

    # النتيجة الأساسية.
    base_score = score_packages + score_exp + score_prog

    # -------------------------
    # د) المحفزات — مطابق للصفحات 32-34
    # -------------------------
    economy_gifts = safe_number(data.get("economy_gifts"))
    medium_gifts = safe_number(data.get("medium_gifts"))
    luxury_gifts = safe_number(data.get("luxury_gifts"))
    gift_points = economy_gifts * 1 + medium_gifts * 4 + luxury_gifts * 20
    gift_incentive = min(5.0, (gift_points / 8000.0) * 5.0)

    umrah_plus_beneficiaries = safe_number(data.get("umrah_plus_beneficiaries"))
    umrah_plus_points = umrah_plus_beneficiaries * 2
    umrah_plus_incentive = min(10.0, (umrah_plus_points / 2000.0) * 10.0)

    award_incentive = 5.0 if bool(data.get("has_ministry_award", False)) else 0.0
    total_incentives = gift_incentive + umrah_plus_incentive + award_incentive

    # -------------------------
    # هـ) الخصم للمخالفة الجسيمة — 5% مباشرة في شهر الرصد
    # -------------------------
    severe_violation_penalty = 5.0 if bool(data.get("has_severe_violation", False)) else 0.0

    score_before_cap = base_score + total_incentives - severe_violation_penalty
    final_score = max(0.0, min(100.0, score_before_cap))

    tier = next(label for threshold, label in TIER_THRESHOLDS if final_score >= threshold)

    return {
        "final_score": round(final_score, 2),
        "score_before_cap": round(score_before_cap, 2),
        "base_score": round(base_score, 2),
        "score_packages": round(score_packages, 2),
        "score_exp": round(score_exp, 2),
        "score_prog": round(score_prog, 2),
        "package_indicators": {
            "luxury_pct": round(lux_pct, 2),
            "medium_pct": round(mid_pct, 2),
            "economy_pct": round(eco_pct, 2),
            "luxury_score": round(score_luxury, 2),
            "medium_score": round(score_medium, 2),
            "economy_score": round(score_economy, 2),
            "target_achievement": {k: (None if v is None else round(v, 2)) for k, v in package_target_achievement.items()},
        },
        "indicator_scores": {
            "satisfaction_pct": satisfaction_pct,
            "service_quality_pct": quality_pct,
            "complaints_on_time_pct": complaints_pct,
            "enrichment_pct": enrichment_pct,
            "compliance_pct": compliance_pct,
            "entry_matching_pct": entry_pct,
            "arrival_boarding_pct": arrival_pct,
            "intercity_boarding_pct": intercity_pct,
            "departure_boarding_pct": departure_pct,
            "exit_matching_pct": exit_pct,
            "housing_confirmation_pct": housing_pct,
        },
        "availability": {
            "complaints_on_time": complaints_available,
            "enrichment": enrichment_available,
            "compliance": compliance_available,
            "entry_matching": entry_available,
            "arrival_boarding": arrival_available,
            "intercity_boarding": intercity_available,
            "departure_boarding": departure_available,
            "exit_matching": exit_available,
            "housing_confirmation": housing_available,
        },
        "gift_points": round(gift_points, 2),
        "gift_incentive": round(gift_incentive, 2),
        "umrah_plus_points": round(umrah_plus_points, 2),
        "umrah_plus_incentive": round(umrah_plus_incentive, 2),
        "award_incentive": round(award_incentive, 2),
        "total_incentives": round(total_incentives, 2),
        "incentives": round(total_incentives, 2),
        "severe_violation_penalty": round(severe_violation_penalty, 2),
        "penalties": round(severe_violation_penalty, 2),
        "tier": tier,
        "methodology_version": APP_VERSION,
        "raw_data": data,
    }


# =========================================================
# 4) المستشار الذكي
# =========================================================
def generate_ai_advisor_report(results, api_key=None, language="العربية"):
    raw = results["raw_data"]
    warnings = []
    actions = []

    if raw.get("has_severe_violation", False):
        warnings.append("🚨 تم تطبيق خصم 5 نقاط مئوية بسبب مخالفة جسيمة في شهر الرصد.")

    comp_pct = results["indicator_scores"].get("compliance_pct")
    if comp_pct is not None and comp_pct < 95:
        affected = max(0, safe_number(raw.get("total_visited_pilgrims")) - safe_number(raw.get("unaffected_pilgrims")))
        warnings.append(f"⚠️ نسبة الالتزام بالخدمات والضوابط {comp_pct:.1f}%، وعدد المعتمرين المتأثرين = {affected:.0f}.")

    complaints_pct = results["indicator_scores"].get("complaints_on_time_pct")
    if complaints_pct is not None and complaints_pct < 90:
        actions.append(f"تحسين معالجة البلاغات ضمن المدة المحددة؛ النسبة الحالية {complaints_pct:.1f}%.")

    # الاقتراب من الحد الأقصى لعمرة+.
    if safe_number(raw.get("umrah_plus_beneficiaries")) < 1000:
        extra = 1000 - safe_number(raw.get("umrah_plus_beneficiaries"))
        gain = min(10.0, extra * 2 / 2000 * 10)
        actions.append(f"تفعيل عمرة+ لتسجيل {extra:.0f} مستفيد إضافي قد يرفع الحافز تدريجيًا حتى السقف المحدد.")

    if api_key and api_key.strip() and genai:
        try:
            genai.configure(api_key=api_key.strip())
            prompt = f"""
أنت مستشار تنفيذي لنظام تصنيف شركات العمرة 1448هـ.
حلل النتيجة التالية دون تغيير أي قاعدة حسابية من المنهجية.
النتيجة النهائية: {results['final_score']}%
النتيجة الأساسية: {results['base_score']}%
تنوع الباقات: {results['score_packages']}/15
تجربة المعتمر والجودة: {results['score_exp']}/45
الالتزام بالبرنامج: {results['score_prog']}/40
المحفزات: +{results['total_incentives']} نقطة مئوية
الخصومات: -{results['penalties']} نقطة مئوية
قدم 3 توصيات عملية مرتبة حسب الأولوية، مع الإشارة إلى المؤشرات التي يجب تحسينها.
"""
            model_names = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
            with st.spinner("🤖 جاري إعداد التحليل الاستشاري..."):
                for model_name in model_names:
                    try:
                        model = genai.GenerativeModel(model_name)
                        response = model.generate_content(prompt)
                        if response and getattr(response, "text", None):
                            return "### 🤖 تحليل ومقترحات الذكاء الاصطناعي\n\n" + response.text
                    except Exception:
                        continue
        except Exception:
            pass

    report = "### 🤖 تقرير استشاري تلقائي\n\n"
    if warnings:
        report += "#### 🛑 التحذيرات\n" + "\n".join(f"- {x}" for x in warnings) + "\n\n"
    if actions:
        report += "#### 🚀 فرص التحسين\n" + "\n".join(f"- {x}" for x in actions) + "\n\n"
    if not warnings and not actions:
        report += "لا توجد تنبيهات آلية بارزة ضمن القواعد المضمنة."
    return report


# =========================================================
# 5) الرسوم
# =========================================================
def render_charts(results):
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=results["final_score"],
                title={"text": "مؤشر التقييم النهائي (%)"},
                gauge={
                    "axis": {"range": [0, 100]},
                    "steps": [
                        {"range": [0, 60], "color": "#F8D7DA"},
                        {"range": [60, 75], "color": "#D9EAF7"},
                        {"range": [75, 90], "color": "#FFF0C2"},
                        {"range": [90, 100], "color": "#DFF0D8"},
                    ],
                },
            )
        )
        fig.update_layout(height=320)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        categories = ["تنوع الباقات", "تجربة المعتمر والجودة", "الالتزام بالبرنامج"]
        scores = [
            results["score_packages"] / 15 * 100,
            results["score_exp"] / 45 * 100,
            results["score_prog"] / 40 * 100,
        ]
        fig = go.Figure(
            go.Scatterpolar(
                r=scores + [scores[0]],
                theta=categories + [categories[0]],
                fill="toself",
                line_color="#1F4E78",
            )
        )
        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=False,
            title="نسبة الإنجاز حسب المحاور الرئيسية",
            height=320,
        )
        st.plotly_chart(fig, use_container_width=True)


def render_indicator_table(results):
    scores = results["indicator_scores"]
    rows = [
        ("الباقات الفاخرة", scores.get("luxury_pct"), WEIGHTS["luxury_packages"]),
        ("الباقات المتوسطة", scores.get("medium_pct"), WEIGHTS["medium_packages"]),
        ("الباقات الاقتصادية", scores.get("economy_pct"), WEIGHTS["economy_packages"]),
        ("رضا المعتمرين", scores.get("satisfaction_pct"), WEIGHTS["satisfaction"]),
        ("جودة أداء الخدمة", scores.get("service_quality_pct"), WEIGHTS["service_quality"]),
        ("البلاغات المغلقة ضمن المدة", scores.get("complaints_on_time_pct"), WEIGHTS["complaints_on_time"]),
        ("الخدمات الإثرائية النوعية", scores.get("enrichment_pct"), WEIGHTS["enrichment"]),
        ("الالتزام بالخدمات والضوابط", scores.get("compliance_pct"), WEIGHTS["compliance"]),
        ("تطابق بيانات الدخول", scores.get("entry_matching_pct"), WEIGHTS["entry_matching"]),
        ("أمر إركاب الوصول", scores.get("arrival_boarding_pct"), WEIGHTS["arrival_boarding"]),
        ("أمر إركاب بين المدن", scores.get("intercity_boarding_pct"), WEIGHTS["intercity_boarding"]),
        ("أمر إركاب المغادرة", scores.get("departure_boarding_pct"), WEIGHTS["departure_boarding"]),
        ("تطابق بيانات الخروج", scores.get("exit_matching_pct"), WEIGHTS["exit_matching"]),
        ("تأكيد السكن", scores.get("housing_confirmation_pct"), WEIGHTS["housing_confirmation"]),
    ]
    df = pd.DataFrame(rows, columns=["المؤشر", "نتيجة المؤشر %", "الوزن %"])
    df["الدرجة المحتسبة"] = df.apply(
        lambda r: None if pd.isna(r["نتيجة المؤشر %"]) else round(r["نتيجة المؤشر %"] / 100 * r["الوزن %"], 2), axis=1
    )
    df["نتيجة المؤشر %"] = df["نتيجة المؤشر %"].apply(lambda x: "غير متاح" if pd.isna(x) else round(float(x), 2))
    st.dataframe(df, use_container_width=True, hide_index=True)


# =========================================================
# 6) الواجهة
# =========================================================
st.title("🕋 نظام إدارة وتصنيف شركات العمرة 1448هـ")
st.caption(f"نسخة محرك التقييم: {APP_VERSION}")

tab1, tab2 = st.tabs(["📊 إجراء التقييم", "📜 سجل التقييمات التاريخية"])

with tab1:
    company_name = st.text_input("اسم الشركة / الرخصة:", "شركة تدبير الغربية")
    st.sidebar.header("⚙️ الخيارات والإعدادات")
    input_mode = st.sidebar.radio("طريقة إدخال البيانات:", ["إدخال يدوي", "استيراد Excel/CSV"])
    gemini_key = st.sidebar.text_input("مفتاح Gemini API - اختياري", type="password")

    if input_mode == "إدخال يدوي":
        st.info("القيم أدناه هي بيانات خام للمؤشرات. لا تُدخل النسب المشتقة؛ النظام يحسبها من المقامات والبسط وفق بطاقات المؤشرات.")
        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("### 1️⃣ تنوع الباقات — 15%")
            luxury_pilgrims = st.number_input("دخول الباقات الفاخرة", min_value=0, value=0, step=1)
            medium_pilgrims = st.number_input("دخول الباقات المتوسطة", min_value=0, value=0, step=1)
            economy_pilgrims = st.number_input("دخول الباقات الاقتصادية", min_value=0, value=0, step=1)
            total_entry_pilgrims = int(luxury_pilgrims + medium_pilgrims + economy_pilgrims)
            st.metric("إجمالي دخول المعتمرين", f"{total_entry_pilgrims:,}")
            if total_entry_pilgrims:
                st.caption(
                    f"فاخر {luxury_pilgrims/total_entry_pilgrims*100:.1f}% | "
                    f"متوسط {medium_pilgrims/total_entry_pilgrims*100:.1f}% | "
                    f"اقتصادي {economy_pilgrims/total_entry_pilgrims*100:.1f}%"
                )
            st.caption("الأهداف المرجعية في المنهجية: فاخر 8%، متوسط 15%، اقتصادي 77%.")

            st.markdown("### 🎁 المحفزات")
            luxury_gifts = st.number_input("هدايا فاخرة (20 نقطة)", min_value=0, value=0, step=1)
            medium_gifts = st.number_input("هدايا متوسطة (4 نقاط)", min_value=0, value=0, step=1)
            economy_gifts = st.number_input("هدايا اقتصادية (1 نقطة)", min_value=0, value=0, step=1)
            umrah_plus_beneficiaries = st.number_input("مستفيدو عمرة+", min_value=0, value=0, step=1)
            has_ministry_award = st.checkbox("الحصول على جائزة من الوزارة (+5 نقاط مئوية)")

        with c2:
            st.markdown("### 2️⃣ تجربة المعتمر وجودة الخدمة — 45%")
            satisfaction_score_pct = st.number_input("رضا المعتمرين (%)", 0.0, 100.0, 0.0, 1.0)
            service_quality_pct = st.number_input("جودة أداء الخدمة (%)", 0.0, 100.0, 0.0, 1.0)

            st.markdown("#### البلاغات")
            total_complaints = st.number_input("إجمالي البلاغات", min_value=0, value=0, step=1)
            closed_complaints_on_time = st.number_input("البلاغات المغلقة ضمن المدة المحددة", min_value=0, value=0, step=1)

            st.markdown("#### الخدمات الإثرائية")
            total_departing_pilgrims = st.number_input("إجمالي المعتمرين المغادرين", min_value=0, value=0, step=1)
            enrichment_beneficiaries = st.number_input("المغادرون المستفيدون من الخدمات النوعية", min_value=0, value=0, step=1)

            st.markdown("#### الالتزام بالخدمات والضوابط")
            total_visited_pilgrims = st.number_input("إجمالي المعتمرين في الزيارات", min_value=0, value=0, step=1)
            unaffected_pilgrims = st.number_input("المعتمرون غير المتأثرين بالمخالفات", min_value=0, value=0, step=1)
            has_severe_violation = st.checkbox("رصد مخالفة جسيمة خلال الشهر (-5 نقاط مئوية)")

        with c3:
            st.markdown("### 3️⃣ الالتزام بالبرنامج — 40%")
            st.markdown("#### بيانات الدخول")
            total_entry_records = st.number_input("إجمالي سجلات الدخول", min_value=0, value=0, step=1)
            matched_entry_records = st.number_input("سجلات الدخول المتطابقة", min_value=0, value=0, step=1)
            st.caption("البرية/البحرية: منفذ الدخول + التاريخ (±3 أيام). الجوية: رقم الرحلة + التاريخ (±12 ساعة).")

            arrival_boarding_orders = st.number_input("أوامر إركاب الوصول", min_value=0, value=0, step=1)
            intercity_boarding_orders = st.number_input("أوامر إركاب بين المدن", min_value=0, value=0, step=1)
            departure_boarding_orders = st.number_input("أوامر إركاب المغادرة", min_value=0, value=0, step=1)

            st.markdown("#### بيانات الخروج")
            total_exit_records = st.number_input("إجمالي سجلات المغادرة", min_value=0, value=0, step=1)
            matched_exit_records = st.number_input("سجلات المغادرة المتطابقة", min_value=0, value=0, step=1)
            st.caption("البرية/البحرية: منفذ الخروج + التاريخ (±3 أيام). الجوية: رقم الرحلة + التاريخ (±12 ساعة).")

            st.markdown("#### تأكيد السكن")
            total_housing_start_pilgrims = st.number_input("إجمالي المعتمرين التي بدأت برامجهم في مكة/المدينة", min_value=0, value=0, step=1)
            confirmed_housing_pilgrims = st.number_input("المعتمرون المؤكد سكنهم عند الوصول", min_value=0, value=0, step=1)

        data = {
            "total_entry_pilgrims": total_entry_pilgrims,
            "luxury_pilgrims": int(luxury_pilgrims),
            "medium_pilgrims": int(medium_pilgrims),
            "economy_pilgrims": int(economy_pilgrims),
            "satisfaction_score_pct": satisfaction_score_pct,
            "service_quality_pct": service_quality_pct,
            "total_complaints": int(total_complaints),
            "closed_complaints_on_time": int(closed_complaints_on_time),
            "total_departing_pilgrims": int(total_departing_pilgrims),
            "enrichment_beneficiaries": int(enrichment_beneficiaries),
            "total_visited_pilgrims": int(total_visited_pilgrims),
            "unaffected_pilgrims": int(unaffected_pilgrims),
            "has_severe_violation": has_severe_violation,
            "total_entry_records": int(total_entry_records),
            "matched_entry_records": int(matched_entry_records),
            "arrival_boarding_orders": int(arrival_boarding_orders),
            "intercity_boarding_orders": int(intercity_boarding_orders),
            "departure_boarding_orders": int(departure_boarding_orders),
            "total_exit_records": int(total_exit_records),
            "matched_exit_records": int(matched_exit_records),
            "total_housing_start_pilgrims": int(total_housing_start_pilgrims),
            "confirmed_housing_pilgrims": int(confirmed_housing_pilgrims),
            "economy_gifts": int(economy_gifts),
            "medium_gifts": int(medium_gifts),
            "luxury_gifts": int(luxury_gifts),
            "umrah_plus_beneficiaries": int(umrah_plus_beneficiaries),
            "has_ministry_award": has_ministry_award,
        }
    else:
        uploaded_file = st.file_uploader("رفع ملف Excel أو CSV", type=["xlsx", "csv"])
        data = None
        if uploaded_file is not None:
            try:
                df = pd.read_csv(uploaded_file) if uploaded_file.name.lower().endswith(".csv") else pd.read_excel(uploaded_file)
                if df.empty:
                    st.error("الملف لا يحتوي على صفوف بيانات.")
                else:
                    st.write("معاينة البيانات")
                    st.dataframe(df.head(10), use_container_width=True)
                    row_number = st.number_input("رقم الصف المراد تقييمه (يبدأ من 1)", min_value=1, max_value=len(df), value=1, step=1)
                    raw_row = df.iloc[int(row_number) - 1].to_dict()
                    # أسماء الأعمدة المتوقعة هي نفس مفاتيح data في الوضع اليدوي.
                    data = {k: (0 if pd.isna(v) else v) for k, v in raw_row.items()}
                    st.warning("الاستيراد المباشر يتطلب أن تتطابق أسماء أعمدة الملف مع مفاتيح البيانات الموثقة في القالب. استخدم القالب المرفق/المعتمد لتجنب أخطاء الربط.")
            except Exception as exc:
                st.error(f"تعذر قراءة الملف: {exc}")

    if st.button("🚀 احتساب التقييم وحفظ النتيجة", type="primary", disabled=data is None):
        try:
            results = calculate_umrah_company_score(data)
            save_evaluation(company_name, results)
            st.session_state["latest_results"] = results
            st.session_state["latest_company"] = company_name
            st.rerun()
        except ValueError as exc:
            st.error("❌ لا يمكن احتساب التقييم بسبب أخطاء منطقية في البيانات:")
            for err in str(exc).splitlines():
                st.error(f"• {err}")

    if st.session_state.get("latest_results"):
        results = st.session_state["latest_results"]
        comp_name = st.session_state.get("latest_company", company_name)

        st.success("تم احتساب وحفظ النتيجة وفق محرك المنهجية.")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("النتيجة النهائية", f"{results['final_score']}%")
        m2.metric("النتيجة الأساسية", f"{results['base_score']}%")
        m3.metric("المحفزات", f"+{results['total_incentives']}%")
        m4.metric("الخصم", f"-{results['penalties']}%")

        if results["score_before_cap"] > 100:
            st.info(f"النتيجة قبل سقف 100 = {results['score_before_cap']}%. تم تطبيق السقف؛ يظهر الأثر التفصيلي للمحفزات في التقرير.")

        render_charts(results)
        st.markdown("### 📋 تفصيل المؤشرات")
        render_indicator_table(results)

        st.markdown("### 🎯 تحقيق أهداف مزيج الباقات")
        targets = results["package_indicators"]["target_achievement"]
        target_df = pd.DataFrame(
            [
                ["فاخر", PACKAGE_TARGETS["luxury"], results["package_indicators"]["luxury_pct"], targets["luxury"]],
                ["متوسط", PACKAGE_TARGETS["medium"], results["package_indicators"]["medium_pct"], targets["medium"]],
                ["اقتصادي", PACKAGE_TARGETS["economy"], results["package_indicators"]["economy_pct"], targets["economy"]],
            ],
            columns=["الفئة", "الهدف %", "الفعلي %", "نسبة تحقيق الهدف %"],
        )
        st.dataframe(target_df, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown(generate_ai_advisor_report(results, gemini_key))

        if generate_pdf_report:
            try:
                pdf_bytes = generate_pdf_report(comp_name, results)
                st.download_button(
                    label="📄 تصدير التقرير النهائي PDF",
                    data=pdf_bytes,
                    file_name=f"تقرير_تقييم_{comp_name}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            except Exception as exc:
                st.warning(f"تعذر إنشاء PDF من مولد التقارير الحالي: {exc}")
        else:
            st.warning("وحدة pdf_generator غير متاحة؛ تم إكمال التقييم دون PDF.")

with tab2:
    st.subheader("📜 السجل التاريخي لتقييمات الشركات")
    conn = sqlite3.connect(DB_PATH)
    df_history = pd.read_sql_query(
        """
        SELECT id, eval_date, company_name, final_score, tier,
               score_packages, score_exp, score_prog, incentives, penalties
        FROM evaluations
        ORDER BY id DESC
        """,
        conn,
    )
    conn.close()

    if not df_history.empty:
        st.dataframe(df_history, use_container_width=True, hide_index=True)
        fig_history = px.line(
            df_history,
            x="eval_date",
            y="final_score",
            color="company_name",
            markers=True,
            title="تطور الدرجة النهائية عبر التقييمات",
        )
        st.plotly_chart(fig_history, use_container_width=True)
    else:
        st.info("لا توجد تقييمات محفوظة حتى الآن.")
