import base64, io, os, re, secrets, time, urllib.parse
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from d1 import q
from mailer import invite_content, send_mail, describe_sender

st.set_page_config(page_title="Supplier Code of Conduct Tracker", page_icon="🧭", layout="wide")

# Constants for Shared Mailbox & Microsoft Form Sign-Off URL
SHARED_MAILBOX = "wps.supplier.Governance@wilhelmsen.com"
MS_SIGNOFF_FORM_URL = "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=rScCXcw7BkGM8xbnNfO0xPhiTJZNiP5Em5O0TKXRRCxURFU3Q01aRjhZNDlOVlhFSzRZSjFDSjBVUS4u"

# Hide Streamlit's built-in menu/header/footer for every viewer, on every page.
# Also set via [client] toolbarMode in .streamlit/config.toml.
st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)
NAVY, BLUE, TEAL, GREEN, PURPLE = "#1f4e79", "#2a5a8c", "#1fa3b5", "#2e9e6b", "#6b4c9a"
st.markdown(f"""<style>
h1{{color:{NAVY};font-size:1.7rem!important}} h2,h3{{color:{BLUE}}}
[data-testid=stSidebar]{{background:#eaf1f8}}
.kpi{{background:#fff;border-radius:12px;padding:14px 18px;box-shadow:0 1px 6px rgba(0,0,0,.12);color:#5b6b7b;font-size:13px}}
.kpi b{{display:block;font-size:34px;line-height:1.2}}
.stButton button,.stDownloadButton button{{border-radius:8px}}
.cert-box{{background:#f8fbfd;border:2px solid {NAVY};border-left:8px solid {GREEN};border-radius:10px;padding:18px 22px;margin:10px 0;color:#1b2a3a}}
</style>""", unsafe_allow_html=True)

def banner():
    logo = ""
    if os.path.exists("logo.png"):
        b = base64.b64encode(open("logo.png", "rb").read()).decode()
        logo = f'<img src="data:image/png;base64,{b}" style="height:46px;background:#fff;padding:6px 10px;border-radius:8px;margin-right:18px">'
    st.markdown(f'<div style="background:linear-gradient(90deg,{NAVY},{BLUE} 60%,{TEAL});padding:22px 28px;border-radius:14px;display:flex;align-items:center;margin-bottom:18px">{logo}<div><div style="color:#fff;font-size:26px;font-weight:700">Supplier Code of Conduct</div><div style="color:#dbe9f6;font-size:14px">Awareness Campaign Tracker · Wilhelmsen Port Services</div></div></div>', unsafe_allow_html=True)

banner()

if not st.session_state.get("ok"):
    st.title("Supplier Code of Conduct – Tracker")
    pw = st.text_input("Admin password", type="password")
    if st.button("Login"):
        if pw == st.secrets["ADMIN_PASSWORD"]:
            st.session_state.ok = True
            st.rerun()
        st.error("Wrong password")
    st.stop()

COLS = ["id", "company", "contact_name", "email", "token", "signed_off_at", "last_sent", "sends", "first_click", "thankyou_at"]
SQL = """SELECT s.id,s.company,s.contact_name,s.email,s.token,s.signed_off_at,
 (SELECT MAX(sent_at) FROM sends WHERE supplier_id=s.id AND status='ok' AND (kind IS NULL OR kind!='thankyou')) last_sent,
 (SELECT COUNT(*) FROM sends WHERE supplier_id=s.id AND status='ok' AND (kind IS NULL OR kind!='thankyou')) sends,
 (SELECT MIN(ts) FROM events WHERE token=s.token AND type='click') first_click,
 (SELECT MAX(sent_at) FROM sends WHERE supplier_id=s.id AND status='ok' AND kind='thankyou') thankyou_at
 FROM suppliers s ORDER BY s.company"""

def load():
    df = pd.DataFrame(q(SQL), columns=COLS)
    df["sends"] = df["sends"].fillna(0).astype(int)
    def stat(r):
        if pd.notna(r.signed_off_at): return "Signed off"
        if pd.notna(r.first_click): return "Course started"
        return "Sent" if r.sends else "Not sent"
    df["status"] = df.apply(stat, axis=1) if len(df) else pd.Series(dtype=str)
    return df

def log(action, detail):
    q("INSERT INTO log(action,detail) VALUES(?,?)", [action, detail])

EMAIL_RE = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"

