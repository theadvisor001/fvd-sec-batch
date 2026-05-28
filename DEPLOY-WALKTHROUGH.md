# SEC Nightly Batch Deploy — Step-by-Step

This walks you through deploying the GitHub Action in `~/Documents/fvd-sec-batch/`
to a GitHub repo so it pulls primary-filings fundamentals nightly and
publishes them as a static JSON URL the dashboard reads.

Total time: **10–12 minutes**. After this is done: SEC EDGAR data is always
available (no more "Failed to fetch"), 5 years of fundamentals for every
covered ticker, no rate-limit risk, no browser CORS problems, no UA header
games.

---

## Why this matters

The dashboard's biggest data gap was always SEC EDGAR live calls failing from
the browser (CORS + User-Agent restrictions). The pre-baked batch solves it
permanently:

- **Once a night**, GitHub Actions runs a Python script that pulls 17 GAAP
  concepts × 5 years for every ticker in `tickers.txt`
- The result is committed to `data/fundamentals.json` in your repo
- The dashboard reads `https://raw.githubusercontent.com/<you>/<repo>/main/data/fundamentals.json`
  on every autofill — no live SEC call needed
- All your covered names get full primary-filings data even if every other
  provider fails

Combined with v31's derive-and-backfill, this fills most "still missing"
fields in the coverage gaps panel.

---

## Step 0 — Prerequisites

You need:
1. A GitHub account (free) — sign up at https://github.com if you don't have one
2. The `gh` CLI installed locally — check with `gh --version`

If you don't have `gh`:
```bash
brew install gh
```

Or download from https://cli.github.com/

---

## Step 1 — Authenticate gh CLI

```bash
gh auth login
```

Walkthrough:
```
? What account do you want to log into?            GitHub.com
? What is your preferred protocol for Git operations?  HTTPS
? Authenticate Git with your GitHub credentials?   Yes
? How would you like to authenticate GitHub CLI?   Login with a web browser

! First copy your one-time code: XXXX-XXXX
Press Enter to open github.com in your browser...
```

A browser tab opens, you paste the code, click **"Authorize github"**, then
come back to the terminal:
```
✓ Authentication complete.
- gh config set -h github.com git_protocol https
- gh config set -h github.com pager less
✓ Configured git protocol
✓ Logged in as <your-username>
```

---

## Step 2 — Initialize the repo locally

```bash
cd ~/Documents/fvd-sec-batch
git init
git add .
git commit -m "Initial FVD SEC nightly batch"
```

Expected output:
```
Initialized empty Git repository in /Users/ahmadalsheikh/Documents/fvd-sec-batch/.git/
[main (root-commit) 4f3a1b2] Initial FVD SEC nightly batch
 4 files changed, 280 insertions(+)
 create mode 100644 .github/workflows/nightly.yml
 create mode 100644 README.md
 create mode 100644 scripts/pull_sec_fundamentals.py
 create mode 100644 tickers.txt
```

---

## Step 3 — Create the GitHub repo + push

```bash
gh repo create fvd-sec-batch --public --source=. --push
```

The `--public` flag is important — the dashboard reads from
`raw.githubusercontent.com`, which only serves public repos without auth.
(If you want it private, you'd need a Personal Access Token in the dashboard
fetch call — more complexity.)

Expected output:
```
✓ Created repository <your-username>/fvd-sec-batch on GitHub
✓ Added remote https://github.com/<your-username>/fvd-sec-batch.git
✓ Pushed commits to https://github.com/<your-username>/fvd-sec-batch.git
```

---

## Step 4 — Add the SEC User-Agent secret

SEC's published API access policy requires identifying contact info in the
`User-Agent` header. They will rate-limit or block anonymous requests.

```bash
gh secret set SEC_UA --body "FVD/1.0 theadvisor001@gmail.com"
```

Use your **real email**. The expected output:
```
✓ Set Actions secret SEC_UA for <your-username>/fvd-sec-batch
```

---

## Step 5 — Trigger the first run manually

The cron schedule is 06:30 UTC daily, but you don't want to wait. Trigger now:

```bash
gh workflow run nightly.yml
```

Expected:
```
✓ Created workflow_dispatch event for nightly.yml at main
To see runs for this workflow, try: gh run list --workflow=nightly.yml
```

Watch it run:
```bash
gh run list --workflow=nightly.yml
gh run watch
```

