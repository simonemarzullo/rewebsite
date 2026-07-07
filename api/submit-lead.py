from http.server import BaseHTTPRequestHandler
import json
import os
import re
import base64
import smtplib
import urllib.request
import urllib.error
from email.message import EmailMessage

FUB_EVENTS_URL = "https://api.followupboss.com/v1/events"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

BUY_INTENT_CONFIG = {
    "new-home": {"tag": "New Home Buyer Lead", "title": "Buyer Inquiry — New Home",
                 "desc": "Looking for a new home (primary residence)."},
    "income-property": {"tag": "Income Property Buyer Lead", "title": "Buyer Inquiry — Income Property",
                         "desc": "Looking to buy an income property."},
    "flip-builder": {"tag": "Flipper/Builder Lead", "title": "Buyer Inquiry — Flip/Builder Project",
                      "desc": "Flipper or builder looking for a next project."},
}
DEFAULT_BUY = {"tag": "Buyer Lead", "title": "Buyer Inquiry", "desc": ""}

SELL_TYPE_CONFIG = {
    "list-market": {"tag": "Listing Lead", "title": "Seller Inquiry — List on Market",
                    "desc": "Wants to list the house on the market."},
    "sell-privately": {"tag": "Private Sale Lead", "title": "Seller Inquiry — Private Sale",
                       "desc": "Wants to sell the house privately (off-market)."},
    "cash-offer": {"tag": "Cash Offer Lead", "title": "Seller Inquiry — Cash Offer",
                   "desc": "Wants a cash offer."},
}
DEFAULT_SELL = {"tag": "Seller Lead", "title": "Seller Inquiry", "desc": ""}

# (label, payload key) pairs used to render both the FollowUpBoss "message"
# field and the notification email body -- a single source of truth so a
# dropped-out visitor's partial answers show up the same way a completed
# submission's do.
FIELD_LABELS = {
    "valuation": [
        ("Property Address", "address"), ("Full Name", "name"),
        ("Email", "email"), ("Phone", "phone"),
        ("Condition", "condition"), ("Notes", "notes"),
    ],
    "sell": [
        ("Property Address", "address"), ("Property Type", "propertyType"),
        ("Estimated Value", "estValue"), ("Condition", "condition"),
        ("Timeline", "timeline"), ("Full Name", "name"),
        ("Email", "email"), ("Phone", "phone"), ("Notes", "notes"),
    ],
    "buy": [
        ("Property Type", "propertyType"),
        ("Budget", "budget"), ("Min Bedrooms", "minBeds"), ("Min Bathrooms", "minBaths"),
        ("Preferred Areas", "areas"), ("Timeline", "timeline"), ("Financing", "financing"),
        ("Full Name", "name"), ("Email", "email"), ("Phone", "phone"),
    ],
    "contact": [
        ("Full Name", "name"), ("Email", "email"), ("Phone", "phone"), ("Message", "message"),
    ],
}


def clean(value):
    return str(value).strip() if value is not None else ""


def digits_only(value):
    return re.sub(r"\D", "", value or "")


def resolve_form_meta(form_type, data):
    """Returns (fub_event_type, fub_tag, human_title, intent_description)."""
    if form_type == "valuation":
        return "Seller Inquiry", "Home Valuation Lead", "Home Valuation Request", None
    if form_type == "sell":
        cfg = SELL_TYPE_CONFIG.get(clean(data.get("saleType")), DEFAULT_SELL)
        return "Seller Inquiry", cfg["tag"], cfg["title"], (cfg["desc"] or None)
    if form_type == "buy":
        cfg = BUY_INTENT_CONFIG.get(clean(data.get("intent")), DEFAULT_BUY)
        return "Property Inquiry", cfg["tag"], cfg["title"], (cfg["desc"] or None)
    if form_type == "contact":
        return "General Inquiry", "Contact Form Lead", "Contact Form Message", None
    return None, "Website Lead", f"Website Form ({form_type or 'unknown'})", None


def build_message(form_type, data, intent_desc=None):
    parts = []
    if intent_desc:
        parts.append(intent_desc)
    for label, key in FIELD_LABELS.get(form_type, []):
        v = clean(data.get(key))
        if v:
            parts.append(f"{label}: {v}")
    if not parts:
        parts.append("(No information was entered before the visitor left the page.)")
    return "\n".join(parts)


def build_person(data, tag, extra_tags=None):
    tags = [tag, "Website Lead"]
    if extra_tags:
        tags.extend(extra_tags)
    person = {"tags": tags}
    name = clean(data.get("name"))
    if name:
        first, _, last = name.partition(" ")
        person["firstName"] = first
        if last:
            person["lastName"] = last
    email = clean(data.get("email"))
    phone = clean(data.get("phone"))
    if email and EMAIL_RE.match(email):
        person["emails"] = [{"value": email}]
    if phone:
        person["phones"] = [{"value": phone}]
    return person