def read_table(up):
    """Reads an uploaded .xlsx/.csv. Handles Excel/Microsoft Forms CSVs (any encoding, any separator,
    trailing commas) and drops blank rows/columns."""
    name = up.name.lower()
    if not name.endswith(".csv"):
        d = pd.read_excel(up)
    else:
        raw = up.getvalue()
        encs = ["utf-16"] if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ["utf-8-sig", "cp1252", "latin-1"]
        d, last_err = None, None
        for enc in encs:
            for sep in (",", ";", "\t"):
                try:
                    t = pd.read_csv(io.BytesIO(raw), encoding=enc, sep=sep, index_col=False)
                    if t.shape[1] > 1:
                        d = t
                        break
                except Exception as e:
                    last_err = e
            if d is not None:
                break
        if d is None:
            for enc in encs:  # last resort: skip any malformed lines
                try:
                    d = pd.read_csv(io.BytesIO(raw), encoding=enc, sep=None, engine="python", on_bad_lines="skip", index_col=False)
                    break
                except Exception as e:
                    last_err = e
        if d is None:
            raise last_err
    d = d.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    d.columns = [str(c).strip() for c in d.columns]
    return d

def map_supplier_cols(d):
    """Finds the email, company and contact columns in an uploaded supplier list, whatever the headings are called."""
    low = {c: str(c).strip().lower() for c in d.columns}
    em = next((c for c, l in low.items() if "mail" in l), None)
    if em is None:  # no heading with "mail": use the column that holds the most @ signs
        best, em = 0, None
        for c in d.columns:
            n = int(d[c].astype(str).str.contains("@").sum())
            if n > best:
                best, em = n, c
    co = next((c for c, l in low.items() if c != em and any(k in l for k in ("company", "supplier", "vendor", "organi", "firm"))), None)
    nm = next((c for c, l in low.items() if c not in (em, co) and any(k in l for k in ("contact", "person", "name"))), None)
    return em, co, nm

def cell(r, col):
    return "" if col is None or pd.isna(r[col]) else str(r[col]).strip()

def supplier_template_bytes():
    buf = io.BytesIO()
    sample = pd.DataFrame({
        "company": ["Example Shipping Pvt Ltd", "Sample Logistics LLC", ""],
        "contact_name": ["Asha Rao", "John Smith", ""],
        "email": ["asha.rao@example.com", "john.smith@example.com", ""],
    })
    howto = pd.DataFrame({"How to fill this sheet": [
        "1. Keep the three headings in row 1 of the 'Suppliers' sheet: company, contact_name, email.",
        "2. One supplier contact per row. Email is the only column that must be filled.",
        "3. Delete the example rows, then paste your own list.",
        "4. Save as .xlsx (or .csv) and upload it on the Suppliers page.",
        "5. People already in the app are skipped, so you can upload the same list again safely.",
    ]})
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        sample.to_excel(xw, sheet_name="Suppliers", index=False)
        howto.to_excel(xw, sheet_name="How to fill", index=False)
        for ws, widths in ((xw.sheets["Suppliers"], (34, 24, 36)), (xw.sheets["How to fill"], (100,))):
            for i, w in enumerate(widths):
                ws.column_dimensions["ABC"[i]].width = w
    return buf.getvalue()

def pick_email_col(d):
    # of the columns with "email" in the name, pick the one that really contains addresses
    # (ignores MS Forms built-in "Email" column when set to "anonymous")
    cands = [i for i, c in enumerate(d.columns) if "email" in str(c).lower()]
    if not cands:
        cands = list(range(len(d.columns)))
    return max(cands, key=lambda i: d.iloc[:, i].astype(str).str.contains("@", regex=False).sum())

def pick_col_by_keywords(d, keywords, exclude_exact=None):
    exclude_exact = {x.lower() for x in (exclude_exact or [])}
    for c in d.columns:
        cl = str(c).strip().lower()
        if cl in exclude_exact:
            continue
        if any(k in cl for k in keywords):
            return c
    return None

def email_rows(d, col, comp_col=None, name_col=None):
    em = d[col].astype(str).str.extract("(" + EMAIL_RE + ")", expand=False).str.strip().str.lower()
    co = d[comp_col].fillna("").astype(str).str.strip() if comp_col is not None and comp_col in d.columns else ""
    nm = d[name_col].fillna("").astype(str).str.strip() if name_col is not None and name_col in d.columns else ""
    return pd.DataFrame({"email": em, "company": co, "contact_name": nm}).dropna(subset=["email"]).drop_duplicates("email", keep="last")

