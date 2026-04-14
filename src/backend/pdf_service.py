"""
PDF Service - Server-side PDF generation using ReportLab with Markdown support
"""

import io
import os
import re
import json
import logging
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, KeepTogether, ListFlowable, ListItem, Preformatted
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from config import ROBOT_IMAGES_DIR, _LEGACY_IMAGES_DIR, DATA_DIR
from database import get_db_connection

# === Font Registration ===
# Try OTF fonts first (downloaded at Docker build time), fall back to CID fonts

FONTS_DIR = os.path.join(os.path.dirname(__file__), 'fonts')

CJK_FONT = 'Helvetica'
CJK_BOLD = 'Helvetica-Bold'
MONO_FONT = 'Courier'

try:
    from reportlab.pdfbase.ttfonts import TTFont
    regular_path = os.path.join(FONTS_DIR, 'NotoSansCJKtc-Regular.otf')
    bold_path = os.path.join(FONTS_DIR, 'NotoSansCJKtc-Bold.otf')
    mono_path = os.path.join(FONTS_DIR, 'IBMPlexMono-Regular.otf')

    if os.path.exists(regular_path):
        pdfmetrics.registerFont(TTFont('NotoSansCJK', regular_path))
        CJK_FONT = 'NotoSansCJK'
    if os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont('NotoSansCJK-Bold', bold_path))
        CJK_BOLD = 'NotoSansCJK-Bold'
    else:
        CJK_BOLD = CJK_FONT
    if os.path.exists(mono_path):
        pdfmetrics.registerFont(TTFont('IBMPlexMono', mono_path))
        MONO_FONT = 'IBMPlexMono'
except Exception as e:
    logging.warning(f"OTF font registration failed: {e}, trying CID fallback")

# CID fallback for environments without OTF files
if CJK_FONT == 'Helvetica':
    try:
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        CJK_FONT = 'STSong-Light'
        CJK_BOLD = 'STSong-Light'
    except Exception as e:
        logging.warning(f"CID font fallback also failed: {e}. CJK text may not render.")

# === Color Scheme (amber / teal) ===
AMBER = colors.HexColor('#b8860b')
TEAL = colors.HexColor('#008b72')
OK_GREEN = colors.HexColor('#28a745')
NG_RED = colors.HexColor('#dc3545')
MUTED_TEXT = colors.HexColor('#888888')
CODE_BG = colors.HexColor('#1a1f25')

# === Logo ===
_LOGO_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), '..', 'frontend', 'static', 'favicon.png'),
    '/app/src/frontend/static/favicon.png',
]

def _find_logo():
    for p in _LOGO_CANDIDATES:
        abspath = os.path.abspath(p)
        if os.path.exists(abspath):
            return abspath
    return None


