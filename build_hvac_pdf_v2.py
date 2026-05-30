# -*- coding: utf-8 -*-
"""
ODIN for HVAC - PDF Handout Generator v2
Run: py build_hvac_pdf_v2.py
Output: Desktop\Master Folder\ODIN_for_HVAC_v2.pdf
"""
from fpdf import FPDF, XPos, YPos
from datetime import date
import os

OUT = r"C:\Users\Brock\OneDrive\Desktop\Master Folder\ODIN_for_HVAC_v2.pdf"

# ── Palette ──────────────────────────────────────────────────────────────────
NAVY        = (18,  40,  76)
BLUE        = (30,  90, 180)
STEEL       = (60,  90, 140)
ORANGE      = (210, 95,  20)
GREEN       = (28, 130,  75)
GRAY        = (70,  80,  95)
MID         = (120, 130, 145)
LIGHT_BG    = (240, 244, 250)
STRIP       = (228, 235, 245)
WHITE       = (255, 255, 255)
PAGE_W      = 215.9   # Letter
LEFT_M      = 16
RIGHT_M     = 16
INNER_W     = PAGE_W - LEFT_M - RIGHT_M   # 183.9 mm


# ── Helpers ───────────────────────────────────────────────────────────────────
def rgb(pdf, color):
    pdf.set_text_color(*color)

def fill(pdf, color):
    pdf.set_fill_color(*color)

def draw(pdf, color):
    pdf.set_draw_color(*color)