def ist(series):
    return pd.to_datetime(series, errors="coerce").dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.strftime("%d %b %Y %H:%M")

page = st.sidebar.radio("Menu", ["Dashboard", "Suppliers", "Send & Follow-up", "Sign-offs"])
if st.sidebar.button("Refresh"): st.rerun()
df = load()

if page == "Dashboard":
    n = len(df)
    sent = int((df.sends > 0).sum()) if n else 0
    started = int((df.first_click.notna() | df.signed_off_at.notna()).sum()) if n else 0
    done = int((df.status == "Signed off").sum()) if n else 0
    waiting = int(((df.sends > 0) & df.first_click.isna() & df.signed_off_at.isna()).sum()) if n else 0
    cards = [
        ("Suppliers", n, NAVY),
        ("Emails sent", sent, BLUE),
        ("Sent, not started", waiting, "#e8a317"),
        ("Course started", started, TEAL),
        ("Signed off", done, GREEN),
    ]
    for col, (lbl, v, c) in zip(st.columns(5), cards):
        col.markdown(f'<div class="kpi" style="border-top:5px solid {c}">{lbl}<b style="color:{c}">{v}</b></div>', unsafe_allow_html=True)
    st.write("")
    if n:
        st.progress(done / n, text=f"Sign-off completion: {done / n:.0%} ({done}/{n} signed off)")
    st.caption("Course started = the supplier entered their email on the start page and pressed Start. Signed off = the supplier submitted the sign-off page at the end of the course.")
    if n:
        f1, f2 = st.columns([2, 1])
        stat_f = f1.multiselect("Status", ["Not sent", "Sent", "Course started", "Signed off"])
        txt = f2.text_input("Search company / email")
        v = df.copy()
        if stat_f: v = v[v.status.isin(stat_f)]
        if txt: v = v[v.company.str.contains(txt, case=False, na=False) | v.email.str.contains(txt, case=False, na=False)]
        out = pd.DataFrame({
            "Company": v.company,
            "Contact": v.contact_name,
            "Email": v.email,
            "Status": v.status,
            "Emails sent": v.sends,
            "Last sent": ist(v.last_sent),
            "Course started": ist(v.first_click),
            "Signed off": ist(v.signed_off_at),
        })
        SC = {"Not sent": "#e6eaef", "Sent": "#d5e6f6", "Course started": "#cdeef2", "Signed off": "#d3f0e1"}
        sty = out.style
        paint = sty.map if hasattr(sty, "map") else sty.applymap
        st.dataframe(paint(lambda s: f"background-color:{SC.get(s, '')};color:#1b2a3a;font-weight:600", subset=["Status"]),
                     use_container_width=True, hide_index=True)
        st.download_button("Download CSV", out.to_csv(index=False), "supplier_tracker.csv")

        def tidy(d):
            return pd.DataFrame({
                "Company": d.company,
                "Contact": d.contact_name,
                "Email": d.email,
                "Status": d.status,
                "Emails sent": d.sends,
                "Last sent": ist(d.last_sent),
                "Course started": ist(d.first_click),
                "Signed off": ist(d.signed_off_at),
            })
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            tidy(df).to_excel(xw, sheet_name="All suppliers", index=False)
            tidy(df[df.status == "Signed off"]).to_excel(xw, sheet_name="Signed off", index=False)
            tidy(df[df.status == "Course started"]).to_excel(xw, sheet_name="Started, not signed off", index=False)
            tidy(df[df.status == "Sent"]).to_excel(xw, sheet_name="Sent, not started", index=False)
        st.download_button("Download Excel report (4 sheets)", buf.getvalue(), "supplier_report.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.subheader("Recent activity")
        ev = pd.DataFrame(q("SELECT s.company,s.email,COUNT(*) clicks,MAX(e.ts) ts FROM events e JOIN suppliers s ON s.token=e.token WHERE e.type='click' GROUP BY s.id ORDER BY MAX(e.ts) DESC LIMIT 25"),
                          columns=["company", "email", "clicks", "ts"])
        if len(ev):
            ev["ts"] = ist(ev["ts"])
            ev.columns = ["Company", "Email", "Clicks", "Latest click"]
            st.dataframe(ev, use_container_width=True, hide_index=True)

    st.subheader("Unlisted visitors")
    st.caption("People who started the course with an email that is not in your supplier list (for example a forwarded invitation or a typo).")
    try:
        uv = pd.DataFrame(q("SELECT email,COUNT(*) starts,MIN(ts) first_ts,MAX(ts) last_ts FROM visitors WHERE handled IS NULL GROUP BY email ORDER BY MAX(ts) DESC"),
                          columns=["email", "starts", "first_ts", "last_ts"])
    except Exception:
        uv = None
        st.info("The visitors table is not set up yet. Run the one-time setup SQL in D1 to enable this section.")
    if uv is not None:
        if len(uv):
            uv["first_ts"], uv["last_ts"] = ist(uv["first_ts"]), ist(uv["last_ts"])
            uv.columns = ["Email", "Times started", "First started", "Latest start"]
            st.write("Tick the people you recognise, add their company, then add them to your supplier list. Their start time is kept.")
            ed = uv.copy()
            ed.insert(0, "Select", False)
            ed["Company"], ed["Contact name"] = "", ""
            with st.form("uv_form"):
                edited = st.data_editor(ed, hide_index=True, use_container_width=True, key="uv_editor",
                                        disabled=["Email", "Times started", "First started", "Latest start"])
                b1, b2 = st.columns(2)
                add_go = b1.form_submit_button("Add selected to supplier list")
                drop_go = b2.form_submit_button("Discard selected (typos or tests)")
            chosen = edited[edited["Select"]]
            if (add_go or drop_go) and chosen.empty:
                st.warning("Tick at least one person first.")
            elif add_go or drop_go:
                for _, r in chosen.iterrows():
                    em = str(r["Email"]).strip().lower()
                    if add_go:
                        co = "" if pd.isna(r["Company"]) else str(r["Company"])
                        nm = "" if pd.isna(r["Contact name"]) else str(r["Contact name"])
                        q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)", [co, nm, em, secrets.token_urlsafe(8)])
                        tok = q("SELECT token FROM suppliers WHERE LOWER(email)=?", [em])[0]["token"]
                        q("INSERT INTO events(token,type,ua,ts) SELECT ?,'click',ua,ts FROM visitors WHERE email=? AND handled IS NULL", [tok, em])
                        q("UPDATE visitors SET handled='added' WHERE email=? AND handled IS NULL", [em])
                        log("Unlisted visitor added to supplier list", f"{em} | company: {co}")
                    else:
                        q("UPDATE visitors SET handled='discarded' WHERE email=? AND handled IS NULL", [em])
                        log("Unlisted visitor discarded", em)
                st.success(f"{'Added' if add_go else 'Discarded'} {len(chosen)} visitor(s).")
                time.sleep(1)
                st.rerun()
            st.download_button("Download unlisted visitors CSV", uv.to_csv(index=False), "unlisted_visitors.csv")
        else:
            st.write("No unlisted visitors yet.")
    with st.expander("Already handled unlisted visitors (added or discarded)"):
        hv = pd.DataFrame(q("SELECT email,handled,MAX(ts) ts FROM visitors WHERE handled IS NOT NULL GROUP BY email,handled ORDER BY MAX(ts) DESC"),
                          columns=["email", "handled", "ts"])
        if len(hv):
            back = hv[hv["handled"] == "discarded"]["email"].tolist()
            hv["ts"] = ist(hv["ts"])
            st.dataframe(hv.rename(columns={"email": "Email", "handled": "What happened", "ts": "Last start"}),
                         use_container_width=True, hide_index=True)
            if back:
                with st.form("restore"):
                    pick = st.multiselect("Bring discarded people back to the Unlisted list", back)
                    rgo = st.form_submit_button("Bring back")
                if rgo and pick:
                    for em in pick:
                        q("UPDATE visitors SET handled=NULL WHERE email=? AND handled='discarded'", [em])
                        log("Discarded visitor brought back", em)
                    st.rerun()
        else:
            st.write("Nothing handled yet.")