Expected (after ~3 minutes):
```
✓ main FVD SEC Fundamentals Nightly Batch · 8123456789
Triggered via workflow_dispatch about 3 minutes ago

JOBS
✓ batch in 2m45s (ID 22765432111)
```

If you want to see exactly what the script printed:
```bash
gh run view --log
```

You should see lines like:
```
Loading CIK map …
[1/12] MA (CIK 1141391) … ok (11 concepts, 4 derived)
[2/12] V (CIK 1403161) … ok (11 concepts, 4 derived)
...
[12/12] KO (CIK 21344) … ok (11 concepts, 4 derived)

Wrote 12 tickers → data/fundamentals.json
```

---

## Step 6 — Find your raw JSON URL

```bash
echo "https://raw.githubusercontent.com/$(gh api user --jq .login)/fvd-sec-batch/main/data/fundamentals.json"
```

Output:
```
https://raw.githubusercontent.com/<your-username>/fvd-sec-batch/main/data/fundamentals.json
```

Copy that URL.

Verify it loads:
```bash
curl -s "https://raw.githubusercontent.com/<your-username>/fvd-sec-batch/main/data/fundamentals.json" | head -c 200
```

Expected: a JSON blob starting with `{"version": 1, "generated_at": "...", "tickers": {"MA": {...`

---

## Step 7 — Wire the URL into the dashboard

1. Open the dashboard: http://localhost:8765/fair-value-verification-dashboard.html
2. Click **⚙ API keys** in the header
3. Find the field labeled **SEC fundamentals URL** (with the green "v25 data bedrock" badge)
4. Paste your raw URL from Step 6
5. Click **Save keys**
6. **Reload the page** (Cmd+R)

---

## Step 8 — Verify

Run an Auto-fill for any preset (e.g. MA). The provider report should now
include:
- **SEC nightly batch** — green, "ok (10 fields, pre-baked YYYY-MM-DDT06:30:00Z)"

The Coverage gaps panel in Stage 9 should drop from ~50% to ~80–90%
populated, because SEC supplies all the multi-year statements (NI, CFO,
CapEx, Total Assets, Receivables, Gross Profit, Interest Expense, etc.) that
derive-and-backfill needs to fill the rest.

---

## Add or remove tickers from the covered universe

Edit `tickers.txt` locally:
```bash
cd ~/Documents/fvd-sec-batch
echo "BAC" >> tickers.txt
git add tickers.txt && git commit -m "add BAC to covered universe"
git push
```

Next nightly run picks it up. Or trigger immediately:
```bash
gh workflow run nightly.yml
```

---

## Schedule & costs

- Runs **nightly at 06:30 UTC** (= 02:30 ET, after most US filings settle)
- Free tier of GitHub Actions: **2,000 minutes/month** for public repos —
  unlimited, effectively. Each run takes ~3 minutes, so 30 runs/month = 90
  min, well within budget.
- SEC's 10 req/s rate limit handled via 120ms sleep between tickers
- Bundle size: ~250 KB for 12 tickers; loads in <500ms from CDN

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Workflow fails: "SEC_UA environment variable required" | `gh secret set SEC_UA --body "FVD/1.0 your@email.com"` |
| `404` from raw.githubusercontent.com | Confirm repo is public: `gh repo edit --visibility public` |
| Dashboard says "ticker not in nightly bundle" | Ticker not in `tickers.txt` — add it and re-run the workflow |
| SEC throttling errors in run log | Increase the `time.sleep(0.12)` in `scripts/pull_sec_fundamentals.py` to `0.5` and re-deploy |
| Workflow doesn't run at 06:30 UTC | GitHub free tier sometimes delays cron up to 15 min; for tightly-scheduled runs, use `gh workflow run nightly.yml` manually or upgrade to GitHub Team |

---

## What you just unlocked

- ✅ SEC EDGAR data always available (5 years × 17 concepts per ticker)
- ✅ Coverage gaps panel drops to ~10–20% missing (was 50%+)
- ✅ Derive-and-backfill has 5x more raw material to work with
- ✅ No more "Alpha Vantage rate limit reached" failures eating into your daily quota for fundamentals
- ✅ Free, primary-source, audit-trail data — the institutional gold standard
- ✅ Dashboard becomes resilient: if every aggregator API is down on a day, SEC bundle still has yesterday's full filings

Combined with the Cloudflare worker (see `~/Documents/fvd-worker/DEPLOY-WALKTHROUGH.md`),
you now have an institutional-grade data ingestion stack on free tiers.
