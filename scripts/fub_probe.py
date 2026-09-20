import base64, json, os, time, urllib.request, urllib.error
KEY = os.environ["FUB_API_KEY"]
BASE = "https://api.followupboss.com/v1"
def call(method, path, payload=None, hdrs=None):
    h = {"Authorization": "Basic " + base64.b64encode(f"{KEY}:".encode()).decode(), "Content-Type": "application/json"}
    h.update(hdrs or {})
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode() if payload is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode(); return r.status, dict(r.headers), raw[:600]
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()[:600]
ts = int(time.time())
sysh = {}
if os.environ.get("FUB_SYSTEM") and os.environ.get("FUB_SYSTEM_KEY"):
    sysh = {"X-System": os.environ["FUB_SYSTEM"], "X-System-Key": os.environ["FUB_SYSTEM_KEY"]}
print("system headers configured:", bool(sysh))
print("GET /me:", call("GET", "/me")[0:3:2])
def ev(label, payload, hdrs=None):
    s, h, b = call("POST", "/events", payload, hdrs)
    print(f"\n[{label}] http={s} body={b[:300]!r}")
    return s, b
base_person = lambda n: {"firstName": "Probe", "lastName": n, "emails": [{"value": f"probe.{n.lower()}.{ts}@gmail.com"}]}
# A: minimal, no system/source fields
ev("A minimal", {"type": "Property Inquiry", "person": base_person("Aaa")})
# B: with source + system name only
ev("B source+system", {"source": "Simone Marzullo Website", "system": "Simone Marzullo Website", "type": "Property Inquiry", "message": "probe B", "person": base_person("Bbb")})
# C: full current payload shape
p = base_person("Ccc"); p["tags"] = ["Buyer Lead", "Match Tool"]; p["background"] = "probe"
ev("C full", {"source": "Simone Marzullo Website", "system": "Simone Marzullo Website", "type": "Property Inquiry",
              "pageUrl": f"https://marzullore.com/match?ts={ts}", "pageTitle": "x", "message": "probe C", "person": p})
# D: with X-System headers if any
if sysh:
    ev("D with X-System hdrs", {"source": "Simone Marzullo Website", "type": "Property Inquiry", "person": base_person("Ddd")}, sysh)
# E: phone only
ev("E phone only", {"type": "Property Inquiry", "person": {"firstName": "Probe", "lastName": "Eee", "phones": [{"value": f"310555{ts%10000:04d}"}]}})
# F: name only
ev("F name only", {"type": "Property Inquiry", "person": {"firstName": "Probe", "lastName": f"Fff{ts}"}})
# G: General Inquiry type
ev("G General Inquiry", {"type": "General Inquiry", "person": base_person("Ggg")})
# cleanup: delete probe people
for q in ("Aaa","Bbb","Ccc","Ddd","Eee","Ggg"):
    pass
s, h, b = call("GET", "/people?name=Probe&limit=50&sort=-created")
try:
    for x in json.loads(b + ("" if b.endswith("}") else ""))["people"] if False else []: pass
except Exception: pass
s, h, raw = None, None, None
import urllib.parse
st, hh, body = call("GET", "/people?" + urllib.parse.urlencode({"limit": 50, "sort": "-created", "fields": "id,firstName,lastName"}))
try:
    full = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/people?limit=50&sort=-created&fields=id,firstName,lastName", headers={"Authorization": "Basic " + base64.b64encode(f"{KEY}:".encode()).decode()}), timeout=30).read().decode())
    ids = [x["id"] for x in full.get("people", []) if x.get("firstName") == "Probe"]
    print("\nprobe contacts found:", ids)
    for i in ids:
        print("delete", i, call("DELETE", f"/people/{i}")[0])
except Exception as e:
    print("cleanup error", e)