def get_styles():
    """Create custom paragraph styles including markdown styles"""
    styles = getSampleStyleSheet()

    # Base styles
    styles.add(ParagraphStyle(
        name='ReportTitle',
        fontName=CJK_BOLD,
        fontSize=24,
        textColor=AMBER,
        spaceAfter=12,
        alignment=TA_CENTER
    ))

    styles.add(ParagraphStyle(
        name='SectionHeader',
        fontName=CJK_BOLD,
        fontSize=14,
        textColor=AMBER,
        spaceBefore=12,
        spaceAfter=8,
        borderWidth=1,
        borderColor=AMBER,
        borderPadding=4
    ))

    styles.add(ParagraphStyle(
        name='CJKNormal',
        fontName=CJK_FONT,
        fontSize=10,
        textColor=colors.black,
        spaceAfter=6,
        leading=14
    ))

    styles.add(ParagraphStyle(
        name='CJKNormalCenter',
        fontName=CJK_FONT,
        fontSize=10,
        textColor=colors.black,
        spaceAfter=6,
        leading=14,
        alignment=TA_CENTER
    ))

    styles.add(ParagraphStyle(
        name='PointName',
        fontName=CJK_BOLD,
        fontSize=12,
        textColor=TEAL,
        spaceBefore=8,
        spaceAfter=4
    ))

    styles.add(ParagraphStyle(
        name='SmallText',
        fontName=CJK_FONT,
        fontSize=8,
        textColor=MUTED_TEXT
    ))

    # Markdown styles
    styles.add(ParagraphStyle(
        name='MDH1',
        fontName=CJK_BOLD,
        fontSize=16,
        textColor=AMBER,
        spaceBefore=14,
        spaceAfter=8,
        leading=20
    ))

    styles.add(ParagraphStyle(
        name='MDH2',
        fontName=CJK_BOLD,
        fontSize=14,
        textColor=AMBER,
        spaceBefore=12,
        spaceAfter=6,
        leading=18
    ))

    styles.add(ParagraphStyle(
        name='MDH3',
        fontName=CJK_BOLD,
        fontSize=12,
        textColor=AMBER,
        spaceBefore=10,
        spaceAfter=4,
        leading=16
    ))

    styles.add(ParagraphStyle(
        name='MDH4',
        fontName=CJK_BOLD,
        fontSize=11,
        textColor=TEAL,
        spaceBefore=8,
        spaceAfter=4,
        leading=14
    ))

    styles.add(ParagraphStyle(
        name='MDParagraph',
        fontName=CJK_FONT,
        fontSize=10,
        textColor=colors.black,
        spaceBefore=4,
        spaceAfter=6,
        leading=14
    ))

    styles.add(ParagraphStyle(
        name='MDBlockquote',
        fontName=CJK_FONT,
        fontSize=10,
        textColor=MUTED_TEXT,
        leftIndent=15,
        borderLeftWidth=2,
        borderLeftColor=AMBER,
        borderLeftPadding=8,
        spaceBefore=6,
        spaceAfter=6,
        leading=14
    ))

    styles.add(ParagraphStyle(
        name='MDCode',
        fontName=MONO_FONT,
        fontSize=9,
        textColor=colors.HexColor('#00f0ff'),
        backColor=CODE_BG,
        borderWidth=1,
        borderColor=colors.HexColor('#333'),
        borderPadding=8,
        spaceBefore=6,
        spaceAfter=6,
        leading=12
    ))

    styles.add(ParagraphStyle(
        name='MDListItem',
        fontName=CJK_FONT,
        fontSize=10,
        textColor=colors.black,
        leftIndent=15,
        spaceBefore=2,
        spaceAfter=2,
        leading=14,
        bulletIndent=5
    ))

    # Table styles
    styles.add(ParagraphStyle(
        name='MDTableHeader',
        fontName=CJK_BOLD,
        fontSize=9,
        textColor=colors.white,
        alignment=TA_CENTER,
        leading=11,
        spaceBefore=0,
        spaceAfter=0
    ))

    styles.add(ParagraphStyle(
        name='MDTableCell',
        fontName=CJK_FONT,
        fontSize=9,
        textColor=colors.black,
        leading=11,
        spaceBefore=0,
        spaceAfter=0
    ))

    return styles


def escape_xml(text):
    """Escape special XML characters for ReportLab Paragraph"""
    if not text:
        return ''
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    return text


def convert_inline_markdown(text):
    """Convert inline markdown (bold, italic, code) to ReportLab XML tags"""
    if not text:
        return ''

    # Escape XML first
    text = escape_xml(text)

    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # Italic: *text* or _text_
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'(?<![_])_([^_]+)_(?![_])', r'<i>\1</i>', text)

    # Inline code: `code`
    teal_hex = TEAL.hexval()
    text = re.sub(r'`([^`]+)`', rf'<font face="{MONO_FONT}" color="{teal_hex}">\1</font>', text)

    return text