elif page == "Suppliers":
    st.title("Suppliers")
    if st.session_state.get("flash"):
        st.success(st.session_state.pop("flash"))
    tab_add, tab_del = st.tabs(["Add suppliers", "Delete suppliers"])
    with tab_add:
        st.download_button("Download blank Excel template", supplier_template_bytes(), "supplier_upload_template.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        up = st.file_uploader("Upload your supplier list (Excel or CSV). Headings with company, contact and email in them are understood.", type=["xlsx", "csv"])
        if up:
            d, em_c, co_c, nm_c = None, None, None, None
            try:
                d = read_table(up)
                if any(re.search(EMAIL_RE, str(c)) for c in d.columns):  # file has no heading row: the first "heading" is really a supplier
                    first = list(d.columns)
                    d.columns = [f"column {i + 1}" for i in range(len(first))]
                    d = pd.concat([pd.DataFrame([first], columns=d.columns), d], ignore_index=True)
                em_c, co_c, nm_c = map_supplier_cols(d)
            except Exception as e:
                st.error(f"Could not read this file: {e}")
            if d is not None and em_c is None:
                st.error("I could not find an email column. Please use the template, or name the column 'email'.")
            elif d is not None:
                st.caption(f"Columns used: Email = '{em_c}', Company = '{co_c}', Contact = '{nm_c}'. First rows of your file:")
                st.dataframe(d.head(5), use_container_width=True, hide_index=True)
                if st.button("Import these suppliers"):
                    known = {str(x).strip().lower() for x in df.email}
                    new = existing = invalid = 0
                    for _, r in d.iterrows():
                        m_ = re.search(EMAIL_RE, str(r[em_c]))
                        if not m_:
                            invalid += 1
                            continue
                        em = m_.group(0).strip().lower()
                        if em in known:
                            existing += 1
                            continue
                        q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)",
                          [cell(r, co_c), cell(r, nm_c), em, secrets.token_urlsafe(8)])
                        known.add(em)
                        new += 1
                    log("Suppliers imported from file", f"{new} new, {existing} already in list, {invalid} without valid email")
                    st.session_state["flash"] = f"Added {new} new supplier(s). {existing} were already in the list and {invalid} row(s) had no valid email."
                    st.rerun()
        pasted = st.text_area("Or paste emails (from Outlook or Excel; any separator)", height=110)
        paste_co = st.text_input("Company for these emails (optional)")
        if st.button("Add pasted emails") and pasted.strip():
            found = list(dict.fromkeys(e.lower() for e in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", pasted)))
            for em in found:
                q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)", [paste_co, "", em, secrets.token_urlsafe(8)])
            st.success(f"Found {len(found)} email(s). Anyone already in the list is skipped.")
            time.sleep(1)
            st.rerun()
        with st.form("add"):
            c1, c2, c3 = st.columns(3)
            co, nm, em = c1.text_input("Company"), c2.text_input("Contact name"), c3.text_input("Email")
            if st.form_submit_button("Add supplier") and "@" in em:
                q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)", [co, nm, em.strip().lower(), secrets.token_urlsafe(8)])
                st.rerun()
    with tab_del:
        if len(df):
            label = {r.email: f"{r.company or '-'}  |  {r.email}" for r in df.itertuples()}
            with st.form("del", clear_on_submit=True):
                sel = st.multiselect("Choose suppliers to delete", list(label), format_func=lambda e: label[e])
                sure_del = st.checkbox("Yes, delete the selected suppliers and their history permanently")
                go = st.form_submit_button("Delete selected")
            if go:
                if sel and sure_del:
                    for r in df[df.email.isin(sel)].itertuples():
                        log("Supplier deleted", f"{r.email} | emails sent: {r.sends} | status: {r.status}")
                        q("DELETE FROM events WHERE token=?", [r.token])
                        q("DELETE FROM sends WHERE supplier_id=?", [int(r.id)])
                        q("DELETE FROM suppliers WHERE id=?", [int(r.id)])
                    st.success(f"Deleted {len(sel)} supplier(s)")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.warning("Choose at least one supplier and tick the box to confirm.")
        else:
            st.info("There are no suppliers to delete.")
    st.subheader("All suppliers")
    st.dataframe(df[["company", "contact_name", "email", "status"]], use_container_width=True, hide_index=True)
    with st.expander("Change log: who was added, discarded or deleted"):
        lg = pd.DataFrame(q("SELECT ts,action,detail FROM log ORDER BY id DESC LIMIT 100"), columns=["ts", "action", "detail"])
        if len(lg):
            lg["ts"] = ist(lg["ts"])
            st.dataframe(lg.rename(columns={"ts": "When", "action": "What", "detail": "Details"}), use_container_width=True, hide_index=True)
        else:
            st.write("Nothing recorded yet.")

