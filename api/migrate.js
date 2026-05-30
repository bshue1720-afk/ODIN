const { Client } = require('pg');
const fs = require('fs');
const path = require('path');

const DB_URL = 'postgresql://postgres:yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC@kodama.proxy.rlwy.net:55551/railway';

async function run() {
  const client = new Client({ connectionString: DB_URL, ssl: { rejectUnauthorized: false } });
  await client.connect();
  console.log('Connected to Railway Postgres');

  const migrations = ['001_create_users.sql', '002_seed_data.sql'];
  for (const file of migrations) {
    const sql = fs.readFileSync(path.join(__dirname, 'migrations', file), 'utf8');
    console.log(`Running ${file}...`);
    await client.query(sql);
    console.log(`  ✓ ${file} done`);
  }

  await client.end();
  console.log('Migrations complete.');
}

run().catch(err => { console.error(err); process.exit(1); });
