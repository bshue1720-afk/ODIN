const { Client } = require('pg');
const crypto = require('crypto');

const DB_URL = 'postgresql://postgres:yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC@kodama.proxy.rlwy.net:55551/railway';
const BASE_URL = 'https://odin-api-production-e85d.up.railway.app';

async function run() {
  const client = new Client({ connectionString: DB_URL, ssl: { rejectUnauthorized: false } });
  await client.connect();

  const { rows } = await client.query("SELECT id FROM users WHERE namespace = 'brock'");
  if (!rows.length) { console.error('Brock not found'); process.exit(1); }

  const token = crypto.randomBytes(32).toString('base64url');
  await client.query(
    "INSERT INTO invite_tokens (user_id, token, expires_at) VALUES ($1, $2, NOW() + INTERVAL '48 hours')",
    [rows[0].id, token]
  );
  await client.end();

  console.log(`\nSet your password here:\n${BASE_URL}/set-password?token=${token}\n`);
}

run().catch(err => { console.error(err); process.exit(1); });
