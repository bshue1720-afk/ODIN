# -*- coding: utf-8 -*-
"""Generates the ODIN-for-HVAC capabilities PDF (case-study handout)."""
from fpdf import FPDF
from datetime import date

NAVY   = (20, 38, 66)
BLUE   = (38, 99, 175)
ORANGE = (199, 102, 28)
GREEN  = (32, 130, 84)
GRAY   = (78, 84, 92)
LIGHT  = (236, 240, 246)
SOFT   = (248, 250, 252)

LEFT = 18
USABLE = 215.9 - LEFT * 2  # Letter width minus margins


class PDF(FPDF):
    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 6, "ODIN  -  Shue Box LLC                          "
                        "Confidential - prepared for a case-study partner",
                  align="C")
        self.set_text_color(150, 150, 150)
        self.cell(0, 6, str(self.page_no()), align="R")


def badge(pdf, text, color):
    pdf.set_fill_color(*color)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(20, 4.6, text, border=0, ln=1, align="C", fill=True)


def section(pdf, num, title):
    pdf.ln(3)
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 12.5)
    pdf.cell(0, 9, "   %d.  %s" % (num, title), ln=1, fill=True)
    pdf.ln(2.5)


def cap(pdf, title, tag, what, why):
    # keep block together-ish; let auto page break handle overflow
    color = GREEN if tag == "READY" else ORANGE
    y = pdf.get_y()
    # title
    pdf.set_text_color(*NAVY)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.cell(USABLE - 22, 5.5, title, ln=0)
    # badge at right
    pdf.set_x(LEFT + USABLE - 20)
    badge(pdf, tag, color)
    # what
    pdf.set_text_color(*GRAY)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(USABLE, 4.6, what)
    # why
    pdf.set_text_color(*BLUE)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(USABLE, 4.4, "Why it helps you:  " + why)
    pdf.ln(3.2)


def body(pdf, text, size=10, color=GRAY, gap=5):
    pdf.set_text_color(*color)
    pdf.set_font("Helvetica", "", size)
    pdf.multi_cell(USABLE, gap, text)


def h2(pdf, text):
    pdf.ln(2)
    pdf.set_text_color(*BLUE)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.cell(0, 7, text, ln=1)
    pdf.set_draw_color(*BLUE)
    pdf.set_line_width(0.4)
    x = pdf.get_x()
    y = pdf.get_y()
    pdf.line(LEFT, y, LEFT + 40, y)
    pdf.ln(2.5)


pdf = PDF(format="Letter", unit="mm")
pdf.set_auto_page_break(True, margin=18)
pdf.set_margins(LEFT, 16, LEFT)

# ---------------- COVER ----------------
pdf.add_page()
pdf.ln(34)
pdf.set_fill_color(*NAVY)
pdf.rect(0, pdf.get_y(), 215.9, 56, style="F")
pdf.set_y(pdf.get_y() + 9)
pdf.set_text_color(255, 255, 255)
pdf.set_font("Helvetica", "B", 54)
pdf.cell(0, 22, "ODIN", align="C", ln=1)
pdf.set_font("Helvetica", "", 11)
pdf.set_text_color(200, 212, 230)
pdf.cell(0, 7, "Orchestrated Decision Intelligence Network", align="C", ln=1)
pdf.ln(22)
pdf.set_text_color(*NAVY)
pdf.set_font("Helvetica", "B", 19)
pdf.cell(0, 10, "An AI Assistant for Your HVAC Business", align="C", ln=1)
pdf.ln(3)
pdf.set_text_color(*GRAY)
pdf.set_font("Helvetica", "", 12)
pdf.cell(0, 7, "Everything it can do for a 1-3 person shop - in plain English", align="C", ln=1)
pdf.ln(30)
pdf.set_draw_color(*ORANGE)
pdf.set_line_width(0.8)
pdf.line(LEFT + 40, pdf.get_y(), 215.9 - LEFT - 40, pdf.get_y())
pdf.ln(8)
pdf.set_text_color(*GRAY)
pdf.set_font("Helvetica", "", 11)
pdf.cell(0, 6, "Prepared by Brock Shue  -  Shue Box LLC", align="C", ln=1)
pdf.cell(0, 6, date.today().strftime("%B %d, %Y"), align="C", ln=1)