def build_property(form_type, data):
    # Buy forms only collect the buyer's *current* address, not a target
    # property, so there is nothing to attach as a FollowUpBoss property.
    if form_type not in ("valuation", "sell"):
        return None
    address = clean(data.get("address"))
    if not address:
        return None
    prop = {"street": address, "state": "CA"}
    if clean(data.get("propertyType")):
        prop["propertyType"] = clean(data.get("propertyType"))
    return prop


def push_to_followupboss(form_type, event_type, tag, data, message_body, extra_tags=None):
    """Best-effort FollowUpBoss sync. Returns True on success, never raises."""
    api_key = os.environ.get("FUB_API_KEY")
    if not api_key:
        print("submit-lead: FUB_API_KEY is not configured")
        return False

    event_payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": event_type,
        "message": message_body,
        "person": build_person(data, tag, extra_tags),
    }
    prop = build_property(form_type, data)
    if prop:
        event_payload["property"] = prop

    req = urllib.request.Request(
        FUB_EVENTS_URL,
        data=json.dumps(event_payload).encode("utf-8"),
        method="POST",
    )
    auth = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/json")

    # System registration is optional for a single-account integration; only
    # send these headers if both are configured (see README).
    system_name = os.environ.get("FUB_SYSTEM")
    system_key = os.environ.get("FUB_SYSTEM_KEY")
    if system_name and system_key:
        req.add_header("X-System", system_name)
        req.add_header("X-System-Key", system_key)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"submit-lead: FollowUpBoss API error {e.code}: {body}")
        return False
    except Exception as e:
        print(f"submit-lead: unexpected error calling FollowUpBoss: {e}")
        return False


def send_notification_email(subject, body):
    host = os.environ.get("SMTP_HOST")
    if not host:
        print("submit-lead: SMTP_HOST not configured, skipping email notification")
        return
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM") or username or "noreply@simonemarzullo.com"
    recipient = os.environ.get("NOTIFY_TO", "Simone@SimoneMarzullo.com")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except Exception as e:
        print(f"submit-lead: failed to send notification email: {e}")


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length else b""

        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON body"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "Invalid payload"})
            return

        # Hidden field real users never fill in; bots that do get a fake
        # success with no FollowUpBoss call, no email, no trace kept.
        if clean(data.get("hp")):
            self._send_json(200, {"ok": True})
            return

        form_type = clean(data.get("formType"))
        event_type, tag, title, intent_desc = resolve_form_meta(form_type, data)
        abandoned = bool(data.get("abandoned"))
        message_body = build_message(form_type, data, intent_desc)

        # A visitor who left the page before finishing: always try the direct
        # "Dropped out" email (a no-op until SMTP_* env vars are set), and
        # also sync to FollowUpBoss whenever there's an email or phone to
        # de-dup on -- so with email notifications off, FollowUpBoss (and its
        # own notification settings) is still the guaranteed catch for any
        # drop-off that left contact info.
        if abandoned:
            send_notification_email(f"Dropped out - {title}", message_body)
            email_v = clean(data.get("email"))
            phone_v = digits_only(data.get("phone"))
            if event_type and (email_v or phone_v):
                dropped_message = "Visitor left the page before finishing this form.\n\n" + message_body
                push_to_followupboss(form_type, event_type, tag, data, dropped_message, extra_tags=["Dropped Off"])
            self._send_json(200, {"ok": True})
            return

        if event_type is None:
            self._send_json(400, {"ok": False, "error": "Unknown form type"})
            return

        email = clean(data.get("email"))
        phone = digits_only(data.get("phone"))
        if not email and not phone:
            self._send_json(400, {"ok": False, "error": "Email or phone is required"})
            return
        if email and not EMAIL_RE.match(email):
            self._send_json(400, {"ok": False, "error": "Invalid email address"})
            return

        lead_name = clean(data.get("name")) or "Website Visitor"
        ok = push_to_followupboss(form_type, event_type, tag, data, message_body)
        if ok:
            send_notification_email(f"New Lead: {title} - {lead_name}", message_body)
            self._send_json(200, {"ok": True})
        else:
            send_notification_email(
                f"New Lead: {title} - {lead_name} (FollowUpBoss sync failed)", message_body
            )
            self._send_json(502, {"ok": False, "error": "Failed to reach FollowUpBoss"})

    def do_GET(self):
        self._send_json(405, {"ok": False, "error": "Method not allowed"})