def parse_markdown_table(lines, styles, page_width=None):
    """Parse markdown table lines into a ReportLab Table"""
    if not lines:
        return None

    # Parse rows
    data = []
    col_max_chars = [] # Track max content length per column

    for line in lines:
        # Check for separator line (e.g. |---|---|)
        if re.match(r'^\s*\|?\s*:?-+:?\s*(\|?\s*:?-+:?\s*)+\|?\s*$', line):
            continue

        # Split by pipe
        # Remove empty first/last if pipe exists there
        parts = line.strip().split('|')
        if line.strip().startswith('|'):
            parts = parts[1:]
        if line.strip().endswith('|'):
            parts = parts[:-1]

        row_data = []
        for j, part in enumerate(parts):
            cell_text = convert_inline_markdown(part.strip())
            # Use Header style for first row
            style = styles['MDTableHeader'] if len(data) == 0 else styles['MDTableCell']
            row_data.append(Paragraph(cell_text, style))

            # Simple length tracking (strip tags for estimation)
            clean_text = part.strip()
            if j >= len(col_max_chars):
                col_max_chars.append(len(clean_text))
            else:
                col_max_chars[j] = max(col_max_chars[j], len(clean_text))

        if row_data:
            data.append(row_data)

    if not data:
        return None

    # Normalize row lengths — pad short rows with empty cells to match the widest row
    num_cols = len(col_max_chars)
    for row in data:
        while len(row) < num_cols:
            row.append(Paragraph("", styles['MDTableCell']))

    # Calculate column widths
    # Total available width = A4 width - margins (approx 170mm)
    total_width = page_width or (170 * mm)

    if num_cols == 0:
        return None

    # Smart column width: narrow columns (≤5 chars, e.g. O/X/numbers) get fixed
    # small width; remaining space goes to text-heavy columns proportionally.
    NARROW_THRESHOLD = 5   # max chars to classify as "narrow"
    NARROW_WIDTH = 9 * mm  # fixed width for narrow columns
    font_size = 8 if num_cols > 6 else 9

    narrow_cols = set()
    for j in range(num_cols):
        if col_max_chars[j] <= NARROW_THRESHOLD:
            narrow_cols.add(j)

    narrow_total = len(narrow_cols) * NARROW_WIDTH
    wide_remaining = total_width - narrow_total

    # Distribute remaining width among wide columns by content weight
    wide_weights = {}
    for j in range(num_cols):
        if j not in narrow_cols:
            # CJK-aware weight: CJK chars count double
            weight = 0
            for row in data:
                if j < len(row):
                    cell_text = row[j].text if hasattr(row[j], 'text') else ''
                    clean = re.sub(r'<[^>]+>', '', cell_text)
                    cw = sum(2 if ord(ch) > 0x2E80 else 1 for ch in clean)
                    weight = max(weight, cw)
            wide_weights[j] = max(weight, 1)

    total_wide_weight = sum(wide_weights.values()) or 1

    col_widths = []
    for j in range(num_cols):
        if j in narrow_cols:
            col_widths.append(NARROW_WIDTH)
        else:
            w = (wide_weights[j] / total_wide_weight) * wide_remaining
            col_widths.append(max(w, 15 * mm))

    # Safety: if total exceeds page, scale down
    total_calculated = sum(col_widths)
    if total_calculated > total_width:
        scale = total_width / total_calculated
        col_widths = [w * scale for w in col_widths]

    table = Table(data, colWidths=col_widths)

    # Style the table
    # Header row background
    narrow_align_cols = sorted(narrow_cols)
    tbl_style = [
        ('BACKGROUND', (0, 0), (-1, 0), AMBER),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('FONTNAME', (0, 0), (-1, -1), CJK_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), font_size),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]
    # Center-align narrow columns (O/X, numbers)
    for j in narrow_align_cols:
        tbl_style.append(('ALIGN', (j, 1), (j, -1), 'CENTER'))

    table.setStyle(TableStyle(tbl_style))

    return table


