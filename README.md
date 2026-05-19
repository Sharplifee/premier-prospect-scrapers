# Premier Prospect — Scraper Pipeline

Automated lead intelligence scrapers for Utah County + Salt Lake County real estate.

Runs every 6 hours via GitHub Actions. Posts signals directly to Supabase.

## Setup

1. Fork or push this repo to GitHub
2. Go to **Settings → Secrets → Actions** and add:
   - `SUPABASE_URL` → `https://lbvaosyfikkpvcwksiph.supabase.co`
   - `SUPABASE_SERVICE_KEY` → your Supabase service role key
3. Done — scraper runs automatically

## Manual Run

Go to **Actions → Premier Prospect Scrapers → Run workflow**

## Sources

| Slug | County | Signal Type | Score |
|------|--------|-------------|-------|
| obituaries-herald | Utah | obituary | 55 |
| utah-county-tax-delinquency | Utah | tax_delinquency | 80 |
| slc-county-tax-sale | Salt Lake | tax_sale | 75 |
| wasatch-county-tax-sale | Wasatch | tax_sale | 65 |
| slc-public-surplus | Salt Lake | surplus | 45 |
| south-slc-permits | Salt Lake | permit | 35 |
| utah-county-codev | Utah | code_enforcement | 50 |
| ksl-fsbo | Utah | fsbo | 65 |