elif page == "Send & Follow-up":
    st.title("Send & Follow-up")
    st.write(f"Send the email from Outlook using the shared mailbox (`{SHARED_MAILBOX}`), then click **Mark as email sent** so the dashboard knows who has been emailed.")
    if st.session_state.get("flash"):
        st.success(st.session_state.pop("flash"))
    if st.session_state.get("flash_warn"):
        st.warning(st.session_state.pop("flash_warn"))

    INIT = "Initial email – not yet sent"
    R1 = "Reminder – sent, not started yet"
    R2 = "Reminder – started, not signed off"
    mode = st.radio("Who to email", [INIT, R1, R2])
    reminder = mode != INIT
    kind_val = "reminder" if reminder else "initial"

    t = df[df.signed_off_at.isna()]
    if mode == INIT:
        t = t[t.sends == 0]
    else:
        days = st.number_input("Skip suppliers emailed within the last (days)", 0, 30, 3)
        t = t[(t.sends > 0) & t.first_click.isna()] if mode == R1 else t[t.first_click.notna()]
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days)
        last = pd.to_datetime(t.last_sent)
        t = t[last.isna() | (last < cutoff)]

    batch_size = st.number_input("Batch size (suppliers per Outlook email)", 1, 500, 100)
    eligible = t.email.tolist()
    picks = st.multiselect(f"Recipients ({len(eligible)} eligible)", eligible, default=eligible[:int(batch_size)])

    st.subheader("Step 1: send from Outlook")
    if picks:
        st.write(f"From: `{SHARED_MAILBOX}`. Copy these into the **BCC** field:")
        st.code("; ".join(picks), language=None)
        sel = t[t.email.isin(picks)]
        st.download_button("Download recipient list (CSV)", sel[["company", "contact_name", "email"]].to_csv(index=False), "recipients.csv")
    else:
        st.info("Nobody is eligible for this option right now.")
    if "TRACK_BASE" in st.secrets:
        with st.expander("Email subject and text to paste into Outlook"):
            s0, t0, _h0 = invite_content("", reminder=reminder)
            st.write("Subject:")
            st.code(s0, language=None)
            st.write("Body (plain text):")
            st.code(t0, language=None)

    st.subheader("Step 2: after you have sent it")
    if st.button(f"Mark {len(picks)} supplier(s) as email sent", type="primary", disabled=not picks):
        for r in t[t.email.isin(picks)].itertuples():
            q("INSERT INTO sends(supplier_id,kind,status) VALUES(?,?,?)", [int(r.id), kind_val, "ok"])
        log("Marked as email sent", f"{len(picks)} supplier(s) | {mode}")
        st.session_state["flash"] = f"Marked {len(picks)} supplier(s) as email sent."
        st.rerun()

    with st.expander("Marked by mistake? Undo 'email sent'"):
        sent_df = df[(df.sends > 0) & df.signed_off_at.isna()]
        if len(sent_df):
            undo = st.multiselect("Suppliers to set back to 'not sent'", sent_df.email.tolist())
            if st.button("Undo email sent for selected", disabled=not undo):
                for r in df[df.email.isin(undo)].itertuples():
                    q("DELETE FROM sends WHERE supplier_id=? AND (kind IS NULL OR kind!='thankyou')", [int(r.id)])
                log("Email sent undone", ", ".join(undo))
                st.session_state["flash"] = f"Set {len(undo)} supplier(s) back to 'not sent'."
                st.rerun()
        else:
            st.write("Nobody is marked as sent.")

    with st.expander("Optional: send from the app instead (only works once the Mailjet sender is fixed)"):
        secrets_ok = all(k in st.secrets for k in ("SMTP_USER", "SMTP_PASS", "TRACK_BASE"))
        if not secrets_ok:
            st.info("To enable sending, add SMTP_USER, SMTP_PASS and TRACK_BASE in Streamlit Secrets (Settings > Secrets).")
        test_to = st.text_input("Step A: send a test email to your own address first")
        if st.button("Send test email", disabled=not (secrets_ok and test_to.strip())):
            try:
                ts, tx, th = invite_content("Test Name", reminder=reminder)
                reply = send_mail(test_to.strip(), "[TEST] " + ts, tx, th)
                st.success(f"The mail server accepted the test email for {test_to.strip()}. Check your inbox (and Junk). Accepted does not always mean delivered.")
                st.code(f"{describe_sender()}\nServer reply: {reply}", language=None)
            except Exception as e:
                st.error(f"Test failed: {e}")
        sure = st.checkbox("Step B: I checked the test email and want to send to the selected suppliers")
        if st.button(f"Send to {len(picks)} supplier(s) now", disabled=not (secrets_ok and picks and sure)):
            rows_ = list(t[t.email.isin(picks)].itertuples())
            bar, ok, fails = st.progress(0), 0, []
            for i, r in enumerate(rows_):
                nm = "" if pd.isna(r.contact_name) else str(r.contact_name).strip()
                try:
                    xs, xt, xh = invite_content(nm, reminder=reminder)
                    send_mail(r.email, xs, xt, xh)
                    status = "ok"
                    ok += 1
                except Exception as e:
                    status = f"error: {e}"[:200]
                    fails.append(f"{r.email}: {status}")
                q("INSERT INTO sends(supplier_id,kind,status) VALUES(?,?,?)", [int(r.id), kind_val, status])
                bar.progress((i + 1) / len(rows_))
                try:
                    time.sleep(float(st.secrets.get("SEND_DELAY_SECONDS", 0.5)))
                except Exception:
                    time.sleep(0.5)
            st.session_state["flash"] = f"Sent {ok} of {len(rows_)} email(s)."
            if fails:
                st.session_state["flash_warn"] = "These failed and were NOT marked as sent: " + " | ".join(fails[:10])
            st.rerun()