def markdown_to_flowables(markdown_text, styles, page_width=None):
    """
    Convert markdown text to ReportLab flowables.

    Supports: headers, bold, italic, code blocks, blockquotes, lists, paragraphs
    """
    if not markdown_text:
        return [Paragraph("No content.", styles['CJKNormal'])]

    flowables = []
    lines = markdown_text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            i += 1
            continue

        # Table
        if stripped.startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1

            table_flowable = parse_markdown_table(table_lines, styles, page_width=page_width)
            if table_flowable:
                flowables.append(table_flowable)
                flowables.append(Spacer(1, 3*mm))
            continue

        # Code block (```)
        if stripped.startswith('```'):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1  # Skip closing ```

            if code_lines:
                code_text = escape_xml('\n'.join(code_lines))
                flowables.append(Preformatted(code_text, styles['MDCode']))
            continue

        # Headers
        if stripped.startswith('#'):
            match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            if match:
                level = len(match.group(1))
                header_text = convert_inline_markdown(match.group(2))
                style_name = f'MDH{min(level, 4)}'
                flowables.append(Paragraph(header_text, styles[style_name]))
                i += 1
                continue

        # Blockquote
        if stripped.startswith('>'):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                quote_lines.append(lines[i].strip()[1:].strip())
                i += 1
            quote_text = convert_inline_markdown(' '.join(quote_lines))
            flowables.append(Paragraph(quote_text, styles['MDBlockquote']))
            continue

        # Unordered list
        if stripped.startswith(('- ', '* ', '+ ')):
            list_items = []
            while i < len(lines):
                l = lines[i].strip()
                if l.startswith(('- ', '* ', '+ ')):
                    item_text = convert_inline_markdown(l[2:])
                    list_items.append(ListItem(Paragraph(item_text, styles['MDListItem'])))
                    i += 1
                elif l and not l.startswith('#') and not l.startswith('>'):
                    # Continuation of previous item
                    if list_items:
                        prev = list_items[-1]
                        prev_text = prev._flowables[0].text if hasattr(prev, '_flowables') else ''
                        list_items[-1] = ListItem(Paragraph(
                            prev_text + ' ' + convert_inline_markdown(l),
                            styles['MDListItem']
                        ))
                    i += 1
                else:
                    break

            if list_items:
                flowables.append(ListFlowable(
                    list_items,
                    bulletType='bullet',
                    bulletColor=TEAL,
                    leftIndent=10
                ))
            continue

        # Ordered list
        if re.match(r'^\d+\.\s', stripped):
            list_items = []
            while i < len(lines):
                l = lines[i].strip()
                match = re.match(r'^\d+\.\s+(.+)$', l)
                if match:
                    item_text = convert_inline_markdown(match.group(1))
                    list_items.append(ListItem(Paragraph(item_text, styles['MDListItem'])))
                    i += 1
                else:
                    break

            if list_items:
                flowables.append(ListFlowable(
                    list_items,
                    bulletType='1',
                    bulletColor=TEAL,
                    leftIndent=10
                ))
            continue

        # Horizontal rule
        if stripped in ('---', '***', '___'):
            flowables.append(Spacer(1, 3*mm))
            hr = Table([['']],colWidths=[170*mm])
            hr.setStyle(TableStyle([
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ]))
            flowables.append(hr)
            flowables.append(Spacer(1, 3*mm))
            i += 1
            continue

        # Regular paragraph
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line or next_line.startswith(('#', '>', '-', '*', '+', '```')) or re.match(r'^\d+\.', next_line):
                break
            para_lines.append(next_line)
            i += 1

        para_text = convert_inline_markdown(' '.join(para_lines))
        flowables.append(Paragraph(para_text, styles['MDParagraph']))

    return flowables if flowables else [Paragraph("No content.", styles['CJKNormal'])]


def parse_inspection_result(response_str):
    """Parse inspection result string to extract is_NG and Description."""
    is_ng = False
    description = response_str or ''

    try:
        data = json.loads(response_str)
        if isinstance(data, dict):
            is_ng = data.get('is_NG', False)
            description = data.get('Description', str(data))
    except (json.JSONDecodeError, TypeError):
        if isinstance(response_str, str):
            is_ng = 'ng' in response_str.lower()

    return is_ng, description


def _build_title_page(story, styles, title_text, subtitle_text=None):
    """Build a title page with optional logo."""
    story.append(Spacer(1, 20*mm))

    # Logo
    logo_path = _find_logo()
    if logo_path:
        try:
            logo = Image(logo_path)
            # Scale to ~15mm height, maintain aspect ratio
            target_h = 15 * mm
            scale = target_h / logo.drawHeight
            logo.drawHeight = target_h
            logo.drawWidth = logo.drawWidth * scale
            logo.hAlign = 'CENTER'
            story.append(logo)
            story.append(Spacer(1, 8*mm))
        except Exception as e:
            logging.warning(f"Failed to load logo: {e}")
            story.append(Spacer(1, 10*mm))
    else:
        story.append(Spacer(1, 10*mm))

    story.append(Paragraph(title_text, styles['ReportTitle']))
    if subtitle_text:
        story.append(Paragraph(subtitle_text, styles['ReportTitle']))


