"""
ODIN — Business Plan PDF Generator
Takes the output of all 4 business agents (scout, income, builder, auditor)
and produces a professional multi-page PDF business plan.

Usage:
    from utils.business_plan_pdf import generate
    path = generate(business_name, scout_data, income_data, build_data, audit_data, for_user='brock')

Output: ODIN/business_plans/YYYY-MM-DD_<slug>.pdf
"""

import os
from datetime import date

try:
    from fpdf import FPDF, XPos, YPos
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False
    # Stub classes so module-level class definitions don't crash at import
    class FPDF:
        pass
    class XPos:
        LMARGIN = RIGHT = None
    class YPos:
        TOP = NEXT = None

# ── Palette ──────────────────────────────────────────────────────────────────
NAVY        = (18,  40,  76)
BLUE        = (30,  90, 180)
ORANGE      = (210, 95,  20)
GREEN       = (28, 130,  75)
RED         = (190, 40,  40)
GRAY        = (70,  80,  95)
MID         = (120, 130, 145)
LIGHT_BG    = (240, 244, 250)
STRIP       = (228, 235, 245)
WHITE       = (255, 255, 255)
KATELYN_PK  = (180,  60, 120)   # pink accent for Katelyn plans

PAGE_W  = 215.9   # Letter
LEFT_M  = 16
RIGHT_M = 16
INNER_W = PAGE_W - LEFT_M - RIGHT_M


# ── Helpers ───────────────────────────────────────────────────────────────────
def _rgb(pdf, color):   pdf.set_text_color(*color)
def _fill(pdf, color):  pdf.set_fill_color(*color)
def _draw(pdf, color):  pdf.set_draw_color(*color)
def _slug(s):           return s.lower().replace(' ', '_').replace('/', '-')[:40]