# ---------------- INTRO ----------------
pdf.add_page()
h2(pdf, "What is ODIN?")
body(pdf,
     "ODIN is software that runs in the background of your business 24 hours a day - like a "
     "full-time office manager who never sleeps, never forgets, and never takes a day off. It keeps "
     "your jobs organized, watches your money, drafts your quotes, follows up with customers "
     "automatically, and brings old customers back - so you can stay on the tools and still grow.")
pdf.ln(2)
h2(pdf, "How you use it")
body(pdf,
     "You talk to ODIN by simple text messages or a basic app. You can ask it things in plain English "
     "- \"text the Johnsons their quote,\" \"what did I spend on fuel this month?\", \"who haven't I "
     "followed up with?\" - and it just does it. Everything else runs on autopilot in the background, "
     "whether you're on a roof or at dinner.")
pdf.ln(4)

# legend box
pdf.set_fill_color(*SOFT)
pdf.set_draw_color(*LIGHT)
top = pdf.get_y()
pdf.rect(LEFT, top, USABLE, 26, style="F")
pdf.set_xy(LEFT + 4, top + 3)
pdf.set_text_color(*NAVY)
pdf.set_font("Helvetica", "B", 10)
pdf.cell(0, 6, "How to read this document", ln=1)
pdf.set_x(LEFT + 4)
badge(pdf, "READY", GREEN)
pdf.set_xy(LEFT + 28, pdf.get_y() - 4.6)
pdf.set_text_color(*GRAY)
pdf.set_font("Helvetica", "", 9.5)
pdf.cell(0, 4.6, "= working today. We can switch it on for your shop right away.", ln=1)
pdf.set_x(LEFT + 4)
badge(pdf, "BUILD", ORANGE)
pdf.set_xy(LEFT + 28, pdf.get_y() - 4.6)
pdf.set_text_color(*GRAY)
pdf.cell(0, 4.6, "= a quick add-on we'd build for you, using parts we already have.", ln=1)
pdf.ln(6)