def _build_token_table(story, styles, run_dict, inspections):
    """Build per-category token usage breakdown table."""
    story.append(Paragraph("Token Usage Breakdown (Token \u4f7f\u7528\u660e\u7d30)", styles['SectionHeader']))

    # Aggregate inspection tokens
    insp_in = sum(ins.get('input_tokens', 0) or 0 for ins in inspections)
    insp_out = sum(ins.get('output_tokens', 0) or 0 for ins in inspections)
    insp_total = sum(ins.get('total_tokens', 0) or 0 for ins in inspections)

    report_in = run_dict.get('report_input_tokens', 0) or 0
    report_out = run_dict.get('report_output_tokens', 0) or 0
    report_total = run_dict.get('report_total_tokens', 0) or 0

    tg_in = run_dict.get('telegram_input_tokens', 0) or 0
    tg_out = run_dict.get('telegram_output_tokens', 0) or 0
    tg_total = run_dict.get('telegram_total_tokens', 0) or 0

    vid_in = run_dict.get('video_input_tokens', 0) or 0
    vid_out = run_dict.get('video_output_tokens', 0) or 0
    vid_total = run_dict.get('video_total_tokens', 0) or 0

    grand_in = insp_in + report_in + tg_in + vid_in
    grand_out = insp_out + report_out + tg_out + vid_out
    grand_total = insp_total + report_total + tg_total + vid_total

    def _fmt(n):
        return f"{n:,}"

    header_style = styles['MDTableHeader']
    cell_style = styles['MDTableCell']

    # Build table data
    table_data = [
        [
            Paragraph("Category (\u985e\u5225)", header_style),
            Paragraph("Input Tokens", header_style),
            Paragraph("Output Tokens", header_style),
            Paragraph("Total Tokens", header_style),
        ],
        [
            Paragraph("\u5f71\u50cf\u8fa8\u8b58 (Image Inspection)", cell_style),
            Paragraph(_fmt(insp_in), cell_style),
            Paragraph(_fmt(insp_out), cell_style),
            Paragraph(_fmt(insp_total), cell_style),
        ],
        [
            Paragraph("\u5831\u544a\u751f\u6210 (Report Generation)", cell_style),
            Paragraph(_fmt(report_in), cell_style),
            Paragraph(_fmt(report_out), cell_style),
            Paragraph(_fmt(report_total), cell_style),
        ],
    ]

    # Conditionally add telegram row
    if tg_total > 0:
        table_data.append([
            Paragraph("Telegram \u751f\u6210", cell_style),
            Paragraph(_fmt(tg_in), cell_style),
            Paragraph(_fmt(tg_out), cell_style),
            Paragraph(_fmt(tg_total), cell_style),
        ])

    # Conditionally add video row
    if vid_total > 0:
        table_data.append([
            Paragraph("\u5f71\u7247\u5206\u6790 (Video Analysis)", cell_style),
            Paragraph(_fmt(vid_in), cell_style),
            Paragraph(_fmt(vid_out), cell_style),
            Paragraph(_fmt(vid_total), cell_style),
        ])

    # Grand total row (bold)
    bold_cell = ParagraphStyle('BoldCell', parent=cell_style, fontName=CJK_BOLD)
    table_data.append([
        Paragraph("<b>\u5408\u8a08 (Grand Total)</b>", bold_cell),
        Paragraph(f"<b>{_fmt(grand_in)}</b>", bold_cell),
        Paragraph(f"<b>{_fmt(grand_out)}</b>", bold_cell),
        Paragraph(f"<b>{_fmt(grand_total)}</b>", bold_cell),
    ])

    col_widths = [55*mm, 38*mm, 38*mm, 38*mm]
    token_table = Table(table_data, colWidths=col_widths)

    last_row = len(table_data) - 1
    tbl_style = [
        ('BACKGROUND', (0, 0), (-1, 0), AMBER),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('FONTNAME', (0, 0), (-1, -1), CJK_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        # Bold total row with light amber background
        ('BACKGROUND', (0, last_row), (-1, last_row), colors.HexColor('#fdf6e3')),
        ('FONTNAME', (0, last_row), (-1, last_row), CJK_BOLD),
        ('LINEABOVE', (0, last_row), (-1, last_row), 1, AMBER),
    ]
    token_table.setStyle(TableStyle(tbl_style))
    story.append(token_table)
    story.append(Spacer(1, 10*mm))


def _build_analysis_token_table(story, styles, period_tokens, report_tokens):
    """Build token usage summary table for analysis reports."""
    story.append(Paragraph("Token Usage Summary (Token \u4f7f\u7528\u6458\u8981)", styles['SectionHeader']))

    def _fmt(n):
        return f"{n:,}"

    header_style = styles['MDTableHeader']
    cell_style = styles['MDTableCell']
    bold_cell = ParagraphStyle('BoldCell', parent=cell_style, fontName=CJK_BOLD)

    table_data = [
        [
            Paragraph("Category (\u985e\u5225)", header_style),
            Paragraph("Input Tokens", header_style),
            Paragraph("Output Tokens", header_style),
            Paragraph("Total Tokens", header_style),
        ],
        [
            Paragraph("\u5de1\u6aa2\u671f\u9593\u6d88\u8017 (Patrol Period)", cell_style),
            Paragraph(_fmt(period_tokens.get('input', 0)), cell_style),
            Paragraph(_fmt(period_tokens.get('output', 0)), cell_style),
            Paragraph(_fmt(period_tokens.get('total', 0)), cell_style),
        ],
        [
            Paragraph("\u672c\u5831\u544a\u751f\u6210 (This Report)", cell_style),
            Paragraph(_fmt(report_tokens.get('input', 0)), cell_style),
            Paragraph(_fmt(report_tokens.get('output', 0)), cell_style),
            Paragraph(_fmt(report_tokens.get('total', 0)), cell_style),
        ],
    ]

    # Grand total
    grand_in = period_tokens.get('input', 0) + report_tokens.get('input', 0)
    grand_out = period_tokens.get('output', 0) + report_tokens.get('output', 0)
    grand_total = period_tokens.get('total', 0) + report_tokens.get('total', 0)

    table_data.append([
        Paragraph("<b>\u5408\u8a08 (Grand Total)</b>", bold_cell),
        Paragraph(f"<b>{_fmt(grand_in)}</b>", bold_cell),
        Paragraph(f"<b>{_fmt(grand_out)}</b>", bold_cell),
        Paragraph(f"<b>{_fmt(grand_total)}</b>", bold_cell),
    ])

    col_widths = [55*mm, 38*mm, 38*mm, 38*mm]
    token_table = Table(table_data, colWidths=col_widths)

    last_row = len(table_data) - 1
    tbl_style = [
        ('BACKGROUND', (0, 0), (-1, 0), AMBER),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('FONTNAME', (0, 0), (-1, -1), CJK_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BACKGROUND', (0, last_row), (-1, last_row), colors.HexColor('#fdf6e3')),
        ('FONTNAME', (0, last_row), (-1, last_row), CJK_BOLD),
        ('LINEABOVE', (0, last_row), (-1, last_row), 1, AMBER),
    ]
    token_table.setStyle(TableStyle(tbl_style))
    story.append(token_table)
    story.append(Spacer(1, 10*mm))


def generate_analysis_report(content, start_date, end_date, period_tokens=None, report_tokens=None):
    """
    Generate PDF from markdown content for multi-day analysis report.
    Uses landscape orientation to accommodate wide inspection tables.

    Args:
        content: Markdown formatted report content
        start_date: Report period start date
        end_date: Report period end date
        period_tokens: dict with input/output/total for patrol runs in the period
        report_tokens: dict with input/output/total for this report's generation

    Returns:
        PDF bytes
    """
    buffer = io.BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=25*mm
    )

    styles = get_styles()
    story = []

    # === Title Page ===
    _build_title_page(story, styles, "VISUAL PATROL", "Analysis Report")
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(
        f"Report Period: {start_date} to {end_date}",
        styles['CJKNormalCenter']
    ))
    story.append(Paragraph(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        styles['SmallText']
    ))
    story.append(Spacer(1, 20*mm))

    # === Token Usage Summary ===
    if period_tokens or report_tokens:
        _build_analysis_token_table(
            story, styles,
            period_tokens or {'input': 0, 'output': 0, 'total': 0},
            report_tokens or {'input': 0, 'output': 0, 'total': 0}
        )

    # === Report Content (Markdown) ===
    story.append(Paragraph("Analysis Report", styles['SectionHeader']))

    # Available content width = page width - left margin - right margin
    content_width = page_size[0] - 40 * mm

    if content:
        md_flowables = markdown_to_flowables(content, styles, page_width=content_width)
        story.extend(md_flowables)
    else:
        story.append(Paragraph("No content.", styles['CJKNormal']))

    # Page number callback
    def add_page_number(canvas, doc):
        canvas.saveState()
        page_num = canvas.getPageNumber()
        canvas.setFont(CJK_FONT, 8)
        canvas.setFillColor(MUTED_TEXT)
        canvas.drawCentredString(page_size[0] / 2, 15*mm, f"Page {page_num}")
        canvas.drawCentredString(page_size[0] / 2, 10*mm, f"VISUAL PATROL System - Analysis Report ({start_date} to {end_date})")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)

    pdf_bytes = buffer.getvalue()
    buffer.close()

    return pdf_bytes