class _Doc(FPDF):
    def __init__(self, accent):
        super().__init__()
        self.accent = accent

    def header(self): pass

    def footer(self):
        self.set_y(-13)
        _fill(self, NAVY)
        self.rect(0, self.get_y(), PAGE_W, 13, style='F')
        self.set_y(-11)
        _rgb(self, (180, 195, 220))
        self.set_font('Helvetica', '', 7.5)
        self.set_x(LEFT_M)
        self.cell(INNER_W / 2, 5, 'ODIN  ·  Shue Box LLC  ·  Confidential',
                  new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.cell(INNER_W / 2, 5, f'Page {self.page_no()}', align='R',
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _new_page(pdf):
    pdf.add_page()
    pdf.set_left_margin(LEFT_M)
    pdf.set_right_margin(RIGHT_M)
    pdf.set_x(LEFT_M)


def _divider(pdf):
    _draw(pdf, STRIP)
    pdf.set_line_width(0.35)
    y = pdf.get_y()
    pdf.line(LEFT_M, y, PAGE_W - RIGHT_M, y)
    pdf.ln(3)


def _section_bar(pdf, icon, title, accent=None):
    pdf.ln(5)
    color = accent or NAVY
    _fill(pdf, color)
    pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 10, style='F')
    pdf.set_x(LEFT_M + 4)
    _rgb(pdf, WHITE)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(INNER_W - 4, 10, f'{icon}  {title}',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)
    _rgb(pdf, GRAY)
    pdf.set_font('Helvetica', '', 10)


def _badge(pdf, label, color):
    _fill(pdf, color)
    _rgb(pdf, WHITE)
    pdf.set_font('Helvetica', 'B', 6.5)
    pdf.cell(22, 5, label, align='C', fill=True,
             new_x=XPos.RIGHT, new_y=YPos.TOP)
    _rgb(pdf, GRAY)
    pdf.set_font('Helvetica', '', 10)


def _bullet(pdf, text, indent=4):
    pdf.set_x(LEFT_M + indent)
    _rgb(pdf, GRAY)
    pdf.set_font('Helvetica', '', 9.5)
    pdf.multi_cell(INNER_W - indent, 5, f'\u2022  {text}',
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _kv(pdf, label, value, label_w=55):
    pdf.set_x(LEFT_M)
    _rgb(pdf, NAVY)
    pdf.set_font('Helvetica', 'B', 9.5)
    pdf.cell(label_w, 5.5, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
    _rgb(pdf, GRAY)
    pdf.set_font('Helvetica', '', 9.5)
    pdf.multi_cell(INNER_W - label_w, 5.5, str(value),
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ── Page builders ─────────────────────────────────────────────────────────────

def _cover(pdf, business_name, for_user, accent):
    _new_page(pdf)

    # Top bar
    _fill(pdf, NAVY)
    pdf.rect(0, 0, PAGE_W, 42, style='F')
    _rgb(pdf, WHITE)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_y(10)
    pdf.cell(PAGE_W, 6, 'ODIN  ·  BUSINESS PLAN', align='C',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Big title
    pdf.ln(30)
    _rgb(pdf, NAVY)
    pdf.set_font('Helvetica', 'B', 26)
    pdf.set_x(LEFT_M)
    pdf.multi_cell(INNER_W, 12, business_name.title(),
                   align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(4)
    _fill(pdf, accent)
    pdf.rect(LEFT_M + 40, pdf.get_y(), INNER_W - 80, 1.5, style='F')
    pdf.ln(8)

    _rgb(pdf, MID)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_x(LEFT_M)
    pdf.cell(INNER_W, 6,
             f'Prepared by ODIN for {for_user.title()}  ·  {date.today().strftime("%B %d, %Y")}',
             align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(20)
    _divider(pdf)

    # Tagline
    pdf.ln(6)
    _rgb(pdf, GRAY)
    pdf.set_font('Helvetica', 'I', 10)
    pdf.set_x(LEFT_M)
    pdf.multi_cell(INNER_W, 5.5,
        'This plan was researched, modeled, and written by ODIN — your AI operating system. '
        'It covers market viability, revenue projections, step-by-step launch tasks, '
        'and the full automation map of what ODIN can build for this business.',
        align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _executive_summary(pdf, business_name, scout_data, income_data, accent):
    _new_page(pdf)
    _section_bar(pdf, '\U0001f4cb', 'Executive Summary', accent)

    # Pull the matching idea from scout
    ideas = scout_data.get('ideas', [])
    idea  = next((i for i in ideas
                  if i.get('name','').lower() in business_name.lower()
                  or business_name.lower() in i.get('name','').lower()), ideas[0] if ideas else {})

    score = idea.get('viability_score', '—')
    auto  = idea.get('automation_potential', '—')
    comp  = idea.get('competition', '—')
    dem   = idea.get('market_demand', '—')
    cost  = idea.get('startup_cost_usd', '—')

    # Score banner
    pdf.ln(2)
    _fill(pdf, LIGHT_BG)
    pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 22, style='F')
    pdf.set_x(LEFT_M + 6)
    pdf.ln(3)
    _rgb(pdf, NAVY)
    pdf.set_font('Helvetica', 'B', 22)
    pdf.cell(35, 12, f'{score}/10', new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_x(LEFT_M + 44)
    pdf.set_font('Helvetica', 'B', 10)
    _rgb(pdf, GRAY)
    pdf.cell(40, 6, f'Automation: {auto}%', new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(40, 6, f'Demand: {dem}', new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(40, 6, f'Competition: {comp}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(LEFT_M + 44)
    pdf.cell(40, 6, f'Startup: ${cost}', new_x=XPos.RIGHT, new_y=YPos.TOP)
    s = income_data.get('scenarios', {}).get('realistic', {})
    pdf.cell(60, 6, f'Realistic M12: ${s.get("month_12", 0):,.0f}/mo',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    # Recommendation
    rec = scout_data.get('recommendation', '')
    summary = idea.get('summary', '')
    if summary:
        _rgb(pdf, NAVY)
        pdf.set_x(LEFT_M)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(INNER_W, 6, 'Overview', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        _rgb(pdf, GRAY)
        pdf.set_font('Helvetica', '', 9.5)
        pdf.set_x(LEFT_M)
        pdf.multi_cell(INNER_W, 5.5, summary, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

    if rec:
        _rgb(pdf, NAVY)
        pdf.set_x(LEFT_M)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(INNER_W, 6, "ODIN's Recommendation", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        _rgb(pdf, GRAY)
        pdf.set_font('Helvetica', 'I', 9.5)
        pdf.set_x(LEFT_M)
        pdf.multi_cell(INNER_W, 5.5, rec, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Key risks & opportunities
    pdf.ln(3)
    _divider(pdf)
    if income_data.get('biggest_risk'):
        _kv(pdf, '\u26a0\ufe0f  Biggest Risk', income_data['biggest_risk'])
    if income_data.get('biggest_opportunity'):
        _kv(pdf, '\U0001f680  Best Opportunity', income_data['biggest_opportunity'])
    if income_data.get('break_even_month'):
        _kv(pdf, '\U0001f4c5  Break-Even', f'Month {income_data["break_even_month"]}')
    if income_data.get('monthly_costs') is not None:
        _kv(pdf, '\U0001f4b8  Monthly Costs', f'~${income_data["monthly_costs"]:,.0f}')


def _revenue_projections(pdf, income_data, accent):
    _new_page(pdf)
    _section_bar(pdf, '\U0001f4b0', 'Revenue Projections', accent)

    s = income_data.get('scenarios', {})
    c = s.get('conservative', {})
    r = s.get('realistic', {})
    o = s.get('optimistic', {})

    months = [(1,'month_1'), (3,'month_3'), (6,'month_6'), (12,'month_12')]

    # Table header
    pdf.ln(2)
    _fill(pdf, NAVY)
    pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 8, style='F')
    _rgb(pdf, WHITE)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_x(LEFT_M)
    col_w = INNER_W / 4
    for h in ['Month', 'Conservative', 'Realistic', 'Optimistic']:
        pdf.cell(col_w, 8, h, align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.ln(8)

    for i, (month, key) in enumerate(months):
        _fill(pdf, LIGHT_BG if i % 2 == 0 else WHITE)
        pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 7, style='F')
        _rgb(pdf, NAVY)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_x(LEFT_M)
        pdf.cell(col_w, 7, f'Month {month}', align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
        _rgb(pdf, GRAY)
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(col_w, 7, f'${c.get(key, 0):,.0f}', align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
        _rgb(pdf, GREEN)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(col_w, 7, f'${r.get(key, 0):,.0f}', align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
        _rgb(pdf, BLUE)
        pdf.cell(col_w, 7, f'${o.get(key, 0):,.0f}', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Annual row
    pdf.ln(1)
    _fill(pdf, NAVY)
    pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 8, style='F')
    _rgb(pdf, WHITE)
    pdf.set_font('Helvetica', 'B', 9.5)
    pdf.set_x(LEFT_M)
    pdf.cell(col_w, 8, 'Annual Total', align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(col_w, 8, f'${c.get("annual",0):,.0f}', align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
    _fill(pdf, GREEN)
    pdf.rect(LEFT_M + col_w*2, pdf.get_y()-8, col_w, 8, style='F')
    pdf.cell(col_w, 8, f'${r.get("annual",0):,.0f}', align='C', new_x=XPos.RIGHT, new_y=YPos.TOP)
    _fill(pdf, NAVY)
    pdf.cell(col_w, 8, f'${o.get("annual",0):,.0f}', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(6)
    _section_bar(pdf, '\U0001f9e0', 'Key Assumptions', accent)
    for assumption in income_data.get('key_assumptions', []):
        _bullet(pdf, assumption)


def _launch_roadmap(pdf, build_data, accent):
    _new_page(pdf)
    _section_bar(pdf, '\U0001f6e3\ufe0f', 'Launch Roadmap', accent)

    # Accounts
    accounts = build_data.get('accounts_to_create', [])
    if accounts:
        pdf.ln(1)
        _rgb(pdf, NAVY)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(INNER_W, 6, 'Accounts to Create', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for a in sorted(accounts, key=lambda x: x.get('priority', 5)):
            cost_str = 'Free' if a.get('free') else f'${a.get("monthly_cost_usd",0):.0f}/mo'
            pdf.set_x(LEFT_M)
            _rgb(pdf, NAVY)
            pdf.set_font('Helvetica', 'B', 9)
            pdf.cell(6, 5.5, f'{a.get("priority","·")}.',
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.set_font('Helvetica', 'B', 9)
            pdf.cell(50, 5.5, a.get('platform',''), new_x=XPos.RIGHT, new_y=YPos.TOP)
            _rgb(pdf, GRAY)
            pdf.set_font('Helvetica', '', 9)
            pdf.cell(95, 5.5, a.get('purpose',''), new_x=XPos.RIGHT, new_y=YPos.TOP)
            _rgb(pdf, GREEN if a.get('free') else ORANGE)
            pdf.set_font('Helvetica', 'B', 8.5)
            pdf.cell(25, 5.5, cost_str, align='R',
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

    # Setup tasks
    setup = build_data.get('setup_tasks', [])
    if setup:
        _divider(pdf)
        total_h = build_data.get('total_setup_hours', '?')
        odin_n  = sum(1 for t in setup if t.get('can_odin_automate'))
        _rgb(pdf, NAVY)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_x(LEFT_M)
        pdf.cell(INNER_W, 6,
                 f'Setup Tasks  ({total_h}h total  ·  \U0001f916 {odin_n} ODIN  ·  \U0001f464 {len(setup)-odin_n} You)',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)
        for t in setup:
            is_odin = t.get('can_odin_automate', False)
            pdf.set_x(LEFT_M)
            _rgb(pdf, NAVY)
            pdf.set_font('Helvetica', 'B', 9)
            pdf.cell(8, 5.5, f'{t.get("step","·")}.',
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            badge_color = GREEN if is_odin else ORANGE
            _badge(pdf, 'ODIN' if is_odin else 'YOU', badge_color)
            pdf.set_x(pdf.get_x() + 2)
            _rgb(pdf, GRAY)
            pdf.set_font('Helvetica', '', 9)
            avail = INNER_W - 8 - 22 - 2 - 18
            pdf.cell(avail, 5.5, t.get('task',''),
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            _rgb(pdf, MID)
            pdf.cell(18, 5.5, f'{t.get("hours",0)}h', align='R',
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if t.get('notes'):
                pdf.set_x(LEFT_M + 12)
                _rgb(pdf, MID)
                pdf.set_font('Helvetica', 'I', 8)
                pdf.multi_cell(INNER_W - 12, 4.5, t['notes'],
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Week 1 priorities
    w1 = build_data.get('week_1_priority_actions', [])
    if w1:
        pdf.ln(3)
        _divider(pdf)
        _rgb(pdf, accent)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_x(LEFT_M)
        pdf.cell(INNER_W, 6, '\U0001f3af  Week 1 — Do These First',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for i, action in enumerate(w1, 1):
            pdf.set_x(LEFT_M + 4)
            _rgb(pdf, NAVY)
            pdf.set_font('Helvetica', 'B', 9.5)
            pdf.cell(8, 5.5, f'{i}.', new_x=XPos.RIGHT, new_y=YPos.TOP)
            _rgb(pdf, GRAY)
            pdf.set_font('Helvetica', '', 9.5)
            pdf.multi_cell(INNER_W - 12, 5.5, action,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _automation_map(pdf, audit_data, accent):
    _new_page(pdf)
    _section_bar(pdf, '\U0001f916', 'ODIN Automation Map', accent)

    score = audit_data.get('automation_score', 0)
    bar   = '\u2588' * (score // 10) + '\u2591' * (10 - score // 10)

    pdf.ln(2)
    _fill(pdf, LIGHT_BG)
    pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 14, style='F')
    pdf.set_x(LEFT_M + 6)
    pdf.ln(3)
    _rgb(pdf, NAVY)
    pdf.set_font('Helvetica', 'B', 20)
    pdf.cell(35, 10, f'{score}/100', new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_x(pdf.get_x() + 4)
    _rgb(pdf, BLUE)
    pdf.set_font('Courier', 'B', 11)
    pdf.cell(70, 10, bar, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.ln(14)

    autos  = audit_data.get('automatable_touchpoints', [])
    high   = [a for a in autos if a.get('impact') == 'high']
    medium = [a for a in autos if a.get('impact') == 'medium']
    low    = [a for a in autos if a.get('impact') == 'low']

    def _auto_row(a, color):
        pdf.set_x(LEFT_M)
        _rgb(pdf, color)
        pdf.set_font('Helvetica', 'B', 9.5)
        pdf.cell(INNER_W - 28, 5.5, a.get('touchpoint',''),
                 new_x=XPos.RIGHT, new_y=YPos.TOP)
        _rgb(pdf, MID)
        pdf.set_font('Helvetica', '', 8.5)
        pdf.cell(28, 5.5, f'{a.get("estimated_build_hours","?")}h build', align='R',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(LEFT_M + 4)
        _rgb(pdf, GRAY)
        pdf.set_font('Helvetica', '', 8.5)
        trigger = a.get('trigger','')
        tool    = a.get('tool','')
        script  = a.get('script_name','')
        if trigger:
            pdf.multi_cell(INNER_W - 4, 4.5,
                           f'Trigger: {trigger}  |  Tool: {tool}  |  Script: {script}',
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

    if high:
        pdf.set_x(LEFT_M)
        _rgb(pdf, RED)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(INNER_W, 6, '\U0001f534  High Impact — Build These First',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for a in high:
            _auto_row(a, NAVY)

    if medium:
        pdf.ln(2)
        _divider(pdf)
        pdf.set_x(LEFT_M)
        _rgb(pdf, ORANGE)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(INNER_W, 6, '\U0001f7e1  Medium Impact',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for a in medium:
            _auto_row(a, GRAY)

    if low:
        pdf.ln(2)
        _divider(pdf)
        pdf.set_x(LEFT_M)
        _rgb(pdf, MID)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(INNER_W, 6, '\u26aa  Lower Priority',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for a in low:
            _auto_row(a, MID)

    # Manual tasks
    manual = audit_data.get('manual_tasks', [])
    if manual:
        pdf.ln(3)
        _divider(pdf)
        pdf.set_x(LEFT_M)
        _rgb(pdf, NAVY)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(INNER_W, 6, '\U0001f464  Requires You (cannot automate)',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for m in manual:
            _bullet(pdf, f'{m.get("task","")} — {m.get("reason","")}')

    # Build summary
    pdf.ln(4)
    _fill(pdf, LIGHT_BG)
    total_h = audit_data.get('total_build_hours', '?')
    scripts = audit_data.get('scripts_to_build', [])
    top_auto = audit_data.get('highest_impact_automation', '')
    pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 8, style='F')
    _rgb(pdf, NAVY)
    pdf.set_font('Helvetica', 'B', 9.5)
    pdf.set_x(LEFT_M + 4)
    pdf.cell(INNER_W - 4, 8,
             f'Build Summary: {len(autos)} automations  ·  {total_h} total hours  ·  {len(scripts)} scripts',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)
    if top_auto:
        _kv(pdf, '\u2605  Start With', top_auto)


def _odin_future(pdf, build_data, audit_data, accent):
    """What ODIN CAN do now vs what ODIN COULD do (future potential)."""
    _new_page(pdf)
    _section_bar(pdf, '\u26a1', 'What ODIN Does For This Business', accent)

    # NOW — ODIN automatable tasks from builder
    now_tasks = [t for t in build_data.get('setup_tasks', [])
                 if t.get('can_odin_automate')]
    now_tasks += [t for t in build_data.get('recurring_tasks', [])
                  if t.get('who') == 'ODIN']

    if now_tasks:
        pdf.ln(1)
        _fill(pdf, GREEN)
        pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 7, style='F')
        _rgb(pdf, WHITE)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_x(LEFT_M + 4)
        pdf.cell(INNER_W - 4, 7, '\u2705  ODIN CAN DO THIS NOW',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)
        for t in now_tasks:
            pdf.set_x(LEFT_M)
            _rgb(pdf, GREEN)
            pdf.set_font('Helvetica', 'B', 9)
            label = t.get('task', t.get('task', ''))
            pdf.cell(INNER_W - 20, 5.5, f'\u2714  {label}',
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            freq = t.get('frequency', '')
            _rgb(pdf, MID)
            pdf.set_font('Helvetica', '', 8.5)
            pdf.cell(20, 5.5, freq, align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if t.get('notes'):
                pdf.set_x(LEFT_M + 8)
                _rgb(pdf, MID)
                pdf.set_font('Helvetica', 'I', 8)
                pdf.multi_cell(INNER_W - 8, 4, t['notes'],
                               new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # FUTURE — scripts from auditor
    scripts = audit_data.get('scripts_to_build', [])
    autos   = audit_data.get('automatable_touchpoints', [])

    if scripts or autos:
        pdf.ln(4)
        _fill(pdf, ORANGE)
        pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 7, style='F')
        _rgb(pdf, WHITE)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_x(LEFT_M + 4)
        pdf.cell(INNER_W - 4, 7, '\U0001f527  ODIN COULD BUILD (Future Potential)',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

        high_future = [a for a in autos if a.get('impact') == 'high']
        for a in high_future:
            pdf.set_x(LEFT_M)
            _rgb(pdf, ORANGE)
            pdf.set_font('Helvetica', 'B', 9)
            pdf.cell(INNER_W - 20, 5.5, f'\u25b6  {a.get("touchpoint","")}',
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            _rgb(pdf, MID)
            pdf.set_font('Helvetica', '', 8.5)
            pdf.cell(20, 5.5, f'{a.get("estimated_build_hours","?")}h',
                     align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_x(LEFT_M + 8)
            _rgb(pdf, MID)
            pdf.set_font('Helvetica', 'I', 8)
            pdf.multi_cell(INNER_W - 8, 4,
                           f'Tool: {a.get("tool","")}  ·  Script: {a.get("script_name","")}',
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        if scripts:
            pdf.ln(3)
            _rgb(pdf, NAVY)
            pdf.set_font('Helvetica', 'B', 9.5)
            pdf.set_x(LEFT_M)
            pdf.cell(INNER_W, 6, 'Scripts ODIN Would Create:',
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            for s in scripts:
                pdf.set_x(LEFT_M + 6)
                _rgb(pdf, BLUE)
                pdf.set_font('Courier', '', 9)
                pdf.cell(INNER_W - 6, 5, s,
                         new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _sop_checklist(pdf, build_data, accent):
    """Final SOP / launch checklist page."""
    _new_page(pdf)
    _section_bar(pdf, '\U0001f4cb', 'SOP — Launch Checklist', accent)

    pdf.ln(2)
    _rgb(pdf, GRAY)
    pdf.set_font('Helvetica', 'I', 9.5)
    pdf.set_x(LEFT_M)
    pdf.cell(INNER_W, 6,
             'Check off each item in order. ODIN items are automated once configured.',
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    all_tasks = []
    for t in build_data.get('setup_tasks', []):
        all_tasks.append({
            'step':   t.get('step', ''),
            'label':  t.get('task', ''),
            'who':    'ODIN' if t.get('can_odin_automate') else 'YOU',
            'hours':  t.get('hours', 0),
            'phase':  'Setup',
        })
    for t in build_data.get('recurring_tasks', []):
        all_tasks.append({
            'step':   '—',
            'label':  t.get('task', ''),
            'who':    t.get('who', 'you').upper(),
            'hours':  t.get('hours', 0),
            'phase':  f'Ongoing ({t.get("frequency","")})',
        })

    current_phase = None
    for t in all_tasks:
        if t['phase'] != current_phase:
            current_phase = t['phase']
            pdf.ln(3)
            _fill(pdf, STRIP)
            pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 6.5, style='F')
            _rgb(pdf, NAVY)
            pdf.set_font('Helvetica', 'B', 9)
            pdf.set_x(LEFT_M + 3)
            pdf.cell(INNER_W - 3, 6.5, current_phase,
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        is_odin = t['who'] == 'ODIN'
        # Checkbox
        pdf.set_x(LEFT_M)
        _draw(pdf, MID)
        pdf.set_line_width(0.3)
        y = pdf.get_y() + 1
        pdf.rect(LEFT_M, y, 4, 4)
        # Badge
        pdf.set_x(LEFT_M + 6)
        _badge(pdf, t['who'], GREEN if is_odin else ORANGE)
        pdf.set_x(pdf.get_x() + 2)
        _rgb(pdf, GRAY)
        pdf.set_font('Helvetica', '', 9)
        avail = INNER_W - 6 - 22 - 2 - 16
        pdf.cell(avail, 5.5, str(t['label']),
                 new_x=XPos.RIGHT, new_y=YPos.TOP)
        _rgb(pdf, MID)
        pdf.cell(16, 5.5, f'{t["hours"]}h', align='R',
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ── Public entry point ────────────────────────────────────────────────────────

def generate(business_name: str,
             scout_data:  dict,
             income_data: dict,
             build_data:  dict,
             audit_data:  dict,
             for_user:    str = 'brock') -> str:
    """
    Generate a business plan PDF and return the file path.
    Raises RuntimeError if fpdf2 is not installed.
    """
    if not FPDF_AVAILABLE:
        raise RuntimeError(
            'fpdf2 is not installed. Run: pip install fpdf2'
        )

    accent = KATELYN_PK if for_user.lower() == 'katelyn' else BLUE

    pdf = _Doc(accent)
    pdf.set_auto_page_break(auto=True, margin=15)

    _cover(pdf, business_name, for_user, accent)
    _executive_summary(pdf, business_name, scout_data, income_data, accent)
    _revenue_projections(pdf, income_data, accent)
    _launch_roadmap(pdf, build_data, accent)
    _automation_map(pdf, audit_data, accent)
    _odin_future(pdf, build_data, audit_data, accent)
    _sop_checklist(pdf, build_data, accent)

    out_dir = os.path.join(
        'C:/Users/Brock/OneDrive/Desktop/Master Folder',
        'ODIN', 'business_plans'
    )
    os.makedirs(out_dir, exist_ok=True)

    today = date.today().isoformat()
    fname = f'{today}_{_slug(business_name)}_plan.pdf'
    out_path = os.path.join(out_dir, fname)
    pdf.output(out_path)
    return out_path
