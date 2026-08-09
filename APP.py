import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import sqlite3
import json
from datetime import datetime

# استدعاء دالة توليد الـ PDF
from pdf_generator import generate_pdf_report

# ---------------------------------------------------------
# 0. إعدادات الصفحة وقاعدة البيانات
# ---------------------------------------------------------
st.set_page_config(
    page_title="نظام إدارة وتصنيف شركات العمرة 1448هـ",
    page_icon="🕋",
    layout="wide"
)

def init_db():
    conn = sqlite3.connect("umrah_evaluations.db")
    c = conn.cursor()
    c.execute('''
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
    ''')
    conn.commit()
    conn.close()

def save_evaluation(company_name, results):
    conn = sqlite3.connect("umrah_evaluations.db")
    c = conn.cursor()
    c.execute('''
        INSERT INTO evaluations 
        (eval_date, company_name, final_score, tier, score_packages, score_exp, score_prog, incentives, penalties, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        company_name,
        results['final_score'],
        results['tier'],
        results['score_packages'],
        results['score_exp'],
        results['score_prog'],
        results['total_incentives'],
        results['penalties'],
        json.dumps(results['raw_data'])
    ))
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------
# 1. محرك التقييم الحسابي
# ---------------------------------------------------------
def calculate_umrah_company_score(data):
    # أ. تنوع باقات الخدمات (15%)
    total_entry = data.get("total_entry_pilgrims", 0) or 1
    p_luxury = min(1.0, (data.get("luxury_pilgrims", 0) or 0) / total_entry) * 7.0
    p_medium = min(1.0, (data.get("medium_pilgrims", 0) or 0) / total_entry) * 5.0
    p_economy = min(1.0, (data.get("economy_pilgrims", 0) or 0) / total_entry) * 3.0
    score_packages = p_luxury + p_medium + p_economy

    # ب. تجربة المعتمر وجودة الخدمة والمخالفات التشغيلية (45%)
    p_satisfaction = ((data.get("satisfaction_score_pct", 0.0) or 0.0) / 100.0) * 10.0
    p_quality = ((data.get("service_quality_pct", 0.0) or 0.0) / 100.0) * 5.0
    
    total_complaints = data.get("total_complaints", 0) or 0
    closed_complaints = data.get("closed_complaints", 0) or 0
    p_complaints = (closed_complaints / total_complaints * 10.0) if total_complaints > 0 else 10.0

    total_departing = data.get("total_departing_pilgrims", 0) or 1
    p_enrichment = min(1.0, (data.get("enrichment_beneficiaries", 0) or 0) / total_departing) * 10.0

    # مؤشر الالتزام بالخدمات والضوابط (تأثير المخالفات العادية على التقييم)
    total_visited = data.get("total_visited_pilgrims", 0) or 0
    unaffected = data.get("unaffected_pilgrims", 0) or 0
    p_compliance = (unaffected / total_visited * 10.0) if total_visited > 0 else 10.0

    score_exp = p_satisfaction + p_quality + p_complaints + p_enrichment + p_compliance

    # ج. الالتزام بالبرنامج (40%)
    total_entry_rec = data.get("total_entry_records", 0) or 1
    p_entry_match = ((data.get("matched_entry_records", 0) or 0) / total_entry_rec) * 10.0
    
    p_arr_boarding = min(1.0, (data.get("arrival_boarding_orders", 0) or 0) / total_entry) * 5.0
    p_inter_boarding = min(1.0, (data.get("intercity_boarding_orders", 0) or 0) / total_departing) * 5.0
    p_dep_boarding = min(1.0, (data.get("departure_boarding_orders", 0) or 0) / total_departing) * 5.0

    total_exit_rec = data.get("total_exit_records", 0) or 1
    p_exit_match = ((data.get("matched_exit_records", 0) or 0) / total_exit_rec) * 10.0

    total_housing = data.get("total_housing_programs", 0) or 1
    p_housing = ((data.get("confirmed_housing", 0) or 0) / total_housing) * 5.0

    score_prog = p_entry_match + p_arr_boarding + p_inter_boarding + p_dep_boarding + p_exit_match + p_housing

    # مجموع النتيجة الأساسية (100%)
    base_score = score_packages + score_exp + score_prog

    # د. المحفزات (Incentives)
    gift_points = ((data.get("economy_gifts", 0) or 0) * 1) + \
                  ((data.get("medium_gifts", 0) or 0) * 4) + \
                  ((data.get("luxury_gifts", 0) or 0) * 20)
    gift_incentive = min(5.0, (gift_points / 8000.0) * 5.0)

    umrah_plus_points = (data.get("umrah_plus_beneficiaries", 0) or 0) * 2
    umrah_plus_incentive = min(10.0, (umrah_plus_points / 2000.0) * 10.0)

    # جوائز الوزارة: نسبة مقطوعة ثابته (+5%) عند وجود جائزة
    award_incentive = 5.0 if data.get("has_ministry_award", False) else 0.0

    total_incentives = gift_incentive + umrah_plus_incentive + award_incentive

    # هـ. الخصومات (Penalties)
    severe_violation_penalty = 5.0 if data.get("has_severe_violation", False) else 0.0

    # النتيجة النهائية
    final_score = min(100.0, max(0.0, base_score + total_incentives - severe_violation_penalty))

    if final_score >= 90:
        tier = "الفئة الماسية (Class A)"
    elif final_score >= 75:
        tier = "الفئة الذهبية (Class B)"
    elif final_score >= 60:
        tier = "الفئة الفضية (Class C)"
    else:
        tier = "غير معتمد / يحتاج تحسين"

    return {
        "final_score": round(final_score, 2),
        "base_score": round(base_score, 2),
        "score_packages": round(score_packages, 2),
        "score_exp": round(score_exp, 2),
        "score_prog": round(score_prog, 2),
        "gift_incentive": round(gift_incentive, 2),
        "umrah_plus_incentive": round(umrah_plus_incentive, 2),
        "award_incentive": round(award_incentive, 2),
        "total_incentives": round(total_incentives, 2),
        "incentives": round(total_incentives, 2),  # إضافة المفتاح المطلوب لتقرير PDF
        "severe_violation_penalty": round(severe_violation_penalty, 2),
        "penalties": round(severe_violation_penalty, 2),  # المفتاح المطلوب لتقرير PDF
        "tier": tier,
        "raw_data": data
    }

# ---------------------------------------------------------
# 2. وكيل الذكاء الاصطناعي للاستشارات
# ---------------------------------------------------------
def generate_ai_advisor_report(results, api_key=None):
    raw = results['raw_data']
    warnings = []
    actions = []

    if raw.get('has_severe_violation', False):
        warnings.append("🚨 **خصم مباشر (-5%):** تم رصد مخالفة جسيمة خلال الشهر (مثل السكن غير المرخص أو عدم توفر تذاكر المغادرة).")
    
    total_visited = raw.get('total_visited_pilgrims', 0) or 0
    unaffected = raw.get('unaffected_pilgrims', 0) or 0
    if total_visited > 0 and (unaffected / total_visited) < 0.95:
        affected_cnt = total_visited - unaffected
        warnings.append(f"⚠️ **مخالفات تشغيلية:** تم رصد تأثر {affected_cnt} معتمر بمخالفات أثناء الزيارات الميدانية.")

    if raw.get('umrah_plus_beneficiaries', 0) < 1000:
        pts_needed = 1000 - raw.get('umrah_plus_beneficiaries', 0)
        gain = round((pts_needed * 2 / 2000) * 10, 1)
        actions.append(f"💡 **تفعيل مبادرة (عمرة+):** تسجيل {pts_needed} معتمر إضافي يمنحك زيادة تحفيزية قدرها **+{gain}%**.")

    if api_key:
        try:
            import openai
            openai.api_key = api_key
            prompt = f"تحليل تقييم شركة عمرة: النتيجة {results['final_score']}% ({results['tier']}). قدم 3 توصيات تنفيذية."
            res = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300
            )
            return res.choices[0].message.content
        except Exception:
            pass

    report = "### 🤖 تقرير وكيل الذكاء الاصطناعي للاستشارات\n\n"
    if warnings:
        report += "#### 🛑 التحذيرات والمخاطر العاجلة:\n" + "\n".join([f"- {w}" for w in warnings]) + "\n\n"
    if actions:
        report += "#### 🚀 أسرع الفرص لرفع التصنيف:\n" + "\n".join([f"- {a}" for a in actions]) + "\n\n"
    return report

# ---------------------------------------------------------
# 3. الرسوم البيانية التفاعلية
# ---------------------------------------------------------
def render_charts(results):
    c1, c2 = st.columns(2)
    
    with c1:
        fig_gauge = go.Figure(go.Indicator(
            mode = "gauge+number",
            value = results['final_score'],
            domain = {'x': [0, 1], 'y': [0, 1]},
            title = {'text': "مؤشر التقييم النهائي (%)"},
            gauge = {
                'axis': {'range': [0, 100]},
                'bar': {'color': "#1F4E78"},
                'steps': [
                    {'range': [0, 60], 'color': "#FFD2D2"},
                    {'range': [60, 75], 'color': "#D9E1F2"},
                    {'range': [75, 90], 'color': "#FFE699"},
                    {'range': [90, 100], 'color': "#E2EFDA"}
                ]
            }
        ))
        fig_gauge.update_layout(height=300)
        st.plotly_chart(fig_gauge, width="stretch")

    with c2:
        categories = ['تنوع الباقات', 'تجربة المعتمر والجودة', 'الالتزام بالبرنامج']
        scores = [
            (results['score_packages'] / 15.0) * 100,
            (results['score_exp'] / 45.0) * 100,
            (results['score_prog'] / 40.0) * 100
        ]
        
        fig_radar = go.Figure(data=go.Scatterpolar(
            r=scores + [scores[0]],
            theta=categories + [categories[0]],
            fill='toself',
            fillcolor='rgba(31, 78, 120, 0.4)',
            line_color='#1F4E78'
        ))
        fig_radar.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=False,
            title="نسبة الإنجاز حسب المحاور الرئيسية",
            height=300
        )
        st.plotly_chart(fig_radar, width="stretch")

# ---------------------------------------------------------
# 4. واجهة المستخدم الرئيسية
# ---------------------------------------------------------
st.title("🕋 نظام إدارة وتصنيف شركات العمرة 1448هـ")

tab1, tab2 = st.tabs(["📊 إجراء التقييم الحالية", "📜 سجل التقييمات التاريخية"])

with tab1:
    company_name = st.text_input("اسم الشركة / الرخصة:", "شركة عمرة النموذجية")
    
    st.sidebar.header("⚙️ الخيارات والإعدادات")
    input_mode = st.sidebar.radio("طريقة إدخال البيانات:", ["إدخال يدوي (Manual)", "استيراد ملف Excel"])
    openai_key = st.sidebar.text_input("مفتاح OpenAI API (اختياري):", type="password")

    data = {}

    if input_mode == "إدخال يدوي (Manual)":
        col1, col2, col3 = st.columns(3)
        
        # العمود الأول: تنوع الباقات والمحفزات
        with col1:
            st.markdown("### 1️⃣ تنوع الباقات (15%)")
            
            col_lux_in, col_lux_out = st.columns([3, 2])
            with col_lux_in:
                luxury_pilgrims = st.number_input("عدد الباقات الفاخرة", min_value=0, value=None, placeholder="0", step=1, key="in_lux_pax")

            col_mid_in, col_mid_out = st.columns([3, 2])
            with col_mid_in:
                medium_pilgrims = st.number_input("عدد الباقات المتوسطة", min_value=0, value=None, placeholder="0", step=1, key="in_mid_pax")

            col_eco_in, col_eco_out = st.columns([3, 2])
            with col_eco_in:
                economy_pilgrims = st.number_input("عدد الباقات الاقتصادية", min_value=0, value=None, placeholder="0", step=1, key="in_eco_pax")

            # الحساب التلقائي لإجمالي الباقات والنسب
            v_lux = luxury_pilgrims if luxury_pilgrims is not None else 0
            v_mid = medium_pilgrims if medium_pilgrims is not None else 0
            v_eco = economy_pilgrims if economy_pilgrims is not None else 0
            total_entry_pilgrims = v_lux + v_mid + v_eco

            p_lux_pct = (v_lux / total_entry_pilgrims * 100.0) if total_entry_pilgrims > 0 else 0.0
            p_mid_pct = (v_mid / total_entry_pilgrims * 100.0) if total_entry_pilgrims > 0 else 0.0
            p_eco_pct = (v_eco / total_entry_pilgrims * 100.0) if total_entry_pilgrims > 0 else 0.0

            # عرض النسب بجانب كل خانة
            with col_lux_out:
                st.markdown("<br>", unsafe_allow_html=True)
                st.caption(f"النسبة: **{p_lux_pct:.1f}%**")

            with col_mid_out:
                st.markdown("<br>", unsafe_allow_html=True)
                st.caption(f"النسبة: **{p_mid_pct:.1f}%**")

            with col_eco_out:
                st.markdown("<br>", unsafe_allow_html=True)
                st.caption(f"النسبة: **{p_eco_pct:.1f}%**")

            st.text_input("إجمالي عدد الباقات (تلقائي)", value=f"{total_entry_pilgrims} باقة", disabled=True)

            st.markdown("---")
            st.markdown("### 🎁 المحفزات والجوائز")
            
            col_g_lux_in, col_g_lux_out = st.columns([3, 2])
            with col_g_lux_in:
                luxury_gifts = st.number_input("عدد الهدايا الفاخرة (20 نقطة)", min_value=0, value=None, placeholder="0", step=1, key="in_g_lux")

            col_g_mid_in, col_g_mid_out = st.columns([3, 2])
            with col_g_mid_in:
                medium_gifts = st.number_input("عدد الهدايا المتوسطة (4 نقاط)", min_value=0, value=None, placeholder="0", step=1, key="in_g_mid")

            col_g_eco_in, col_g_eco_out = st.columns([3, 2])
            with col_g_eco_in:
                economy_gifts = st.number_input("عدد الهدايا الاقتصادية (1 نقطة)", min_value=0, value=None, placeholder="0", step=1, key="in_g_eco")

            # الحساب التلقائي لإجمالي الهدايا والنسب
            v_g_lux = luxury_gifts if luxury_gifts is not None else 0
            v_g_mid = medium_gifts if medium_gifts is not None else 0
            v_g_eco = economy_gifts if economy_gifts is not None else 0
            total_gifts = v_g_lux + v_g_mid + v_g_eco

            g_lux_pct = (v_g_lux / total_gifts * 100.0) if total_gifts > 0 else 0.0
            g_mid_pct = (v_g_mid / total_gifts * 100.0) if total_gifts > 0 else 0.0
            g_eco_pct = (v_g_eco / total_gifts * 100.0) if total_gifts > 0 else 0.0

            # عرض النسب بجانب كل خانة
            with col_g_lux_out:
                st.markdown("<br>", unsafe_allow_html=True)
                st.caption(f"النسبة: **{g_lux_pct:.1f}%**")

            with col_g_mid_out:
                st.markdown("<br>", unsafe_allow_html=True)
                st.caption(f"النسبة: **{g_mid_pct:.1f}%**")

            with col_g_eco_out:
                st.markdown("<br>", unsafe_allow_html=True)
                st.caption(f"النسبة: **{g_eco_pct:.1f}%**")

            st.text_input("إجمالي عدد الهدايا (تلقائي)", value=f"{total_gifts} هدية", disabled=True)

            umrah_plus_beneficiaries = st.number_input("معتمري مبادرة (عمرة+)", min_value=0, value=None, placeholder="0", key="in_umrah_plus")
            has_ministry_award = st.checkbox("حاصل على جائزة من الوزارة (+5%)", key="in_has_award")

        # العمود الثاني: تجربة المعتمر والجودة والمخالفات
        with col2:
            st.markdown("### 2️⃣ تجربة المعتمر والجودة (45%)")
            
            satisfaction_score_pct = st.number_input("رضا المعتمرين (%)", 0.0, 100.0, value=None, placeholder="0", step=1.0, key="in_sat")
            service_quality_pct = st.number_input("تقييم جودة الخدمة (%)", 0.0, 100.0, value=None, placeholder="0", step=1.0, key="in_qual")

            st.caption("معالجة الشكاوى والبلاغات:")
            total_complaints = st.number_input("إجمالي البلاغات الواردة", min_value=0, value=None, placeholder="0", key="in_tot_comp")
            closed_complaints = st.number_input("البلاغات المعالجة وفق SLA", min_value=0, value=None, placeholder="0", key="in_cls_comp")

            total_departing_pilgrims = st.number_input("إجمالي المعتمرين المغادرين", min_value=0, value=None, placeholder="0", key="in_tot_dep")
            enrichment_beneficiaries = st.number_input("المستفيدون من الخدمات الإثرائية", min_value=0, value=None, placeholder="0", key="in_enrich")

            st.markdown("---")
            st.markdown("### ⚠️ المخالفات والامتثال")
            total_visited_pilgrims = st.number_input("إجمالي المعتمرين في الزيارات الميدانية", min_value=0, value=None, placeholder="0", key="in_tot_vis")
            unaffected_pilgrims = st.number_input("عدد المعتمرين غير المتأثرين بمخالفات", min_value=0, value=None, placeholder="0", key="in_unaff_pax")
            has_severe_violation = st.checkbox("رصد مخالفة جسيمة خلال الشهر (-5%)", key="in_severe_viol")

        # العمود الثالث: الالتزام بالبرنامج
        with col3:
            st.markdown("### 3️⃣ الالتزام بالبرنامج (40%)")

            total_entry_records = st.number_input("إجمالي سجلات القدوم", min_value=0, value=None, placeholder="0", key="in_tot_ent_rec")
            matched_entry_records = st.number_input("سجلات القدوم المتطابقة", min_value=0, value=None, placeholder="0", key="in_mtch_ent_rec")

            arrival_boarding_orders = st.number_input("أوامر إركاب الوصول الصادرة", min_value=0, value=None, placeholder="0", key="in_arr_board")
            intercity_boarding_orders = st.number_input("أوامر إركاب بين المدن الصادرة", min_value=0, value=None, placeholder="0", key="in_inter_board")
            departure_boarding_orders = st.number_input("أوامر إركاب المغادرة الصادرة", min_value=0, value=None, placeholder="0", key="in_dep_board")

            total_exit_records = st.number_input("إجمالي سجلات المغادرة", min_value=0, value=None, placeholder="0", key="in_tot_ext_rec")
            matched_exit_records = st.number_input("سجلات المغادرة المتطابقة", min_value=0, value=None, placeholder="0", key="in_mtch_ext_rec")

            total_housing_programs = st.number_input("إجمالي برامج العمرة مع السكن", min_value=0, value=None, placeholder="0", key="in_tot_hsg")
            confirmed_housing = st.number_input("المؤكد سكنهم إلكترونياً عند الوصول", min_value=0, value=None, placeholder="0", key="in_cnfm_hsg")

    else:
        uploaded_file = st.file_uploader("رفع ملف Excel:", type=['xlsx', 'csv'])
        if uploaded_file:
            df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
            data = df.iloc[0].to_dict()
        else:
            st.stop()

    if st.button("🚀 احتساب التقييم وحفظ النتيجة", type="primary"):
        if input_mode == "إدخال يدوي (Manual)":
            data = {
                'total_entry_pilgrims': total_entry_pilgrims,
                'luxury_pilgrims': v_lux,
                'medium_pilgrims': v_mid,
                'economy_pilgrims': v_eco,
                'economy_gifts': v_g_eco,
                'medium_gifts': v_g_mid,
                'luxury_gifts': v_g_lux,
                'umrah_plus_beneficiaries': umrah_plus_beneficiaries,
                'has_ministry_award': has_ministry_award,
                'satisfaction_score_pct': satisfaction_score_pct,
                'service_quality_pct': service_quality_pct,
                'total_complaints': total_complaints,
                'closed_complaints': closed_complaints,
                'total_departing_pilgrims': total_departing_pilgrims,
                'enrichment_beneficiaries': enrichment_beneficiaries,
                'total_visited_pilgrims': total_visited_pilgrims,
                'unaffected_pilgrims': unaffected_pilgrims,
                'has_severe_violation': has_severe_violation,
                'total_entry_records': total_entry_records,
                'matched_entry_records': matched_entry_records,
                'arrival_boarding_orders': arrival_boarding_orders,
                'intercity_boarding_orders': intercity_boarding_orders,
                'departure_boarding_orders': departure_boarding_orders,
                'total_exit_records': total_exit_records,
                'matched_exit_records': matched_exit_records,
                'total_housing_programs': total_housing_programs,
                'confirmed_housing': confirmed_housing
            }

        results = calculate_umrah_company_score(data)
        save_evaluation(company_name, results)

        # حفظ النتيجة في الجلسة لعرضها بعد إعادة التحميل
        st.session_state['latest_results'] = results
        st.session_state['latest_company'] = company_name

        # تفريغ جميع خانات الإدخال تلقائياً
        keys_to_clear = [
            'in_lux_pax', 'in_mid_pax', 'in_eco_pax',
            'in_g_eco', 'in_g_mid', 'in_g_lux', 'in_umrah_plus', 'in_has_award',
            'in_sat', 'in_qual', 'in_tot_comp', 'in_cls_comp',
            'in_tot_dep', 'in_enrich', 'in_tot_vis', 'in_unaff_pax', 'in_severe_viol',
            'in_tot_ent_rec', 'in_mtch_ent_rec', 'in_arr_board', 'in_inter_board',
            'in_dep_board', 'in_tot_ext_rec', 'in_mtch_ext_rec', 'in_tot_hsg', 'in_cnfm_hsg'
        ]
        for k in keys_to_clear:
            if k in st.session_state:
                del st.session_state[k]

        st.rerun()

    # عرض نتائج التقييم الأخير والتقرير
    if 'latest_results' in st.session_state and st.session_state['latest_results']:
        results = st.session_state['latest_results']
        comp_name = st.session_state['latest_company']

        st.success("تم حساب وحفظ النتيجة بنجاح!")

        res_c1, res_c2, res_c3, res_c4 = st.columns(4)
        res_c1.metric("الدرجة النهائية", f"{results['final_score']}%")
        res_c2.metric("التصنيف المستحق", results['tier'])
        res_c3.metric("إجمالي المحفزات", f"+{results['total_incentives']}%")
        res_c4.metric("خصم المخالفات الجسيمة", f"-{results['penalties']}%")

        render_charts(results)
        st.markdown("---")
        
        ai_report = generate_ai_advisor_report(results, openai_key)
        st.markdown(ai_report)

        pdf_bytes = generate_pdf_report(comp_name, results)
        
        st.download_button(
            label="📄 تصدير التقرير النهائي (PDF)",
            data=pdf_bytes,
            file_name=f"تقرير_تقييم_{comp_name}.pdf",
            mime="application/pdf",
            width="stretch"
        )

with tab2:
    st.subheader("📜 السجل التاريخي لتقييمات الشركات")
    conn = sqlite3.connect("umrah_evaluations.db")
    df_history = pd.read_sql_query("SELECT id, eval_date, company_name, final_score, tier, score_packages, score_exp, score_prog, incentives, penalties FROM evaluations ORDER BY id DESC", conn)
    conn.close()

    if not df_history.empty:
        st.dataframe(df_history, width="stretch")
        fig_history = px.line(df_history, x="eval_date", y="final_score", color="company_name", markers=True, title="تطور الدرجة النهائية عبر التقييمات المتعاقبة")
        st.plotly_chart(fig_history, width="stretch")
    else:
        st.info("لا توجد تقييمات محفوظة حتى الآن.")
