/**
 * ODIN — Buyer CSV Import
 * Imports all XLeads buyer CSVs into the buyers table.
 * Deduplicates by primary phone. Skips litigators and contacts with no phone/email.
 * Run: node scripts/import_buyers.js
 */

const { Client } = require('../node_modules/pg');
const fs = require('fs');
const path = require('path');

const CSV_DIR = path.join(__dirname, '../Xleads/Buyers');
const CSV_FILES = [
  'lpp-export-464a5f74-21c0-4d79-be0f-74b5c52e14a9.csv',
  'lpp-export-5f4c2970-6d42-4c58-941c-7b9800a97704.csv',
  'lpp-export-578a43c6-12f5-4765-a0a3-97dcd7648844.csv',
];

const DB = {
  host: 'kodama.proxy.rlwy.net', port: 55551,
  user: 'postgres', password: 'yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC',
  database: 'railway', ssl: { rejectUnauthorized: false }
};

// Memphis metro area detection (these buyers are highest priority)
const MEMPHIS_METRO = [
  'memphis', 'cordova', 'germantown', 'bartlett', 'collierville',
  'arlington', 'lakeland', 'millington', 'southaven', 'olive branch',
  'horn lake', 'nesbit', 'hernando', 'walls', 'west memphis'
];

function isMemphi(city, state) {
  if (!city) return false;
  const c = city.toLowerCase();
  return MEMPHIS_METRO.some(m => c.includes(m)) ||
    (state === 'TN' && ['38002','38016','38017','38018','38104','38105','38106','38107','38108','38109','38111','38112','38114','38115','38116','38117','38118','38119','38120','38122','38125','38127','38128','38131','38132','38133','38134','38135','38138','38139'].includes(''));
}

function getMarket(city, state, zip) {
  if (isMemphi(city, state)) return 'Memphis TN';
  if (state === 'TN') return `Tennessee`;
  if (state === 'MS') return `Mississippi`;
  return `${city || ''} ${state || ''}`.trim() || 'Unknown';
}

// Simple CSV parser — handles quoted fields
function parseCSVLine(line) {
  const result = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      inQuotes = !inQuotes;
    } else if (c === ',' && !inQuotes) {
      result.push(current);
      current = '';
    } else {
      current += c;
    }
  }
  result.push(current);
  return result;
}

function parseBool(val) {
  return val === 'True' || val === 'true' || val === '1';
}

function parseNum(val) {
  const n = parseFloat(val);
  return isNaN(n) ? 0 : n;
}

function parseRows(filePath) {
  const content = fs.readFileSync(filePath, 'utf8');
  const lines = content.split('\n').filter(l => l.trim());
  const headers = parseCSVLine(lines[0]);
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const vals = parseCSVLine(lines[i]);
    if (vals.length < 30) continue;
    const row = {};
    headers.forEach((h, idx) => { row[h] = (vals[idx] || '').trim(); });
    rows.push(row);
  }
  return rows;
}

function rowToBuyer(r) {
  const phone1     = r['Contact1Phone_1'] || '';
  const phone1dnc  = parseBool(r['Contact1Phone_1_DNC']);
  const phone1lit  = parseBool(r['Contact1Phone_1_Litigator']);
  const phone2     = r['Contact1Phone_2'] || '';
  const email1     = r['Contact1Email_1'] || '';
  const email2     = r['Contact1Email_2'] || '';
  const contactName = r['Contact1Name'] || '';
  const fullName   = r['FullName'] || '';
  const city       = r['MailingAddressCity'] || '';
  const state      = r['MailingAddressState'] || '';
  const zip        = r['MailingAddressZip'] || '';

  // Pick best phone: if phone1 is DNC but phone2 exists, use phone2
  let bestPhone = '';
  let isDnc = false;
  if (phone1 && !phone1dnc && !phone1lit) {
    bestPhone = phone1;
  } else if (phone2 && !parseBool(r['Contact1Phone_2_DNC'])) {
    bestPhone = phone2;
    isDnc = phone1dnc;
  } else {
    bestPhone = phone1 || phone2;
    isDnc = phone1dnc;
  }

  // Skip if no contact info at all
  if (!bestPhone && !email1) return null;

  return {
    name:              contactName || fullName,
    company:           fullName !== contactName ? fullName : null,
    email:             email1 || null,
    email2:            email2 || null,
    phone:             bestPhone || null,
    phone2:            phone2 && phone2 !== bestPhone ? phone2 : null,
    market:            getMarket(city, state, zip),
    city:              city || null,
    state:             state || null,
    zip:               zip || null,
    is_flipper:        parseBool(r['IsFlipper']),
    is_landlord:       parseBool(r['IsLandlord']),
    is_note_holder:    parseBool(r['IsNoteHolder']),
    is_lender:         parseBool(r['IsLender']),
    is_distressed:     parseBool(r['IsInvestorDistressed']),
    portfolio_owned:   parseInt(r['PortfolioOwnedCount']) || 0,
    portfolio_value:   parseNum(r['PortfolioValue']),
    portfolio_buy_avg: parseNum(r['PortfolioPurchaseAverage']),
    flipped_count:     parseInt(r['FlippedPropertiesCount']) || 0,
    flipped_avg_profit:parseNum(r['FlippedAverageGrossProfit']),
    source:            'xleads_csv',
    xleads_detail_url: r['DetailsLink'] || null,
    is_dnc:            isDnc,
    is_litigator:      phone1lit,
    active:            !isDnc && !phone1lit,
    notes:             buildNotes(r),
  };
}