# ---------------- SECTIONS ----------------
sections = [
    ("Stay Organized", [
        ("Job Board", "READY",
         "Every job moves through clear stages: New, Quoted, Scheduled, In Progress, Done, Paid.",
         "You always know exactly where every job stands. Nothing slips through the cracks."),
        ("Customer History", "READY",
         "ODIN remembers every customer - their equipment, last service date, warranty info, and your notes.",
         "Pull up a customer before you knock and look like you've known them for years."),
        ("Follow-Up Queue", "READY",
         "ODIN tracks every callback and unsent quote, then reminds you - or follows up for you.",
         "Forgetting to follow up is the #1 way small shops lose money. ODIN never forgets."),
        ("Daily Briefing", "READY",
         "Each morning ODIN sends a simple rundown: today's jobs, addresses, customer notes, and what needs attention.",
         "Start every day knowing your plan without digging through paperwork."),
    ]),
    ("Track Your Money", [
        ("Account Balances", "READY",
         "Securely connects to your bank (read-only) and shows all your balances in one place.",
         "Know what you've actually got without logging into five different apps."),
        ("Spending Breakdown", "READY",
         "Sorts spending by category - fuel, parts, tools, insurance - by week, month, or year-to-date.",
         "See where the money is really going so you can plug the leaks."),
        ("Subscription Watch", "READY",
         "Finds every recurring charge hitting your account automatically.",
         "Most owners pay for 2-3 things they forgot about. ODIN catches them."),
        ("Profit Per Job", "BUILD",
         "Tracks revenue minus parts and labor on each job so you see what each one actually made.",
         "Learn which job types pay best and stop chasing the ones that don't."),
        ("Tax Set-Aside Reminder", "BUILD",
         "Tells you how much to put away for taxes as money comes in.",
         "No nasty surprise come tax time."),
    ]),
    ("Win More Quotes", [
        ("Instant Quote Drafter", "BUILD",
         "Text ODIN a few details (\"new AC, 1800 sq ft, attic unit, 14 SEER\") and it writes a clean, "
         "professional quote in seconds.",
         "Quote from the truck instead of at the kitchen table at 9 PM."),
        ("Good / Better / Best", "BUILD",
         "Every quote comes back as three options automatically.",
         "Giving a middle choice is proven to raise the average ticket 20-40% - customers pick up, not down."),
        ("Quote Follow-Up", "READY",
         "If a quote isn't accepted, ODIN gently nudges the customer on day 2 and day 5.",
         "Most quotes die from silence. This recovers jobs you would have lost."),
        ("Professional Look", "READY",
         "Quotes and emails go out clean, consistent, and branded to your shop.",
         "Look like the established outfit, even as a 1-3 person crew."),
    ]),
    ("Grow Your Business", [
        ("Seasonal Maintenance Plans", "BUILD",
         "ODIN automatically reminds past customers for spring AC tune-ups and fall furnace checks.",
         "This is the single biggest growth lever in HVAC - it turns one-time jobs into steady, "
         "recurring income and smooths out your slow months."),
        ("Review Requests", "READY",
         "After every finished job, ODIN texts a friendly \"How'd we do?\" with your Google review link.",
         "More 5-star reviews means more calls. Small shops live and die on reviews."),
        ("Win Back Old Customers", "READY",
         "ODIN finds customers you haven't seen in a year or more and reaches out to them.",
         "Free money sitting in your old customer list."),
        ("Referral Asks", "BUILD",
         "Automatically asks happy customers if they know anyone else who needs service.",
         "The cheapest, highest-trust lead you'll ever get."),
        ("Owner Scorecard", "READY",
         "Simple weekly numbers: jobs done, average ticket, close rate, revenue.",
         "See your business clearly - probably for the first time."),
    ]),
    ("Installs & Scheduling", [
        ("Smart Scheduling", "READY",
         "Books jobs on your calendar and blocks the right amount of time for each.",
         "No double-bookings, no forgotten appointments."),
        ("Prep & Materials List", "BUILD",
         "ODIN generates the parts and tools list for each job type before you head out.",
         "Show up with everything - no second trip to the supply house."),
        ("Customer Prep Texts", "READY",
         "Auto-sends \"We arrive 8-10 AM, please clear access to the unit\" ahead of the job.",
         "Smoother installs, fewer surprises, happier customers."),
        ("Post-Install Wrap-Up", "READY",
         "Warranty reminder and review request fire automatically once the job is done.",
         "Closes every job the right way without you lifting a finger."),
    ]),
    ("Repairs & Diagnostics", [
        ("Repair Intake", "READY",
         "Captures the problem, urgency, and address when a repair comes in and slots it into your day.",
         "Nothing gets lost between the phone call and the truck."),
        ("Diagnostic Knowledge Base", "BUILD",
         "Load HVAC troubleshooting know-how into ODIN so you - or a helper - can look up symptoms fast.",
         "Faster diagnoses, and a built-in training tool for any new hire."),
        ("Parts Follow-Up", "READY",
         "Tracks jobs waiting on a part and reminds you when to circle back.",
         "No customer left hanging for two weeks wondering where you went."),
        ("Repair-to-Maintenance Upsell", "BUILD",
         "After a repair, ODIN offers the customer your maintenance plan.",
         "Turns a one-time fix into a recurring, loyal customer."),
    ]),
    ("Never Miss a Lead", [
        ("Missed-Call Text-Back", "READY",
         "If you can't answer because you're on a roof, ODIN instantly texts the caller back.",
         "A missed call is a lost job - usually to the next guy who picks up. This catches it."),
        ("Instant Lead Reply", "READY",
         "Leads from your website or Facebook get a reply within seconds.",
         "The first shop to respond almost always wins the job."),
        ("After-Hours Cover", "READY",
         "ODIN responds nights and weekends so leads don't go cold.",
         "Capture the 6 PM \"my AC just died\" panic call even while you're at dinner."),
    ]),
]

num = 1
for title, caps in sections:
    section(pdf, num, title)
    for c in caps:
        cap(pdf, *c)
    num += 1

