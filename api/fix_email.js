const { Client } = require('pg');

const DB_URL = 'postgresql://postgres:yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC@kodama.proxy.rlwy.net:55551/railway';

async function run() {
  const client = new Client({ connectionString: DB_URL, ssl: { rejectUnauthorized: false } });
  await client.connect();
  await client.query("UPDATE users SET email = 'shueboxllc@gmail.com' WHERE namespace = 'brock'");
  console.log('Email updated.');
  await client.end();
}

run().catch(err => { console.error(err); process.exit(1); });