else:
    st.title("Sign-offs")
    st.write("Suppliers sign off by themselves on the sign-off page at the end of the course, and it appears on the Dashboard automatically. Use this page only to fix something.")
    tab_manual, tab_fix = st.tabs(["Record a sign-off manually", "Remove a sign-off"])
    with tab_manual:
        st.subheader("Record Sign-Off Directly in the App")
        st.write("Mark an existing supplier as signed off, or enter a new supplier's sign-off directly.")
        with st.form("direct_signoff_form"):
            c1, c2, c3 = st.columns(3)
            so_name = c1.text_input("1. Full name")
            so_comp = c2.text_input("2. Company name *")
            so_email = c3.text_input("3. Email ID *")
            so_submit = st.form_submit_button("Record Sign-Off Now")
        if so_submit:
            em_clean = so_email.strip().lower()
            if "@" not in em_clean:
                st.error("Please enter a valid Email ID.")
            else:
                q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)",
                  [so_comp.strip(), so_name.strip(), em_clean, secrets.token_urlsafe(8)])
                q("UPDATE suppliers SET signed_off_at=COALESCE(signed_off_at, CURRENT_TIMESTAMP), "
                  "company=CASE WHEN (company IS NULL OR company='') AND ? != '' THEN ? ELSE company END, "
                  "contact_name=CASE WHEN (contact_name IS NULL OR contact_name='') AND ? != '' THEN ? ELSE contact_name END "
                  "WHERE LOWER(TRIM(email))=?",
                  [so_comp.strip(), so_comp.strip(), so_name.strip(), so_name.strip(), em_clean])
                log("Direct sign-off recorded in app", f"{em_clean} | company: {so_comp.strip()}")
                st.success(f"Recorded sign-off for {em_clean} ({so_comp.strip()}).")
                time.sleep(1)
                st.rerun()

        st.divider()
        st.subheader("Or pick from existing supplier list")
        m = st.selectbox("Supplier email", df.email.tolist() if len(df) else [])
        if st.button("Mark selected supplier as signed off") and m:
            q("UPDATE suppliers SET signed_off_at=COALESCE(signed_off_at, CURRENT_TIMESTAMP) WHERE LOWER(TRIM(email))=?", [m.strip().lower()])
            log("Manual sign-off marked", m)
            st.success(f"Marked {m} as signed off.")
            time.sleep(1)
            st.rerun()
    with tab_fix:
        signed = df[df.signed_off_at.notna()]
        if len(signed):
            rem = st.multiselect("Suppliers to change back to 'not signed off'", signed.email.tolist())
            if st.button("Remove sign-off for selected", disabled=not rem):
                for em_ in rem:
                    q("UPDATE suppliers SET signed_off_at=NULL WHERE LOWER(TRIM(email))=?", [str(em_).strip().lower()])
                    log("Sign-off removed", em_)
                st.success(f"Removed sign-off for {len(rem)} supplier(s).")
                time.sleep(1)
                st.rerun()
        else:
            st.write("Nobody is marked as signed off.")
