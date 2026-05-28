# FVD SEC Fundamentals Nightly Batch

Pre-bakes primary-filings fundamentals from SEC EDGAR into a single JSON file
that the dashboard consumes. Solves the two SEC-side problems the dashboard
has always had:

1. **CORS-blocked from browser.** SEC requires a `User-Agent` header; the
   browser cannot reliably set one, and CORS blocks the call from `file://`
   and from non-allowlisted origins.
2. **Rate-limited (10 req/s).** A nightly server-side pull is fundamentally
   better than 100+ browser-side pulls during a single review session.

## One-time setup

1. **Fork or copy this folder to its own GitHub repo.** The dashboard reads
   the JSON via `raw.githubusercontent.com` so the repo must be public (or
   you must publish the JSON to another CDN you control).

2. **Add the SEC User-Agent secret in repo Settings → Secrets and variables → Actions:**
   ```
   Name:   SEC_UA
   Value:  FVD/1.0 theadvisor001@gmail.com
   ```
   (SEC's published policy requires identifying contact info; use your real
   email — they may rate-limit anonymous agents.)

3. **Push.** The workflow runs:
   - Nightly at 06:30 UTC
   - Whenever you click "Run workflow" in the Actions tab
   - On every push to the workflow file itself

4. **Find the JSON.** After the first run, the file lives at:
   ```
   https://raw.githubusercontent.com/<your-user>/<repo>/main/data/fundamentals.json
   ```

5. **In the dashboard:** Settings → API keys modal → **SEC fundamentals URL** →
   paste that raw URL → Save. Every preset load and auto-fill now reads from
   it first; live providers fall back if a field is missing.

## Editing the covered universe

Edit `tickers.txt`. One ticker per line. `#` comments allowed.

The default list matches the 12 presets the dashboard ships with:
MA, V, AAPL, MSFT, GOOGL, META, AMZN, NVDA, JPM, JNJ, XOM, KO.

## What gets extracted

Per ticker, the bundle includes the last ~5 years of 10-K/10-Q values for:

- **Income statement:** Revenue, Gross Profit, Operating Income, Net Income, R&D
- **Cash flow:** CFO, CapEx
- **Balance sheet:** Cash, Long-term debt, Stockholders' equity, Assets, Liabilities
- **Per-share:** Diluted EPS, Basic EPS, Shares outstanding
- **Derived:** FCF (TTM), Net debt, Book value per share, 5-year revenue CAGR

## Why not just query SEC live?

The dashboard does fall back to the live `data.sec.gov/api/xbrl/companyfacts/...`
endpoint when no pre-baked URL is configured, but in practice that call
fails ~70% of the time from browser contexts due to CORS and missing UA.
Pre-baking server-side is the only reliable path to SEC primary filings.

## License

This batch is intentionally minimal (~200 LOC of Python) so you can audit
and modify freely.