# note about voice
pdf.set_fill_color(*LIGHT)
top = pdf.get_y()
pdf.rect(LEFT, top, USABLE, 16, style="F")
pdf.set_xy(LEFT + 4, top + 2.5)
pdf.set_text_color(*NAVY)
pdf.set_font("Helvetica", "B", 9.5)
pdf.cell(0, 5, "Coming in a later phase:", ln=1)
pdf.set_x(LEFT + 4)
pdf.set_text_color(*GRAY)
pdf.set_font("Helvetica", "", 9.5)
pdf.multi_cell(USABLE - 8,
               4.4,
               "A full AI phone receptionist that answers calls out loud and books appointments by "
               "voice. We're holding that piece for a later step so we can get the core system "
               "working for you first.")

# ---------------- SUMMARY + NEXT STEPS ----------------
pdf.add_page()
h2(pdf, "What's ready now vs. what we'd build")
body(pdf, "Most of this is already working - we'd simply set it up for your shop. A handful of "
          "add-ons are quick builds tailored to HVAC.")
pdf.ln(2)

pdf.set_text_color(*GREEN)
pdf.set_font("Helvetica", "B", 10.5)
pdf.cell(0, 6, "Ready to switch on now:", ln=1)
pdf.set_text_color(*GRAY)
pdf.set_font("Helvetica", "", 9.8)
pdf.multi_cell(USABLE, 4.7,
               "Job tracking, customer history, follow-up queue, daily briefing, finance tracking "
               "(balances / spending / subscriptions), quote follow-up, branded quotes & emails, "
               "review requests, win-back old customers, owner scorecard, smart scheduling, customer "
               "prep texts, post-install wrap-up, repair intake, parts follow-up, missed-call "
               "text-back, instant lead reply, after-hours cover.")
pdf.ln(3)
pdf.set_text_color(*ORANGE)
pdf.set_font("Helvetica", "B", 10.5)
pdf.cell(0, 6, "Quick builds for your shop:", ln=1)
pdf.set_text_color(*GRAY)
pdf.set_font("Helvetica", "", 9.8)
pdf.multi_cell(USABLE, 4.7,
               "Instant quote drafter, good/better/best pricing, seasonal maintenance plans, profit "
               "per job, tax set-aside reminder, prep & materials lists, diagnostic knowledge base, "
               "referral asks, repair-to-maintenance upsell.")
pdf.ln(4)

h2(pdf, "What you'd need (it's simple)")
for line in [
    "A phone number for texts - we set it up for you.",
    "A read-only connection to your bank - you stay in full control; ODIN can never move money.",
    "Your email address for sending quotes and follow-ups.",
    "About 30 minutes to walk us through how your shop runs.",
]:
    pdf.set_text_color(*ORANGE)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(6, 5.2, "-", ln=0)
    pdf.set_text_color(*GRAY)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(USABLE - 6, 5.2, line)
pdf.ln(1)
body(pdf, "No new computer. No complicated software to learn. ODIN meets you where you already are - "
          "your phone.")
pdf.ln(3)

h2(pdf, "What it costs")
body(pdf, "To run: pennies a day in hosting and AI costs. To you, as a case-study partner: nothing. In "
          "exchange, we'd ask for honest feedback as we go and a short testimonial if you're happy with "
          "the results.")
pdf.ln(3)

h2(pdf, "How we'd roll it out")
steps = [
    "A 30-minute sit-down to map how your shop runs today.",
    "Week one: we switch on the \"ready\" pieces.",
    "We build 1-2 custom pieces for you (likely the quote drafter and seasonal maintenance plans).",
    "You run it for a few weeks and tell us what's working.",
    "We capture the results together as a case study.",
]
for i, s in enumerate(steps, 1):
    pdf.set_text_color(*BLUE)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(7, 5.4, "%d." % i, ln=0)
    pdf.set_text_color(*GRAY)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(USABLE - 7, 5.4, s)
pdf.ln(5)

# closing banner
pdf.set_fill_color(*NAVY)
top = pdf.get_y()
pdf.rect(LEFT, top, USABLE, 22, style="F")
pdf.set_xy(LEFT + 5, top + 4)
pdf.set_text_color(255, 255, 255)
pdf.set_font("Helvetica", "BI", 11.5)
pdf.multi_cell(USABLE - 10, 6,
               "ODIN's whole job is to give you back your time and stop money from leaking out of the "
               "business - so you can stay on the tools and still grow.")

out = r"C:\Users\Brock\OneDrive\Desktop\Master Folder\ODIN_for_HVAC.pdf"
pdf.output(out)
print("WROTE", out)
