#!/usr/bin/env python3
"""redBus fare tracker: fetch, compare with last run, notify on changes."""
import argparse, json, os, re, sqlite3, sys, time
from datetime import datetime
import requests, yaml
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "fares.db")

def load_env():
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.split("#")[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# ---------------- fetching ----------------
NAME_KEYS = ("travelsName", "TravelsName", "operatorName", "travels", "opName")
FARE_KEYS = ("fare", "Fare", "minFare", "startingFare", "price", "seatFare")
DEP_KEYS = ("departureTime", "DepartureTime", "dt", "depTime", "departure")
TYPE_KEYS = ("busType", "BusType", "busTypeName", "bt")
SEAT_KEYS = ("availableSeats", "AvailableSeats", "seatsAvailable", "availSeats", "seats", "seatCount", "availseat")

def pick(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]

def to_price(v):
    if isinstance(v, list) and v: v = v[0]
    try: return float(re.sub(r"[^\d.]", "", str(v)))
    except ValueError: return None

def to_seats(v):
    if v is None: return None
    try: return int(re.sub(r"[^\d]", "", str(v)))
    except ValueError: return None

def is_valid_name(v):
    """Reject values that are clearly not operator names (times, numbers, empty)."""
    if not v or not str(v).strip(): return False
    s = str(v).strip()
    if re.match(r"^\d{1,2}:\d{2}$", s): return False   # "21:30" is a time, not a name
    if re.match(r"^\d+$", s): return False               # pure number
    return True

def walk(node, out):
    if isinstance(node, dict):
        name_raw = pick(node, NAME_KEYS)
        fare = to_price(pick(node, FARE_KEYS))
        name = str(name_raw).strip() if is_valid_name(name_raw) else None
        if name and fare:
            seats_raw = pick(node, SEAT_KEYS)
            out.append({"operator": name,
                        "dep": str(pick(node, DEP_KEYS) or ""),
                        "type": str(pick(node, TYPE_KEYS) or ""),
                        "fare": fare,
                        "seats": to_seats(seats_raw)})
        for v in node.values(): walk(v, out)
    elif isinstance(node, list):
        for v in node: walk(v, out)

def dom_fallback(page):
    """Parse bus cards from aria-label (most reliable) then fall back to inner_text."""
    buses = []
    for card in page.query_selector_all("li[class*='tupleWrapper'], div[class*='bus-item'], li[class*='row-sec']"):
        aria = card.get_attribute("aria-label") or ""
        if aria:
            # aria-label format: "Om Sai Travels, A/C Sleeper (2+1). Departs 21:30, arrives 06:00.
            #                     Duration 8h 30m. Available seats 28 Seats. Price 1299 INR."
            op_m   = re.match(r"^([^,]+),", aria)
            type_m = re.search(r",\s*([^.]+)\.", aria)
            dep_m  = re.search(r"Departs\s+(\d{1,2}:\d{2})", aria, re.I)
            seat_m = re.search(r"Available seats\s+(\d+)", aria, re.I)
            price_m= re.search(r"Price\s+([\d,]+)\s+INR", aria, re.I)
            if op_m and price_m:
                buses.append({
                    "operator": op_m.group(1).strip(),
                    "dep":      dep_m.group(1) if dep_m else "",
                    "type":     type_m.group(1).strip() if type_m else "",
                    "fare":     float(price_m.group(1).replace(",", "")),
                    "seats":    int(seat_m.group(1)) if seat_m else None,
                })
            continue
        # fallback: parse inner text
        t = card.inner_text()
        prices = re.findall(r"₹\s?([\d,]+)", t)
        times  = re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
        lines  = [l for l in t.split("\n") if l.strip()]
        seat_m = re.search(r"(\d+)\s*[Ss]eat", t)
        if prices and lines:
            buses.append({"operator": lines[0].strip(),
                          "dep":  "%s:%s" % times[0] if times else "",
                          "type": next((l for l in lines if re.search(r"A/C|Sleeper|Seater|Volvo", l, re.I)), ""),
                          "fare": float(prices[0].replace(",", "")),
                          "seats": int(seat_m.group(1)) if seat_m else None})
    return buses

def fetch(cfg, date, debug=False):
    r = cfg["route"]
    d = datetime.strptime(date, "%Y-%m-%d").strftime("%d-%b-%Y")
    url = (f"https://www.redbus.in/bus-tickets/{r['from_slug']}-to-{r['to_slug']}"
           f"?fromCityName={r['from_city']}&toCityName={r['to_city']}&onward={d}&doj={d}")
    found, captured = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=not debug,
            args=["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = b.new_context(locale="en-IN", viewport={"width": 1366, "height": 900},
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        page = ctx.new_page()
        def on_resp(resp):
            if "json" in (resp.headers.get("content-type") or ""):
                try: captured.append(resp.json())
                except Exception: pass
        page.on("response", on_resp)
        for attempt in range(2):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                break
            except Exception as e:
                if attempt == 1:
                    raise
                print(f"  [fetch] goto attempt {attempt+1} failed, retrying...", file=sys.stderr)
                page.wait_for_timeout(2000)
        page.wait_for_timeout(8000)
        for _ in range(8):                       # scroll to load lazy results
            page.mouse.wheel(0, 3000); page.wait_for_timeout(1200)
        for c in captured: walk(c, found)
        if not found: found = dom_fallback(page)
        if debug or not found:
            page.screenshot(path=os.path.join(HERE, f"debug_{date}.png"), full_page=True)
            open(os.path.join(HERE, f"debug_{date}.html"), "w", encoding="utf-8").write(page.content())
        b.close()
    uniq = {}
    for bus in found:
        uniq[f"{bus['operator']}|{bus['dep']}|{bus['type']}"] = bus
    return list(uniq.values())

def passes(bus, f):
    if f.get("max_price") and bus["fare"] > f["max_price"]: return False
    if f.get("bus_type_contains") and f["bus_type_contains"].lower() not in bus["type"].lower(): return False
    if f.get("operator_contains") and f["operator_contains"].lower() not in bus["operator"].lower(): return False
    m = re.search(r"(\d{1,2}):(\d{2})", bus["dep"])
    if m:
        hhmm = f"{int(m.group(1)):02d}:{m.group(2)}"
        if f.get("departure_after") and hhmm < f["departure_after"]: return False
        if f.get("departure_before") and hhmm > f["departure_before"]: return False
    return True

# ---------------- storage / compare ----------------
def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS fares(date TEXT, key TEXT, operator TEXT, dep TEXT,
                 type TEXT, fare REAL, seats INTEGER, ts TEXT, PRIMARY KEY(date,key))""")
    c.execute("CREATE TABLE IF NOT EXISTS history(date TEXT, key TEXT, fare REAL, seats INTEGER, ts TEXT)")
    # migrate: add seats column if upgrading from older schema
    for col in ("seats INTEGER",):
        try: c.execute(f"ALTER TABLE fares ADD COLUMN {col}")
        except Exception: pass
        try: c.execute(f"ALTER TABLE history ADD COLUMN {col}")
        except Exception: pass
    return c

def compare(c, date, buses, cfg):
    a = cfg["alerts"]; msgs = []; now = datetime.now().isoformat(timespec="seconds")
    old = {r[0]: r for r in c.execute("SELECT key,operator,dep,type,fare,seats FROM fares WHERE date=?", (date,))}
    first_run = not old
    seen = set()
    low_seats_warn = a.get("low_seats_warn", 5)   # alert when seats drop to/below this
    for b in buses:
        key = f"{b['operator']}|{b['dep']}|{b['type']}"; seen.add(key)
        label = f"{b['operator']} {b['dep']} {b['type']}".strip()
        seats = b.get("seats")
        seats_str = f" [{seats} seats]" if seats is not None else ""
        if key in old:
            diff = b["fare"] - old[key][4]
            if abs(diff) >= a["min_change_rupees"]:
                arrow = "UP" if diff > 0 else "DOWN"
                msgs.append(f"{arrow}: {label}  Rs{old[key][4]:.0f} -> Rs{b['fare']:.0f} ({diff:+.0f}){seats_str}")
            # seat count change alert
            old_seats = old[key][5]
            if seats is not None and old_seats is not None and seats != old_seats:
                if seats <= low_seats_warn:
                    msgs.append(f"SEATS LOW: {label}  only {seats} left! (was {old_seats})")
                elif seats < old_seats:
                    msgs.append(f"SEATS: {label}  {old_seats} -> {seats} seats")
        elif not first_run and a["notify_on_new_bus"]:
            msgs.append(f"NEW: {label}  Rs{b['fare']:.0f}{seats_str}")
        if a.get("below_threshold") and b["fare"] <= a["below_threshold"] and (key not in old or old[key][4] > a["below_threshold"]):
            msgs.append(f"CHEAP: {label} at Rs{b['fare']:.0f}{seats_str}")
        c.execute("INSERT OR REPLACE INTO fares VALUES(?,?,?,?,?,?,?,?)",
                  (date, key, b["operator"], b["dep"], b["type"], b["fare"], seats, now))
        c.execute("INSERT INTO history VALUES(?,?,?,?,?)", (date, key, b["fare"], seats, now))
    if a["notify_on_sold_out"] and not first_run:
        for key, r in old.items():
            if key not in seen:
                msgs.append(f"GONE/SOLD OUT: {r[1]} {r[2]} {r[3]}")
                c.execute("DELETE FROM fares WHERE date=? AND key=?", (date, key))
    c.commit()
    return msgs, first_run

# ---------------- notifications ----------------
def notify(text, cfg):
    mobile = cfg["notify"]["my_mobile"]
    for ch in cfg["notify"]["channels"]:
        try:
            if ch == "telegram":
                token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
                chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
                if not token or not chat_id:
                    print(f"[notify:telegram] skipped – TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env",
                          file=sys.stderr)
                    continue
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat_id, "text": text}, timeout=20)
            elif ch == "ntfy":
                requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}", data=text.encode(),
                              headers={"Title": "Bus fare change"}, timeout=20)
            elif ch in ("twilio_sms", "twilio_whatsapp"):
                to = f"whatsapp:{mobile}" if ch == "twilio_whatsapp" else mobile
                frm = os.environ["TWILIO_FROM"]
                if ch == "twilio_whatsapp" and not frm.startswith("whatsapp:"): frm = "whatsapp:" + frm
                sid = os.environ["TWILIO_ACCOUNT_SID"]
                requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                              auth=(sid, os.environ["TWILIO_AUTH_TOKEN"]),
                              data={"To": to, "From": frm, "Body": text[:1500]}, timeout=20)
        except Exception as e:
            print(f"[notify:{ch}] failed: {e}", file=sys.stderr)

# ---------------- main ----------------
def get_dates(cfg):
    from datetime import date, timedelta
    r = cfg["route"]
    if r.get("dates"):
        ds = [str(d) for d in r["dates"]]
    else:
        ds = [(date.today() + timedelta(days=i)).isoformat() for i in range(1, int(r.get("track_next_days", 3)) + 1)]
    return [d for d in ds if d > date.today().isoformat()]

def get_chat_id():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)
    res = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=20).json()
    for u in res.get("result", []):
        m = u.get("message") or {}
        if m.get("chat"):
            print("Your TELEGRAM_CHAT_ID is:", m["chat"]["id"], "| name:", m["chat"].get("first_name")); return
    print("No messages found. Open your bot in Telegram, press Start, send 'hi', then run again.")

def bus_summary_line(b):
    seats_str = f"  [{b['seats']} seats]" if b.get("seats") is not None else ""
    return f"  {b['operator']} | {b['dep']} | {b['type']} | Rs{b['fare']:.0f}{seats_str}"

def run_once(cfg, debug=False):
    c = db()
    for date in get_dates(cfg):
        buses = [b for b in fetch(cfg, date, debug) if passes(b, cfg["filters"])]
        r = cfg["route"]
        print(f"[{datetime.now():%H:%M:%S}] {r['from_city']}->{r['to_city']} {date}: {len(buses)} buses")
        if not buses:
            print("  No buses parsed. See debug_*.png/html and adjust parser if needed."); continue
        msgs, first = compare(c, date, buses, cfg)
        if first:
            sorted_buses = sorted(buses, key=lambda b: b["fare"])
            lines = [f"Nandikotkur -> Bangalore  |  {date}  |  {len(buses)} buses found\n"]
            lines += [bus_summary_line(b) for b in sorted_buses[:30]]
            notify("\n".join(lines), cfg)
            # also print to console
            print("\n".join(lines))
        elif msgs:
            notify(f"{r['from_city']}->{r['to_city']} {date}\n" + "\n".join(msgs[:25]), cfg)
            print("\n".join(msgs))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, metavar="MIN", help="repeat every N minutes")
    ap.add_argument("--debug", action="store_true", help="visible browser + save screenshot/HTML")
    ap.add_argument("--test-notify", action="store_true")
    ap.add_argument("--get-chat-id", action="store_true", help="print your Telegram chat id")
    args = ap.parse_args()
    load_env()
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    if args.get_chat_id:
        get_chat_id(); sys.exit()
    if args.test_notify:
        notify("Test alert from bus tracker", cfg); sys.exit()
    while True:
        try: run_once(cfg, args.debug)
        except Exception as e: print("run failed:", e, file=sys.stderr)
        if not args.loop: break
        time.sleep(args.loop * 60)
