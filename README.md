# Simone Marzullo — Real Estate Website

A mobile/tablet/desktop-friendly single-page site built around one simple
flow: the visitor picks **I Want to Sell**, **I Want to Buy**, or **Home
Valuation**, then a short address-first form. Every completed submission is
pushed straight to FollowUpBoss.

It's also set up (currently running in **FollowUpBoss-only mode**, see below)
to catch visitors who start a form and leave without finishing: if they left
an email or phone number, that partial info is pushed to FollowUpBoss too,
tagged **"Dropped Off"** so it's easy to spot in FollowUpBoss's own new-lead
notifications. The one thing this mode can't catch: someone who types only an
address (no email/phone) and leaves — there's no contact method for
FollowUpBoss to create a record from. The backend also has an optional direct
"Dropped out"-subject email path (SMTP) that would close that gap and guarantee
literally everything gets emailed, even info-free visits — it's simply off
until `SMTP_HOST` is configured (see step 2). Let me know if you'd like that
turned on later.

- `index.html` — the full site (static, no build step)
- `api/submit-lead.py` — Vercel Python serverless function that validates a
  submission and creates a FollowUpBoss lead via `POST /v1/events`
- `.env.example` — the environment variables the backend needs

This is meant to run on **Vercel** as a subdomain (e.g.
`home.simonemarzullo.com`) alongside the existing Squarespace site on the
root domain.

## The flow

- **I Want to Sell** → List on the Market / Sell Privately / Get a Cash
  Offer → address, then property details, then name/email/phone.
- **I Want to Buy** → New Home / Income Property / Flipper or Builder →
  current address, then specs/area/budget/timeline/financing, then
  name/email/phone.
- **Home Valuation** → address, name, email, phone, condition
  (original/renovated/recently built), optional notes — one step, no wizard.
- **Contact** (bottom of page, unchanged) — name, email, phone, message.

## 1. Get a FollowUpBoss API key

1. Log into FollowUpBoss.
2. Click the gear icon (Admin) in the top right → **API**.
3. Click **Create API Key**, name it something like "Website", and copy the
   key. You'll paste this into Vercel as `FUB_API_KEY` below.

This key alone is enough to make lead creation work — it authenticates every
request via HTTP Basic Auth.

### Optional: system registration

FollowUpBoss documents an additional step called "system registration" that
issues an `X-System` / `X-System-Key` header pair, mainly meant for software
vendors whose product is used by many different FollowUpBoss accounts. For a
single website tied to one account, it is **not required** — the backend
here works with just the API key and only adds those headers if you later
set `FUB_SYSTEM_KEY`. If you want to register anyway (it can raise API rate
limits), do it at https://apps.followupboss.com/system-registration and add
the result as `FUB_SYSTEM` / `FUB_SYSTEM_KEY`.

## 2. (Optional) Turn on direct "Dropped out" emails

Skip this section to run FollowUpBoss-only, as currently configured — nothing
else to set up, and it already handles every completed lead plus any
drop-off that left an email or phone number.

If you later want literally every visit (even a form abandoned with zero
contact info) to guarantee an email to Simone, set these and the backend
starts sending directly via SMTP (works with Gmail, Outlook/365, or any
transactional relay) in addition to FollowUpBoss:

- `SMTP_HOST` / `SMTP_PORT` — e.g. `smtp.gmail.com` / `587`
- `SMTP_USERNAME` / `SMTP_PASSWORD` — for Gmail, this must be an **App
  Password** (Google Account → Security → 2-Step Verification → App
  Passwords), not your normal login password
- `SMTP_FROM` — the "from" address (usually same as `SMTP_USERNAME`)
- `NOTIFY_TO` — where the notifications go (defaults to
  `Simone@SimoneMarzullo.com`)

If you'd rather use a transactional email API (Resend, SendGrid, Postmark)
instead of SMTP, let me know and I'll swap the sender to use that instead.

## 3. Push this project to GitHub

```bash
cd "RE Website 2026"
git add -A
git commit -m "Initial site"
```

Create a new repository on GitHub (via github.com or `gh repo create`), then:

```bash
git remote add origin <your-repo-url>
git push -u origin main
```

## 4. Deploy on Vercel

1. Go to https://vercel.com and sign in (or create an account).
2. **Add New… → Project**, then import the GitHub repo you just pushed.
3. Vercel will auto-detect the static `index.html` and the Python function in
   `api/` — no build configuration is needed.
4. Before the first deploy (or right after, then redeploy), go to
   **Project Settings → Environment Variables** and add everything from
   `.env.example`:  `FUB_API_KEY` (required), `FUB_SOURCE`,
   `FUB_SYSTEM`/`FUB_SYSTEM_KEY` (optional), and the `SMTP_*`/`NOTIFY_TO`
   variables from step 2.
5. Deploy.

## 5. Point home.simonemarzullo.com at it

1. In the Vercel project, go to **Settings → Domains** and add
   `home.simonemarzullo.com`.
2. Vercel will show you a DNS record to add — typically a `CNAME` record:
   - Host/Name: `home`
   - Value: `cname.vercel-dns.com`
3. Log into Squarespace → **Settings → Domains** → select
   `simonemarzullo.com` → **DNS Settings**, and add that CNAME record there.
   (This only adds the `home` subdomain — your existing root domain and any
   other pages stay on Squarespace untouched.)
4. DNS changes can take anywhere from a few minutes to a few hours to
   propagate. Vercel's domain screen will show a green check once it sees
   the record.

## 6. Test it end-to-end

Once deployed, try each of the 3 entry points (Sell → all 3 sub-options, Buy
→ all 3 sub-options, Home Valuation) plus the Contact form, and confirm:

- The person appears in FollowUpBoss under **People**, tagged correctly
  (e.g. Listing Lead / Cash Offer Lead / Income Property Buyer Lead /
  Flipper/Builder Lead / Home Valuation Lead / Contact Form Lead) so you can
  filter or route with FollowUpBoss action plans and smart lists.
- An email notification arrives at `NOTIFY_TO`.
- Start a form, fill in a couple of fields, then close the tab without
  submitting — an email with **"Dropped out"** in the subject should still
  arrive with whatever you'd entered.

If a submission ever fails (FollowUpBoss down, bad API key, etc.), the form
shows an inline message asking the visitor to call/text or email directly —
no lead is silently lost without the visitor knowing, and you still get an
email either way.

## Known gaps / things to add later

- **Photos**: `index.html` now tries to load `assets/headshot.jpg` (hero +
  about section) and `assets/agency-logo.png` (footer) automatically, falling
  back to the placeholder/text if those files don't exist yet. Drop your
  headshot and The Agency's logo file into an `assets/` folder at the project
  root with those exact names and they'll appear with no further changes —
  or send me the files and I'll place them.
- **Home valuation is lead-capture only** (matches the existing copy — you
  prepare and send the report within 48 hours). It does not call any
  automated valuation/AVM service.
- **Spam protection** is a honeypot field only (invisible to real visitors,
  usually enough to stop basic bots). If spam becomes a problem, adding
  Cloudflare Turnstile or reCAPTCHA to the forms is a reasonable next step.
- **Dropped-out detection** relies on the browser's `pagehide` event, which
  fires reliably on tab close/navigation across desktop and mobile browsers
  (including backgrounding on iOS Safari). It does not fire on every possible
  way a tab could vanish (e.g. force-quitting the OS process), so it's a
  strong best-effort signal, not a 100% guarantee.
- **No MLS/IDX listings feed** — this is a lead-capture site, not a property
  search site. Let me know if you want that added later.