function buildNotes(r) {
  const parts = [];
  if (parseBool(r['IsFlipper']))     parts.push('Flipper');
  if (parseBool(r['IsLandlord']))    parts.push('Landlord');
  if (parseBool(r['IsNoteHolder']))  parts.push('Note Holder');
  if (parseBool(r['IsLender']))      parts.push('Lender');
  const flips = parseInt(r['FlippedPropertiesCount']) || 0;
  if (flips > 0) parts.push(`${flips} flips, avg profit $${Math.round(parseFloat(r['FlippedAverageGrossProfit'])||0).toLocaleString()}`);
  const owned = parseInt(r['PortfolioOwnedCount']) || 0;
  if (owned > 0) parts.push(`${owned} properties owned`);
  return parts.join(' | ') || null;
}

async function main() {
  const client = new Client(DB);
  await client.connect();
  console.log('Connected to DB');

  let total = 0, inserted = 0, skipped = 0, dupes = 0;
  const seenPhones = new Set();

  for (const file of CSV_FILES) {
    const fp = path.join(CSV_DIR, file);
    console.log(`\nParsing ${file}...`);
    const rows = parseRows(fp);
    console.log(`  ${rows.length} rows`);

    // Build batch of valid buyers first
    const batch = [];
    for (const row of rows) {
      total++;
      const buyer = rowToBuyer(row);
      if (!buyer) { skipped++; continue; }
      if (buyer.phone && seenPhones.has(buyer.phone)) { dupes++; continue; }
      if (buyer.phone) seenPhones.add(buyer.phone);
      batch.push(buyer);
    }

    // Batch insert in chunks of 100
    const CHUNK = 100;
    for (let i = 0; i < batch.length; i += CHUNK) {
      const chunk = batch.slice(i, i + CHUNK);
      const vals = [];
      const placeholders = chunk.map((b, idx) => {
        const o = idx * 26;
        vals.push(
          b.name, b.company, b.email, b.email2,
          b.phone, b.phone2, b.market, b.city, b.state, b.zip,
          b.is_flipper, b.is_landlord, b.is_note_holder, b.is_lender, b.is_distressed,
          b.portfolio_owned, b.portfolio_value, b.portfolio_buy_avg,
          b.flipped_count, b.flipped_avg_profit,
          b.source, b.xleads_detail_url, b.is_dnc, b.is_litigator, b.active, b.notes
        );
        return `($${o+1},$${o+2},$${o+3},$${o+4},$${o+5},$${o+6},$${o+7},$${o+8},$${o+9},$${o+10},$${o+11},$${o+12},$${o+13},$${o+14},$${o+15},$${o+16},$${o+17},$${o+18},$${o+19},$${o+20},$${o+21},$${o+22},$${o+23},$${o+24},$${o+25},$${o+26})`;
      }).join(',');
      try {
        const res = await client.query(`
          INSERT INTO buyers
            (name, company, email, email2, phone, phone2, market, city, state, zip,
             is_flipper, is_landlord, is_note_holder, is_lender, is_distressed,
             portfolio_owned, portfolio_value, portfolio_buy_avg,
             flipped_count, flipped_avg_profit,
             source, xleads_detail_url, is_dnc, is_litigator, active, notes)
          VALUES ${placeholders}
          ON CONFLICT DO NOTHING
        `, vals);
        inserted += res.rowCount;
      } catch(e) {
        console.error(`  Chunk error: ${e.message}`);
      }
    }
  }

  // Summary
  const countRes = await client.query('SELECT COUNT(*) FROM buyers WHERE source = \'xleads_csv\'');
  const memphisRes = await client.query("SELECT COUNT(*) FROM buyers WHERE market = 'Memphis TN'");
  const flipperRes = await client.query('SELECT COUNT(*) FROM buyers WHERE is_flipper = true AND active = true');

  console.log('\n════ IMPORT COMPLETE ════');
  console.log(`Total rows:      ${total}`);
  console.log(`Inserted:        ${inserted}`);
  console.log(`Dupes skipped:   ${dupes}`);
  console.log(`No contact info: ${skipped}`);
  console.log(`\nDB totals:`);
  console.log(`  XLeads CSV buyers: ${countRes.rows[0].count}`);
  console.log(`  Memphis metro:     ${memphisRes.rows[0].count}`);
  console.log(`  Active flippers:   ${flipperRes.rows[0].count}`);

  await client.end();
}

main().catch(e => { console.error(e); process.exit(1); });