def generate_patrol_report(run_id):
    """Generate a PDF report for a patrol run with markdown support."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM patrol_runs WHERE id = ?', (run_id,))
    run = cursor.fetchone()

    if not run:
        conn.close()
        raise ValueError(f"Patrol run #{run_id} not found")

    run_dict = dict(run)

    cursor.execute('SELECT * FROM inspection_results WHERE run_id = ? ORDER BY id', (run_id,))
    inspections = [dict(row) for row in cursor.fetchall()]
    conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20*mm,
        leftMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=25*mm
    )

    styles = get_styles()
    story = []

    # === Title Page ===
    _build_title_page(story, styles, "VISUAL PATROL REPORT")
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph(f"Report #{run_id}", styles['CJKNormalCenter']))
    story.append(Paragraph(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        styles['SmallText']
    ))
    story.append(Spacer(1, 20*mm))

    # === Patrol Information ===
    story.append(Paragraph("Patrol Information", styles['SectionHeader']))

    # Calculate duration
    duration_str = 'N/A'
    start_time_str = run_dict.get('start_time')
    end_time_str = run_dict.get('end_time')
    if start_time_str and end_time_str:
        try:
            fmt = '%Y-%m-%d %H:%M:%S'
            start_dt = datetime.strptime(start_time_str, fmt)
            end_dt = datetime.strptime(end_time_str, fmt)
            delta = end_dt - start_dt
            total_secs = int(delta.total_seconds())
            mins, secs = divmod(total_secs, 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                duration_str = f"{hours}h {mins}m {secs}s"
            elif mins > 0:
                duration_str = f"{mins}m {secs}s"
            else:
                duration_str = f"{secs}s"
        except (ValueError, TypeError):
            pass

    info_data = [
        ['Start Time:', start_time_str or 'N/A'],
        ['End Time:', end_time_str or 'N/A'],
        ['Duration:', duration_str],
        ['Robot Serial:', run_dict.get('robot_serial', 'N/A')],
        ['AI Model:', run_dict.get('model_id', 'N/A')],
    ]

    info_table = Table(info_data, colWidths=[35*mm, 120*mm])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), CJK_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), MUTED_TEXT),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.black),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 10*mm))

    # === Token Usage Breakdown ===
    _build_token_table(story, styles, run_dict, inspections)

    # === AI Summary Report (Markdown) ===
    story.append(Paragraph("AI Summary Report", styles['SectionHeader']))

    report_content = run_dict.get('report_content', '')
    if report_content:
        md_flowables = markdown_to_flowables(report_content, styles)
        story.extend(md_flowables)
    else:
        story.append(Paragraph("No report generated.", styles['CJKNormal']))

    story.append(Spacer(1, 10*mm))

    # === Video Analysis ===
    video_analysis = run_dict.get('video_analysis')
    if video_analysis:
        story.append(Paragraph("Video Analysis (影片分析)", styles['SectionHeader']))
        va_flowables = markdown_to_flowables(video_analysis, styles)
        story.extend(va_flowables)
        story.append(Spacer(1, 10*mm))

    # === Inspection Points ===
    story.append(Paragraph(f"Inspection Points ({len(inspections)})", styles['SectionHeader']))

    teal_hex = TEAL.hexval()
    ok_hex = OK_GREEN.hexval()
    ng_hex = NG_RED.hexval()

    if not inspections:
        story.append(Paragraph("No inspection records.", styles['CJKNormal']))
    else:
        for ins in inspections:
            inspection_elements = []

            is_ng, description = parse_inspection_result(ins.get('ai_response', ''))
            status_color = NG_RED if is_ng else OK_GREEN
            status_text = 'NG' if is_ng else 'OK'

            point_name = ins.get('point_name', 'Unknown Point')
            inspection_elements.append(Paragraph(
                f"<font color='{teal_hex}'>{escape_xml(point_name)}</font> "
                f"<font color='{status_color.hexval()}'>[{status_text}]</font>",
                styles['PointName']
            ))

            timestamp = ins.get('timestamp', 'N/A')
            coord_x = ins.get('coordinate_x')
            coord_y = ins.get('coordinate_y')
            coord_str = f"({coord_x:.2f}, {coord_y:.2f})" if coord_x is not None else "N/A"

            inspection_elements.append(Paragraph(
                f"Time: {timestamp} | Coordinates: {coord_str}",
                styles['SmallText']
            ))

            image_path = ins.get('image_path')
            if image_path:
                # Try robot-specific dir, then cross-robot dir, then legacy dir
                full_image_path = os.path.join(ROBOT_IMAGES_DIR, image_path)
                if not os.path.exists(full_image_path):
                    rid = ins.get('robot_id')
                    if rid:
                        full_image_path = os.path.join(DATA_DIR, rid, "report", "images", image_path)
                if not os.path.exists(full_image_path):
                    full_image_path = os.path.join(_LEGACY_IMAGES_DIR, image_path)
                if os.path.exists(full_image_path):
                    try:
                        img = Image(full_image_path)
                        max_width = 140*mm
                        max_height = 80*mm
                        width_ratio = max_width / img.drawWidth
                        height_ratio = max_height / img.drawHeight
                        scale = min(width_ratio, height_ratio, 1.0)
                        img.drawWidth *= scale
                        img.drawHeight *= scale
                        inspection_elements.append(Spacer(1, 3*mm))
                        inspection_elements.append(img)
                    except Exception as e:
                        logging.warning(f"Failed to load image {full_image_path}: {e}")

            inspection_elements.append(Spacer(1, 3*mm))
            prompt = ins.get('prompt', 'N/A')
            inspection_elements.append(Paragraph(
                f"<b>Prompt:</b> {escape_xml(prompt)}",
                styles['CJKNormal']
            ))

            result_color = ng_hex if is_ng else ok_hex
            inspection_elements.append(Paragraph(
                f"<font color='{result_color}'><b>Result:</b></font>",
                styles['CJKNormal']
            ))
            inspection_elements.extend(markdown_to_flowables(description, styles))

            inspection_elements.append(Spacer(1, 5*mm))

            line_table = Table([['']],colWidths=[170*mm])
            line_table.setStyle(TableStyle([
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ]))
            inspection_elements.append(line_table)
            inspection_elements.append(Spacer(1, 3*mm))

            story.append(KeepTogether(inspection_elements[:4]))
            story.extend(inspection_elements[4:])

    def add_page_number(canvas, doc):
        canvas.saveState()
        page_num = canvas.getPageNumber()
        canvas.setFont(CJK_FONT, 8)
        canvas.setFillColor(MUTED_TEXT)
        canvas.drawCentredString(A4[0] / 2, 15*mm, f"Page {page_num}")
        canvas.drawCentredString(A4[0] / 2, 10*mm, f"VISUAL PATROL System - Report #{run_id}")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)

    pdf_bytes = buffer.getvalue()
    buffer.close()

    return pdf_bytes
