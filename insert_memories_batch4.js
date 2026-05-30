const { Client } = require('./node_modules/pg');
const client = new Client({
  host: 'kodama.proxy.rlwy.net', port: 55551,
  user: 'postgres', password: 'yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC',
  database: 'railway', ssl: { rejectUnauthorized: false }
});

const USER_ID = 'a4ede2f8-b3b1-4158-816f-e7503dab81a4';

const memories = [

  // ─── SAAS / PRICING (individual breakouts + new entries) ─────────────────
  {
    key: 'saas_pricing_van_westendorp',
    value: 'Van Westendorp pricing survey: reveals true willingness-to-pay, pricing sensitivity, and preferred value metrics from actual customers. Often surfaces that customers want per-seat pricing instead of all-inclusive flat rate — even when founder believes "simple = better." Also reveals rank order of features customers value most, which directly informs how to package tier differences. Must be done BEFORE finalizing pricing tiers. Dan\'s clients added $200M+ in new recurring revenue after implementing all 5 pricing strategies.'
  },
  {
    key: 'saas_pricing_tiered_psychology',
    value: 'Tiered pricing psychology: Every SaaS needs minimum 3 pricing tiers. TOP tier = price anchor that makes middle tier look cheap. BOTTOM tier = entry point for smaller customers who grow into higher tiers (not having a bottom tier loses 20-30% of potential revenue). Design natural "pull forward" caps at each tier (seats, contacts, usage volume) that force upgrade when hit. Even identical products can be differentiated purely by support level (premium support on top, email-only on bottom). For Valdr Ops: 3 tiers minimum.'
  },
  {
    key: 'saas_pricing_value_metrics',
    value: 'Two SaaS value metric types: (1) OUTCOME-BASED = charge % of value delivered (e.g., recovery SaaS charges % of revenue recovered above baseline). Scales infinitely, aligns incentives perfectly. For Valdr Ops: could charge 10-20% of revenue generated from recovered missed calls. (2) FUNCTIONAL = limit seats, contacts, storage — natural upgrade trigger as customer grows. For Valdr Ops: cap on number of missed calls recovered per month or AI appointments booked per month. Outcome-based can scale same product from $10K/mo to $10M/mo without product changes.'
  },
  {
    key: 'saas_upsells_sawdust_monetization',
    value: 'Sawdust monetization: Partner with adjacent companies to sell their product to your customers, keep 30-50% revenue with zero delivery overhead. The "sawdust" = byproduct of existing customer base that can be monetized at near-100% margin. For Valdr Ops: partner with CRM software, SEO agency, or review management tool — offer as paid add-on. For PartSync Pro: partner with HVAC supply companies (Johnstone Supply, Wesco) for referral revenue. Channel partner cross-sell can grow revenue enough to cover CAC for paid acquisition.'
  },
  {
    key: 'saas_pricing_hide_early_stage',
    value: 'Do NOT show pricing on website in early stage (Dan Martell\'s 5 reasons): (1) Deals get complex as you scale — don\'t lock yourself in publicly. (2) Enterprise needs discount flexibility without a public anchor. (3) $800K enterprise deals are sold completely differently than $100K SMB deals — price is a detail, not the lead. (4) Enterprise buyers care more about deployment speed, integrations, and support than price. (5) Public low pricing looks cheap to enterprise prospects. Recommendation: use "Schedule a demo" or "Get a quote" pages with no visible price. Dan built a multi-million dollar company (Spheric Technologies) without ever publishing pricing, closed P&G with full flexibility.'
  },
  {
    key: 'saas_failure_5_reasons',
    value: '5 reasons SaaS startups fail (Dan Martell): (1) MARKET PROBLEMS — built without validating a real, painful market. Early adopters = people who already solved the problem in a hack way (spreadsheets, internal tools). (2) BUSINESS MODEL FAILURE — wrong pricing model for the customer type (e.g., global site license vs. per-user). (3) POOR MANAGEMENT — co-CEO model never works ("when two are accountable, nobody is"). (4) RUNNING OUT OF CASH — not a money problem, it\'s a milestone management problem. Use 90-day milestone sprints to unlock next funding or revenue level. (5) PRODUCT PROBLEMS — buggy software at scale even with great customers = tidal wave of churn. Use as diagnostic checklist when Valdr Ops or PartSync hits friction or stagnation.'
  },

  // ─── BUSINESS SYSTEMS ────────────────────────────────────────────────────
  {
    key: 'business_team_building_4principles',
    value: 'Dan Martell\'s 4 team-building principles: (1) HIRE AND TRAIN A LOT — "What if I train them and they leave? What if you don\'t and they stay?" Train people to think, not just execute. (2) ALIGN GOALS — ask every new hire: "In 10 years we can\'t be working together — what does your life look like?" Their answer reveals their deep driver. Great leaders align company needs with employee dreams. (3) LEARNING AND DEVELOPMENT PLANS — build explicit skill growth paths; open your personal network to your team. (4) LEADERSHIP CEILING — no business grows past the growth of its leader. "You need to become a $100M leader to build a $100M company." Zig Ziglar: "You don\'t build a business, you build the people and the people build the business." Spheric grew 150% YoY after implementing these principles.'
  },

  // ─── FINANCE ─────────────────────────────────────────────────────────────
  {
    key: 'finance_top1pct_money_rules',
    value: 'Dan Martell\'s 9 money rules of the top 1%: (1) AUTOMATE FINANCIALS — when money arrives, it flows immediately to tax bucket, savings, and investment without ever seeing it. Pay yourself as little as possible in early years. (2) 10% RULE — live on 10% of income; work toward it even starting at 40-60%. (3) INVEST FIRST — broke people buy things, middle class gets loans for things, wealthy invest in income-generating assets. (4) 6-MONTH LIQUID NEST EGG — never in stocks/bonds; accessible in 48 hours. (5) NO CONSUMER DEBT — 47% of people carry CC debt at 22% interest. That is slavery. (6) RISK-RETURN QUADRANT — target: low risk/high return = a business you know well with value-add opportunity (not crypto or index funds). (7) BUILD A PERSONAL P&L — household = business; income minus expenses = profit or loss; hire a house manager if needed. Dan invested $1.7M in coaches/seminars over his career and considers it his highest ROI investment, generating thousands of times the return of that capital.'
  },

  // ─── PERSONAL GROWTH / MINDSET ────────────────────────────────────────────
  {
    key: 'mindset_incompetence_superpower',
    value: '"Be incompetent" as a leadership strategy (Dan Martell): Intentionally stop doing things you are capable of so others must own them. If you keep being the most capable person who jumps in, your team never develops ownership or judgment. "If you want to be rich, be lazy. If you want to be wealthy, be incompetent." Stop teaching people HOW to do their job — train them on the philosophy and principles, then let them figure out execution. Every time you do their job, you signal you don\'t trust them. This is the mechanism behind the 10-80-10 delegation rule: set context (10%) → let them execute (80%) → add finishing touches (10%). Applied to ODIN: do not micromanage every step of ODIN\'s output; set Mission + Parameters, then review the result.'
  },
  {
    key: 'mindset_delayed_gratification',
    value: 'Delayed gratification as wealth compressor (Dan Martell): "A yes to your impulse is a no to your dreams." Delayed gratification is MORE powerful than compound interest as a wealth tool. Strategy: tie big purchases to achievement goals (Dan tied his first McLaren to completing 75 Hard). Walk around with $10K cash in your pocket for 12 months without spending it — breaks the scarcity-to-spending reflex. "The only winning move is not to play the game" of status goods. Dan did not buy luxury goods for 20 years of building. At 38, bought his first supercar. A $20K ski trip at 21 was the worst ROI of his life vs. a road trip with his brother = same memory for less cost. When considering a large personal purchase before hitting a revenue milestone, tie the purchase to a specific achievement instead.'
  },
  {
    key: 'mindset_coaching_roi',
    value: 'Investing in mentors and coaches: Dan Martell invested $1.7M in mentors, coaches, and seminars over 27 years. Considers it his single highest ROI investment — generated thousands of times more than the S&P 500 would have on the same capital. Rule: only 3 valid advice sources: (1) Mentors = people who have done exactly what you want to do (not parents, not friends). (2) Coaches = deep domain specialists with a proven blueprint for the specific skill. (3) Peers = people 1-2 years ahead of you on the same exact path (they bring certainty about what works NOW). Everyone else: listen politely, ignore. "You would never take workout tips from an overweight fitness coach." This is why Brock invests in Rick Ginn + Zach FWR for RE, and Dan Martell frameworks for SaaS/business — all verified by results.'
  }
];

client.connect().then(async () => {
  let inserted = 0;
  for (const m of memories) {
    try {
      await client.query(
        `INSERT INTO memories (user_id, key, value, source)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source`,
        [USER_ID, m.key, m.value, 'transcript_extraction_2026-05-28_batch4']
      );
      inserted++;
    } catch(e) {
      console.log('Failed:', m.key, '-', e.message.slice(0, 80));
    }
  }
  console.log('Done:', inserted + '/' + memories.length + ' memories inserted/updated');
  client.end();
}).catch(e => { console.error('Connection failed:', e.message); process.exit(1); });
