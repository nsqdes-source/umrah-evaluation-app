import os
import io
import urllib.request
import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------------------------------------------------------
# إعداد وتنزيل الخط العربي (Amiri Font)
# ---------------------------------------------------------
FONT_PATH = "Amiri-Regular.ttf"
FONT_NAME = "Amiri"

def setup_arabic_font():
    """تنزيل وتسجيل خط Amiri لدعم اللغة العربية في ReportLab"""
    if not os.path.exists(FONT_PATH):
        font_url = "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf"
        try:
            urllib.request.urlretrieve(font_url, FONT_PATH)
        except Exception as e:
            print(f"تعذر تنزيل الخط تلقائياً: {e}")
            
    if os.path.exists(FONT_PATH):
        pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
        return FONT_NAME
    return "Helvetica"  # خط بديل في حال تعذر التنزيل

def reshape_ar(text):
    """تهيئة وتنسيق النص العربي للعرض من اليمين إلى اليسار"""
    if not text:
        return ""
    reshaped_text = arabic_reshaper.reshape(str(text))
    return get_display(reshaped_text)

# ---------------------------------------------------------
# دالة إنتاج التقرير
# ---------------------------------------------------------
def generate_pdf_report(company_name, results):
    active_font = setup_arabic_font()
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    
    elements = []
    styles = getSampleStyleSheet()

    # أنماط النصوص المخصصة
    title_style = ParagraphStyle(
        'ArabicTitle',
        parent=styles['Normal'],
        fontName=active_font,
        fontSize=18,
        leading=24,
        alignment=1,  # وسط
        textColor=colors.HexColor('#1F4E78')
    )
    
    subtitle_style = ParagraphStyle(
        'ArabicSubTitle',
        parent=styles['Normal'],
        fontName=active_font,
        fontSize=12,
        leading=16,
        alignment=1,
        textColor=colors.HexColor('#595959')
    )

    cell_style = ParagraphStyle(
        'ArabicCell',
        parent=styles['Normal'],
        fontName=active_font,
        fontSize=10,
        leading=14,
        alignment=2  # يمين
    )

    header_style = ParagraphStyle(
        'ArabicHeader',
        parent=styles['Normal'],
        fontName=active_font,
        fontSize=10,
        leading=14,
        alignment=2,
        textColor=colors.white
    )

    # رأس التقرير
    elements.append(Paragraph(reshape_ar(f"تقرير تقييم شركة: {company_name}"), title_style))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(reshape_ar("نظام تصنيف شركات ومؤسسات العمرة - موسم 1448هـ"), subtitle_style))
    elements.append(Spacer(1, 15))

    # 1. جدول الملخص العام
    summary_headers = ["إجمالي الخصومات", "إجمالي المحفزات", "التصنيف المستحق", "النتيجة النهائية"]
    summary_values = [
        f"-{results['penalties']}%",
        f"+{results['incentives']}%",
        results['tier'],
        f"{results['final_score']}%"
    ]

    summary_data = [
        [Paragraph(reshape_ar(h), header_style) for h in summary_headers],
        [Paragraph(reshape_ar(v), cell_style) for v in summary_values]
    ]

    t_summary = Table(summary_data, colWidths=[120, 120, 160, 140])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E78')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D9D9D9')),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#F2F4F8')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(t_summary)
    elements.append(Spacer(1, 20))

    # 2. جدول تفاصيل الدرجات حسب المحاور
    elements.append(Paragraph(reshape_ar("تفاصيل درجات المحاور الرئيسية:"), cell_style))
    elements.append(Spacer(1, 8))

    details_headers = ["الدرجة المحققة", "الوزن المخصص", "المحور الرئيسي"]
    details_rows = [
        [f"{results['score_packages']}%", "15%", "تنوع باقات الخدمات"],
        [f"{results['score_exp']}%", "45%", "تجربة المعتمر وجودة الخدمة"],
        [f"{results['score_prog']}%", "40%", "الالتزام بالبرنامج والضوابط التشغيلية"],
        [f"{results['base_score']}%", "100%", "النتيجة الأساسية (قبل المحفزات والخصومات)"]
    ]

    details_data = [[Paragraph(reshape_ar(h), header_style) for h in details_headers]]
    for row in details_rows:
        details_data.append([Paragraph(reshape_ar(item), cell_style) for item in row])

    t_details = Table(details_data, colWidths=[140, 120, 280])
    t_details.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E78')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D9D9D9')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E9EEF4')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(t_details)

    # بناء التقرير
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()