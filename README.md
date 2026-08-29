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

## Off-market opportunities page

`/off-market` is a private, password-gated page for sharing off-market
listings with select buyers/investors before they hit the open market.

- **Access code**: set `OFFMARKET_PASSWORD` in Vercel (Project Settings →
  Environment Variables) to whatever code you want to hand out, then
  redeploy. It's a single shared code, not a per-person login — anyone with
  the code plus a valid email gets in. Change it anytime (redeploy required)
  to rotate access; rotating it also signs out everyone currently logged in.
- **How visitors get in**: they go to `/off-market`, enter their email and
  the code. Their email is logged to FollowUpBoss (tagged "Off-Market
  Access") and you get an email notification, so you know who's asked in.
  After that, a cookie keeps them signed in for 24 hours before they need
  to re-enter the code.
- **Adding listings**: there's no admin form for this — message me the
  property details (address, price, beds/baths/sqft, a short description,
  and a photo) and I'll add them to `api/offmarket.py` and deploy. This
  matches how every other page on this site is updated.
- **Not linked anywhere public**: no nav or footer link points to it, so
  it's only reachable by whoever you send the URL to directly. Let me know
  if you'd rather have a small discreet link somewhere (e.g. the footer).
- **Not in the sitemap**, and the page sends `noindex` — it won't show up
  in Google.

## Client dashboard and admin panel

Two private, password-gated sections (`api/portal.py`):

- **`/clientaccess`** — a per-client login where a seller can check on a
  listing's progress: showings, emails sent, calls made, texts sent, offers
  received (price, cash/loan, contingencies, close of escrow), and feedback
  (split into showing feedback and pricing feedback from agents/buyers).
  Linked from the nav as "Log In" so clients can find their way back in
  without needing the raw URL.
- **`/admin`** — your own dashboard to run it: create client accounts
  (email + a password you choose — tell them what it is, there's no
  "forgot password" flow yet), add listings to a client and update their
  numbers, add offers and feedback, and manage the list of contingency
  types offers can be tagged with (add new ones anytime, e.g. "Sale of
  Buyer's Property"). Deliberately **not** linked anywhere on the site
  (not in the nav, footer, or sitemap) — bookmark it directly.

Both live in one Vercel function (`api/portal.py`) rather than the usual
one-file-per-page pattern — Vercel's Hobby plan caps a deployment at 12
Serverless Functions, and this site was already at 11, so a combined file
keeps the total at 12 instead of going over. A team-member resource hub
was designed alongside this but held back for a later phase (would need
either a paid Vercel plan for more functions, or folding into this same
file too).

This is the first feature on this site backed by a real database, since it
needs to remember data between visits (everything else is either static or
computed on the fly). One-time setup:

1. **Add a database**: Vercel dashboard → Storage → Create Database →
   Postgres → connect it to this project. Vercel sets `POSTGRES_URL`
   automatically — you don't need to type it in yourself.
2. **Create the tables**: open the new database in the Vercel dashboard →
   Query tab, paste in the contents of `db/schema.sql`, and run it once.
   Safe to run again later if needed (it won't duplicate anything).
3. **Set `ADMIN_PASSWORD`** (your own login for `/admin`) and
   `SESSION_SECRET` (a random string that keeps login sessions secure —
   generate one with `openssl rand -hex 32`) in Vercel's Environment
   Variables, then redeploy.

**Cancelling an account**: deactivating a client from `/admin` blocks their
login immediately, but keeps all their data — it moves into a "History"
section in `/admin` instead of being deleted, and can be reactivated later.

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