class Doc(FPDF):
    def header(self):
        pass   # custom per-page

    def footer(self):
        self.set_y(-13)
        fill(self, NAVY)
        self.rect(0, self.get_y(), PAGE_W, 13, style="F")
        self.set_y(-11)
        rgb(self, (180, 195, 220))
        self.set_font("Helvetica", "", 7.5)
        self.cell(INNER_W / 2 + LEFT_M, 5,
                  "ODIN  ·  Shue Box LLC  ·  Confidential",
                  new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.cell(INNER_W / 2, 5,
                  "Page %d" % self.page_no(),
                  align="R",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def add_page(pdf):
    pdf.add_page()
    pdf.set_left_margin(LEFT_M)
    pdf.set_right_margin(RIGHT_M)
    pdf.set_x(LEFT_M)


def divider(pdf, color=STRIP):
    draw(pdf, color)
    pdf.set_line_width(0.35)
    y = pdf.get_y()
    pdf.line(LEFT_M, y, PAGE_W - RIGHT_M, y)
    pdf.ln(2)


def section_bar(pdf, icon, title):
    """Full-width dark navy section header bar."""
    pdf.ln(4)
    fill(pdf, NAVY)
    pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 10, style="F")
    pdf.set_x(LEFT_M + 4)
    rgb(pdf, WHITE)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.cell(INNER_W - 4, 10, "%s  %s" % (icon, title),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)


def pill(pdf, label, color):
    """Inline colored label badge."""
    fill(pdf, color)
    rgb(pdf, WHITE)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.cell(18, 4.8, label, align="C", fill=True,
             new_x=XPos.RIGHT, new_y=YPos.TOP)


def feature(pdf, title, tag, description, why):
    """One feature block: title + tag, description, italic why-it-helps line."""
    # title row
    color = GREEN if tag == "READY NOW" else ORANGE
    pdf.set_x(LEFT_M)
    rgb(pdf, NAVY)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(INNER_W - 22, 5.5, title,
             new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_x(PAGE_W - RIGHT_M - 20)
    pill(pdf, tag, color)
    pdf.ln(6)
    # description
    pdf.set_x(LEFT_M)
    rgb(pdf, GRAY)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(INNER_W, 5, description,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    # why
    pdf.set_x(LEFT_M)
    rgb(pdf, STEEL)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(INNER_W, 4.8,
                   "\>  Why it matters:  " + why,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3.5)
    divider(pdf, STRIP)


def call_out(pdf, text, bg=LIGHT_BG, text_color=NAVY):
    """Shaded call-out box."""
    fill(pdf, bg)
    draw(pdf, bg)
    start_y = pdf.get_y()
    pdf.rect(LEFT_M, start_y, INNER_W, 1, style="F")   # placeholder
    pdf.set_x(LEFT_M + 3)
    rgb(pdf, text_color)
    pdf.set_font("Helvetica", "", 9.5)
    line_h = 5
    # measure height
    lines = text.split("\n")
    total_h = len(lines) * line_h + 6
    fill(pdf, bg)
    pdf.rect(LEFT_M, start_y, INNER_W, total_h, style="F")
    pdf.set_xy(LEFT_M + 4, start_y + 3)
    pdf.multi_cell(INNER_W - 8, line_h, text,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def h2(pdf, text):
    pdf.ln(2)
    rgb(pdf, BLUE)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(INNER_W, 7, text,
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    draw(pdf, ORANGE)
    pdf.set_line_width(0.6)
    pdf.line(LEFT_M, pdf.get_y(), LEFT_M + 35, pdf.get_y())
    pdf.ln(3)


def body_text(pdf, text, size=10, color=GRAY, line_h=5.2):
    pdf.set_x(LEFT_M)
    rgb(pdf, color)
    pdf.set_font("Helvetica", "", size)
    pdf.multi_cell(INNER_W, line_h, text,
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ── BUILD DOC ─────────────────────────────────────────────────────────────────
pdf = Doc(format="Letter", unit="mm")
pdf.set_auto_page_break(True, margin=20)
pdf.set_margins(LEFT_M, 16, RIGHT_M)


# ════════════════════════════════════════════════════════════════════
#  COVER PAGE
# ════════════════════════════════════════════════════════════════════
add_page(pdf)

# full navy top band
fill(pdf, NAVY)
pdf.rect(0, 0, PAGE_W, 80, style="F")

# orange accent stripe
fill(pdf, ORANGE)
pdf.rect(0, 80, PAGE_W, 3.5, style="F")

# ODIN big text
pdf.set_y(16)
rgb(pdf, WHITE)
pdf.set_font("Helvetica", "B", 68)
pdf.cell(PAGE_W, 28, "ODIN", align="C",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)

# subtitle
rgb(pdf, (180, 200, 230))
pdf.set_font("Helvetica", "", 10.5)
pdf.cell(PAGE_W, 6,
         "Orchestrated Decision Intelligence Network",
         align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.ln(10)

# main headline
rgb(pdf, NAVY)
pdf.set_font("Helvetica", "B", 20)
pdf.cell(PAGE_W, 10,
         "An AI System Built for Your HVAC Business",
         align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.ln(2)

rgb(pdf, GRAY)
pdf.set_font("Helvetica", "", 11.5)
pdf.cell(PAGE_W, 7,
         "Everything it can do for a 1-3 person shop, explained in plain English",
         align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.ln(12)

# legend box
fill(pdf, LIGHT_BG)
draw(pdf, STRIP)
pdf.rect(LEFT_M + 20, pdf.get_y(), INNER_W - 40, 32, style="FD")
pdf.set_xy(LEFT_M + 26, pdf.get_y() + 4)
rgb(pdf, NAVY)
pdf.set_font("Helvetica", "B", 10)
pdf.cell(INNER_W - 52, 6, "How to read this document",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_x(LEFT_M + 26)

fill(pdf, GREEN)
rgb(pdf, WHITE)
pdf.set_font("Helvetica", "B", 7)
pdf.cell(22, 5, "READY NOW", align="C", fill=True,
         new_x=XPos.RIGHT, new_y=YPos.TOP)
pdf.set_x(pdf.get_x() + 3)
rgb(pdf, GRAY)
pdf.set_font("Helvetica", "", 9.5)
pdf.cell(0, 5, "Working today. We can switch it on for your shop immediately.",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.set_x(LEFT_M + 26)
pdf.ln(2)
fill(pdf, ORANGE)
rgb(pdf, WHITE)
pdf.set_font("Helvetica", "B", 7)
pdf.cell(22, 5, "QUICK BUILD", align="C", fill=True,
         new_x=XPos.RIGHT, new_y=YPos.TOP)
pdf.set_x(pdf.get_x() + 3)
rgb(pdf, GRAY)
pdf.set_font("Helvetica", "", 9.5)
pdf.cell(0, 5, "A short add-on we build for you, using parts that already exist.",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)

pdf.ln(16)

# prepared by
draw(pdf, MID)
pdf.set_line_width(0.3)
pdf.line(LEFT_M + 50, pdf.get_y(), PAGE_W - RIGHT_M - 50, pdf.get_y())
pdf.ln(5)
rgb(pdf, MID)
pdf.set_font("Helvetica", "", 10)
pdf.cell(PAGE_W, 6,
         "Prepared by Brock Shue  ·  Shue Box LLC  ·  " + date.today().strftime("%B %d, %Y"),
         align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ════════════════════════════════════════════════════════════════════
#  PAGE 2 — WHAT IS ODIN
# ════════════════════════════════════════════════════════════════════
add_page(pdf)

# page header stripe
fill(pdf, NAVY)
pdf.rect(0, 0, PAGE_W, 12, style="F")
pdf.set_y(2.5)
rgb(pdf, WHITE)
pdf.set_font("Helvetica", "B", 9)
pdf.cell(PAGE_W, 7, "ODIN for HVAC", align="C",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.ln(6)

h2(pdf, "What is ODIN?")
body_text(pdf,
    "ODIN is software that runs in the background of your business 24 hours a day. "
    "Think of it as a full-time office manager who never sleeps, never forgets a follow-up, "
    "and never takes a sick day. It keeps your jobs organized, watches your money, "
    "writes your quotes, chases down old customers, and answers leads the moment they come in "
    "- so you can stay on the tools and still grow.")
pdf.ln(3)

h2(pdf, "How you use it")
body_text(pdf,
    "You talk to ODIN by text or a simple web app - plain English, no learning curve. "
    "Say \"who haven't I followed up with?\", \"what did I spend on parts this month?\", "
    "\"text the Johnson quote\" - and it handles it. "
    "Everything else runs automatically in the background whether you're on a roof, under a "
    "crawlspace, or at your kid's game.")
pdf.ln(3)

h2(pdf, "What it is NOT")
body_text(pdf,
    "It is not ServiceTitan or Housecall Pro. Those tools cost $300-500/month and are built "
    "for large crews. ODIN is lighter, cheaper, and AI-native - built specifically for the "
    "1-3 person shop that needs automation without the overhead.")
pdf.ln(4)

# highlight box
fill(pdf, NAVY)
pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 22, style="F")
pdf.set_xy(LEFT_M + 5, pdf.get_y() + 4)
rgb(pdf, WHITE)
pdf.set_font("Helvetica", "B", 11)
pdf.cell(INNER_W - 10, 6.5,
         "The core promise: you stay on the tools.",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_x(LEFT_M + 5)
rgb(pdf, (180, 200, 230))
pdf.set_font("Helvetica", "", 10)
pdf.multi_cell(INNER_W - 10, 5.5,
    "ODIN handles the phone calls you miss, the quotes you forget to send, the customers "
    "you mean to follow up with, and the money details you don't have time to track.")


# ════════════════════════════════════════════════════════════════════
#  SECTIONS 1-7
# ════════════════════════════════════════════════════════════════════

ALL_SECTIONS = [

    ("1", "STAY ORGANIZED", [
        (
            "Job Board",
            "READY NOW",
            "Every job moves through clear stages: New Lead, Quoted, Scheduled, "
            "In Progress, Done, Paid. You see every job's status at a glance from your phone.",
            "Nothing falls through the cracks. You always know exactly where every job stands "
            "without calling around or digging through a notepad."
        ),
        (
            "Customer History",
            "READY NOW",
            "ODIN stores every customer's equipment model, install date, last service, warranty "
            "expiry, and your personal notes. Pull it up before you knock on any door.",
            "You show up knowing their system. Customers notice - and they stay loyal to the "
            "contractor who remembers them."
        ),
        (
            "Follow-Up Queue",
            "READY NOW",
            "ODIN tracks every open quote, missed callback, and pending job. It reminds you "
            "automatically - or follows up for you - so nothing gets dropped.",
            "Forgetting to follow up is the single biggest way small shops lose revenue. "
            "ODIN never forgets."
        ),
        (
            "Daily Briefing",
            "READY NOW",
            "Every morning ODIN sends a simple rundown: today's jobs with addresses and customer "
            "notes, anything urgent, and what needs attention.",
            "Start every day with a clear plan instead of scrambling to remember who called "
            "yesterday."
        ),
        (
            "Task Delegation",
            "READY NOW",
            "Assign tasks to yourself, a helper, or ODIN with a simple text. ODIN tracks "
            "completion and reminds when things are overdue.",
            "If you ever bring on a part-time helper, ODIN becomes their to-do list and "
            "accountability system automatically."
        ),
    ]),

    ("2", "TRACK YOUR MONEY", [
        (
            "Account Balances",
            "READY NOW",
            "ODIN connects securely to your bank (read-only - it can never move money) "
            "and shows all your balances in one place on demand.",
            "Know what you actually have right now without logging into multiple apps "
            "or waiting for a statement."
        ),
        (
            "Spending Breakdown",
            "READY NOW",
            "Automatically sorts your spending into categories: fuel, parts, tools, insurance, "
            "subs. View it by week, month, or year-to-date with a single message.",
            "See where the money is going. Most owners are surprised by two or three categories "
            "they had no idea were that high."
        ),
        (
            "Subscription Watch",
            "READY NOW",
            "ODIN scans every recurring charge hitting your accounts and lists them all in one "
            "place - name, amount, billing date.",
            "The average small business owner pays for 3-4 subscriptions they forgot about. "
            "ODIN finds them."
        ),
        (
            "Profit Per Job Type",
            "QUICK BUILD",
            "Tracks revenue minus parts and labor costs on each job so you can see "
            "what each type of work actually nets you.",
            "Learn which jobs make real money and which ones eat your time for thin margins - "
            "then shift what you take on."
        ),
        (
            "Tax Set-Aside Tracker",
            "QUICK BUILD",
            "As money comes in, ODIN calculates and reminds you how much to set aside "
            "for quarterly taxes based on your actual income.",
            "No more scrambling in April. The money is already waiting."
        ),
        (
            "Weekly Revenue Scorecard",
            "READY NOW",
            "A simple weekly number: jobs completed, total revenue, average ticket, "
            "compare to last week and last month.",
            "You run a real business. See it like one."
        ),
    ]),

    ("3", "WIN MORE QUOTES", [
        (
            "Instant Quote Drafter",
            "QUICK BUILD",
            "Text ODIN a few job details from the truck - unit size, type of work, location, "
            "condition - and it writes a clean, professional quote in under 60 seconds.",
            "Quote on-site instead of at your kitchen table at 9 PM. Faster quotes close faster."
        ),
        (
            "Good / Better / Best Pricing",
            "QUICK BUILD",
            "Every quote comes back structured as three options automatically: "
            "basic fix, recommended solution, and premium option.",
            "Customers almost always pick the middle. This one change alone typically raises "
            "average ticket value 20-40% with zero extra selling."
        ),
        (
            "Quote Follow-Up Automation",
            "READY NOW",
            "If a quote is not accepted within 48 hours, ODIN automatically sends a "
            "friendly check-in on day 2 and again on day 5.",
            "Most small-shop quotes die from silence. This sequence recovers 15-25% of "
            "quotes that would otherwise be lost."
        ),
        (
            "Professional Branded Look",
            "READY NOW",
            "Every quote, email, and text goes out clean and consistent with your shop name. "
            "No more copying and pasting from old texts.",
            "Look like the established shop even as a 2-person crew."
        ),
    ]),

    ("4", "GROW YOUR BUSINESS", [
        (
            "Seasonal Maintenance Plan Engine",
            "QUICK BUILD",
            "ODIN automatically reaches out to your past customers each spring (AC tune-up) "
            "and each fall (furnace check) with a simple, personal-feeling message.",
            "This is the single highest-ROI move in HVAC. It converts one-time customers "
            "into recurring revenue and fills your slow months before they happen."
        ),
        (
            "Google Review Requests",
            "READY NOW",
            "After every completed job, ODIN sends a friendly text: "
            "\"How did we do? A quick review means the world to us.\" with your Google link.",
            "1-3 man shops live and die on reviews. A consistent review engine is the "
            "single most reliable way to grow calls without spending on ads."
        ),
        (
            "Win Back Old Customers",
            "READY NOW",
            "ODIN identifies customers you have not contacted in 12+ months and sends them "
            "a warm check-in - no cold feel, just a natural reconnect.",
            "Your old customer list is free revenue. Most shops never touch it."
        ),
        (
            "Referral Requests",
            "QUICK BUILD",
            "After a positive review or a completed job, ODIN asks the customer if they "
            "know anyone else who needs HVAC work.",
            "Referred customers close faster, complain less, and refer more. "
            "The cheapest lead you will ever get."
        ),
        (
            "Owner KPI Scorecard",
            "READY NOW",
            "Simple weekly numbers texted to you: jobs done, revenue, average ticket, "
            "close rate on quotes, new vs. returning customers.",
            "Most small shop owners have never seen their own numbers clearly. "
            "Once you see them, you make better decisions automatically."
        ),
        (
            "Decision Support",
            "READY NOW",
            "When you face a business decision - hire a helper, buy a new van, expand to "
            "a new area - ODIN logs it, frames the options, and gives you a recommendation.",
            "Stop making big decisions alone at midnight. Have a system that thinks through "
            "it with you."
        ),
    ]),

    ("5", "SCHEDULING AND INSTALLS", [
        (
            "Smart Job Scheduling",
            "READY NOW",
            "Book jobs into your calendar with one text. ODIN blocks the right amount of "
            "time per job type and prevents double-booking.",
            "No more missed appointments or showing up to two places at once."
        ),
        (
            "Job Prep and Materials List",
            "QUICK BUILD",
            "Before each install or major repair, ODIN generates the parts and tools list "
            "based on the job type so you leave the supply house with everything.",
            "One fewer trip back to the supply house is an hour saved and a happier customer."
        ),
        (
            "Customer Prep Text",
            "READY NOW",
            "The morning of every install, ODIN automatically texts the customer: "
            "arrival window, what to clear, what to expect.",
            "Smoother installs. Fewer \"nobody told me\" calls. Customers feel taken care of."
        ),
        (
            "Post-Install Wrap-Up",
            "READY NOW",
            "After a job is marked done, ODIN automatically fires a warranty reminder to "
            "the customer and a review request 24 hours later.",
            "Every job closes the right way without you lifting a finger."
        ),
    ]),

    ("6", "REPAIRS AND DIAGNOSTICS", [
        (
            "Repair Intake",
            "READY NOW",
            "When a repair call comes in, ODIN captures the problem description, urgency, "
            "customer address, and equipment info and slots it into your day.",
            "Nothing gets lost between the voicemail and the truck."
        ),
        (
            "Diagnostic Knowledge Base",
            "QUICK BUILD",
            "Load your HVAC troubleshooting knowledge into ODIN - symptoms, likely causes, "
            "common fixes. You or a helper can look up any symptom on the way to a job.",
            "Faster diagnosis on every call. Also the foundation of a training tool if you "
            "ever bring someone on."
        ),
        (
            "Parts Follow-Up Tracker",
            "READY NOW",
            "For jobs waiting on a part or a callback, ODIN tracks the open item and "
            "reminds you automatically so the customer never wonders where you went.",
            "One of the top reasons small HVAC shops lose customers is silence while "
            "waiting on parts. This eliminates it."
        ),
        (
            "Repair to Maintenance Upsell",
            "QUICK BUILD",
            "After every repair, ODIN sends the customer a short message offering your "
            "seasonal maintenance plan so it never happens again.",
            "Turns a one-time repair customer into a recurring plan customer. "
            "This is the growth loop."
        ),
    ]),

    ("7", "NEVER MISS A LEAD", [
        (
            "Missed-Call Text-Back",
            "READY NOW",
            "The instant you miss a call, ODIN texts the caller back: "
            "\"Hey, this is [Your Shop] - sorry we missed you, what can we help with?\" "
            "in under 10 seconds.",
            "A missed call is almost always a lost job - it goes to the next guy who "
            "picks up. This is the single fastest ROI of anything in this document."
        ),
        (
            "Instant Web / Facebook Lead Reply",
            "READY NOW",
            "Any lead that comes in through your website or Facebook gets a reply "
            "within seconds, day or night.",
            "The first contractor to respond almost always gets the job. "
            "ODIN makes sure that is always you."
        ),
        (
            "After-Hours Coverage",
            "READY NOW",
            "When a lead comes in at 7 PM on a Saturday - \"my AC just died\" - "
            "ODIN responds immediately, captures the details, and books them for Monday.",
            "You are at dinner. You still get the job. That is the whole point."
        ),
    ]),
]


for sec_num, sec_title, features in ALL_SECTIONS:
    add_page(pdf)

    # page header
    fill(pdf, NAVY)
    pdf.rect(0, 0, PAGE_W, 12, style="F")
    pdf.set_y(2.5)
    rgb(pdf, WHITE)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(PAGE_W, 7, "ODIN for HVAC", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    section_bar(pdf, "", "SECTION %s  -  %s" % (sec_num, sec_title))

    for f in features:
        feature(pdf, *f)


# ════════════════════════════════════════════════════════════════════
#  SUMMARY PAGE
# ════════════════════════════════════════════════════════════════════
add_page(pdf)

fill(pdf, NAVY)
pdf.rect(0, 0, PAGE_W, 12, style="F")
pdf.set_y(2.5)
rgb(pdf, WHITE)
pdf.set_font("Helvetica", "B", 9)
pdf.cell(PAGE_W, 7, "ODIN for HVAC", align="C",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.ln(5)

section_bar(pdf, "", "SUMMARY")

# Ready now list
rgb(pdf, GREEN)
pdf.set_font("Helvetica", "B", 10.5)
pdf.cell(INNER_W, 6.5, "Ready to switch on for your shop today:",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.ln(1)
rgb(pdf, GRAY)
pdf.set_font("Helvetica", "", 9.5)
pdf.multi_cell(INNER_W, 5,
    "Job board  \·  Customer history  \·  Follow-up queue  \·  Daily briefing  \·  "
    "Task tracking  \·  Account balances  \·  Spending breakdown  \·  Subscription watch  \·  "
    "Weekly revenue scorecard  \·  Quote follow-up  \·  Branded quotes and emails  \·  "
    "Review requests  \·  Win-back old customers  \·  Owner KPI scorecard  \·  "
    "Decision support  \·  Smart scheduling  \·  Customer prep texts  \·  "
    "Post-install wrap-up  \·  Repair intake  \·  Parts follow-up  \·  "
    "Missed-call text-back  \·  Instant lead reply  \·  After-hours coverage",
    new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.ln(4)
divider(pdf)

# Quick builds list
rgb(pdf, ORANGE)
pdf.set_font("Helvetica", "B", 10.5)
pdf.cell(INNER_W, 6.5, "Quick builds tailored for your shop:",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.ln(1)
rgb(pdf, GRAY)
pdf.set_font("Helvetica", "", 9.5)
pdf.multi_cell(INNER_W, 5,
    "Instant quote drafter  \·  Good/Better/Best pricing  \·  "
    "Seasonal maintenance plan engine  \·  Profit per job type  \·  "
    "Tax set-aside tracker  \·  Job prep and materials list  \·  "
    "Diagnostic knowledge base  \·  Referral requests  \·  "
    "Repair-to-maintenance upsell",
    new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.ln(4)
divider(pdf)

# What you need
h2(pdf, "What you would need (it is simple)")

items = [
    ("A text number for your shop",
     "We set it up for you. Takes about 10 minutes."),
    ("Read-only bank access",
     "You stay in full control. ODIN can never move money, only read it."),
    ("Your email address",
     "For sending quotes, follow-ups, and review requests."),
    ("30 minutes with us",
     "We walk through how your shop runs today and configure everything around it."),
]
for title, desc in items:
    fill(pdf, ORANGE)
    pdf.rect(LEFT_M, pdf.get_y(), 2, 5.5, style="F")
    pdf.set_x(LEFT_M + 5)
    rgb(pdf, NAVY)
    pdf.set_font("Helvetica", "B", 9.8)
    pdf.cell(45, 5.5, title, new_x=XPos.RIGHT, new_y=YPos.TOP)
    rgb(pdf, GRAY)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(INNER_W - 50, 5.5, desc, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

pdf.ln(3)
divider(pdf)

# Cost
h2(pdf, "What it costs you")
body_text(pdf,
    "As a case-study partner: nothing. We set it up for your shop at no charge. "
    "In exchange, we ask for honest feedback along the way and a short testimonial "
    "if you are happy with the results. That is the whole deal.")
pdf.ln(3)
divider(pdf)

# Rollout
h2(pdf, "How we would roll it out")
steps = [
    "30-minute conversation to understand how your shop runs today.",
    "Week 1: all the ready-now pieces switched on and tested.",
    "Week 2: we build 1-2 custom pieces for your specific workflow (quote drafter + seasonal maintenance are the usual first picks).",
    "You run it for a few weeks and give us real feedback.",
    "We document the results together as a case study.",
]
for i, step in enumerate(steps, 1):
    fill(pdf, NAVY)
    pdf.set_font("Helvetica", "B", 9)
    rgb(pdf, WHITE)
    pdf.rect(LEFT_M, pdf.get_y(), 7, 6, style="F")
    pdf.set_xy(LEFT_M, pdf.get_y())
    pdf.cell(7, 6, str(i), align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_x(LEFT_M + 10)
    rgb(pdf, GRAY)
    pdf.set_font("Helvetica", "", 9.8)
    pdf.multi_cell(INNER_W - 10, 6, step, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

pdf.ln(4)

# closing banner
fill(pdf, NAVY)
pdf.rect(LEFT_M, pdf.get_y(), INNER_W, 24, style="F")
pdf.set_xy(LEFT_M + 6, pdf.get_y() + 4)
rgb(pdf, ORANGE)
pdf.set_font("Helvetica", "B", 11)
pdf.cell(INNER_W - 12, 6.5, "The bottom line:",
         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
pdf.set_x(LEFT_M + 6)
rgb(pdf, WHITE)
pdf.set_font("Helvetica", "", 10)
pdf.multi_cell(INNER_W - 12, 5.5,
    "ODIN's job is to catch the money your shop is already losing - missed calls, "
    "dead quotes, forgotten follow-ups, customers who drifted away - and put it back "
    "in your pocket, so you can stay on the tools and still grow.")


# ── WRITE ─────────────────────────────────────────────────────────────────────
pdf.output(OUT)
print("DONE ->", OUT)
print("Pages:", pdf.page)
