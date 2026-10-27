import base64, os, re, secrets, time
import pandas as pd
import streamlit as st
from d1 import q

st.set_page_config(page_title="Supplier Code of Conduct Tracker", page_icon="🧭", layout="wide")

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
NAVY, BLUE, TEAL, GREEN = "#1f4e79", "#2a5a8c", "#1fa3b5", "#2e9e6b"
st.markdown(f"""<style>
h1{{color:{NAVY};font-size:1.7rem!important}} h2,h3{{color:{BLUE}}}
[data-testid=stSidebar]{{background:#eaf1f8}}
.kpi{{background:#fff;border-radius:12px;padding:14px 18px;box-shadow:0 1px 6px rgba(0,0,0,.12);color:#5b6b7b;font-size:13px}}
.kpi b{{display:block;font-size:34px;line-height:1.2}}
.stButton button,.stDownloadButton button{{border-radius:8px}}
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

COLS = ["id", "company", "contact_name", "email", "token", "signed_off_at", "last_sent", "sends", "first_click"]
SQL = """SELECT s.id,s.company,s.contact_name,s.email,s.token,s.signed_off_at,
 (SELECT MAX(sent_at) FROM sends WHERE supplier_id=s.id AND status='ok') last_sent,
 (SELECT COUNT(*) FROM sends WHERE supplier_id=s.id AND status='ok') sends,
 (SELECT MIN(ts) FROM events WHERE token=s.token AND type='click') first_click
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

def ist(series):
    return pd.to_datetime(series, errors="coerce").dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.strftime("%d %b %Y %H:%M")

page = st.sidebar.radio("Menu", ["Dashboard", "Suppliers", "Send & Follow-up", "Import lists"])
if st.sidebar.button("Refresh"): st.rerun()
df = load()

if page == "Dashboard":
    n = len(df)
    sent = int((df.sends > 0).sum()) if n else 0
    started = int((df.first_click.notna() | df.signed_off_at.notna()).sum()) if n else 0
    done = int((df.status == "Signed off").sum()) if n else 0
    waiting = int(((df.sends > 0) & df.first_click.isna() & df.signed_off_at.isna()).sum()) if n else 0
    cards = [("Suppliers", n, NAVY), ("Emails sent", sent, BLUE), ("Sent, not started", waiting, "#e8a317"), ("Course started", started, TEAL), ("Signed off", done, GREEN)]
    for col, (lbl, v, c) in zip(st.columns(5), cards):
        col.markdown(f'<div class="kpi" style="border-top:5px solid {c}">{lbl}<b style="color:{c}">{v}</b></div>', unsafe_allow_html=True)
    st.write("")
    if n:
        st.progress(done / n, text=f"Sign-off completion: {done / n:.0%}")
    st.caption("Course started = the supplier entered their email on the start page and pressed Start, which takes them into the course. Signed off = their sign-off form response, uploaded on the Import lists page.")
    if n:
        f1, f2 = st.columns([2, 1])
        stat_f = f1.multiselect("Status", ["Not sent", "Sent", "Course started", "Signed off"])
        txt = f2.text_input("Search company / email")
        v = df.copy()
        if stat_f: v = v[v.status.isin(stat_f)]
        if txt: v = v[v.company.str.contains(txt, case=False, na=False) | v.email.str.contains(txt, case=False, na=False)]
        out = pd.DataFrame({"Company": v.company, "Contact": v.contact_name, "Email": v.email, "Status": v.status,
                            "Emails sent": v.sends, "Last sent": ist(v.last_sent), "Course started": ist(v.first_click),
                            "Signed off": ist(v.signed_off_at)})
        SC = {"Not sent": "#e6eaef", "Sent": "#d5e6f6", "Course started": "#cdeef2", "Signed off": "#d3f0e1"}
        sty = out.style
        paint = sty.map if hasattr(sty, "map") else sty.applymap
        st.dataframe(paint(lambda s: f"background-color:{SC.get(s, '')};color:#1b2a3a;font-weight:600", subset=["Status"]),
                     use_container_width=True, hide_index=True)
        st.download_button("Download CSV", out.to_csv(index=False), "supplier_tracker.csv")
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
                        tok = q("SELECT token FROM suppliers WHERE email=?", [em])[0]["token"]
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
    tab_add, tab_del = st.tabs(["Add suppliers", "Delete suppliers"])
    with tab_add:
        up = st.file_uploader("Upload list (Excel/CSV with columns: company, contact_name, email)", type=["xlsx", "csv"])
        if up and st.button("Import"):
            d = pd.read_csv(up) if up.name.endswith(".csv") else pd.read_excel(up)
            d.columns = [c.strip().lower() for c in d.columns]
            added = 0
            for r in d.itertuples():
                em = str(getattr(r, "email", "")).strip().lower()
                if "@" not in em: continue
                q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)",
                  [str(getattr(r, "company", "")), str(getattr(r, "contact_name", "")), em, secrets.token_urlsafe(8)])
                added += 1
            st.success(f"Processed {added} rows (duplicates by email are ignored)")
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
                typed = st.text_input("Type DELETE to confirm (this removes their history permanently)")
                go = st.form_submit_button("Delete selected")
            if go:
                if sel and typed.strip() == "DELETE":
                    for r in df[df.email.isin(sel)].itertuples():
                        log("Supplier deleted", f"{r.email} | emails sent: {r.sends} | status: {r.status}")
                        q("DELETE FROM events WHERE token=?", [r.token])
                        q("DELETE FROM sends WHERE supplier_id=?", [int(r.id)])
                        q("DELETE FROM suppliers WHERE id=?", [int(r.id)])
                    st.success(f"Deleted {len(sel)} supplier(s)")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.warning("Choose at least one supplier and type DELETE to confirm.")
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
    st.write("Send the emails yourself from Outlook, then record them here with **Mark as sent** so the dashboard and reminder dates stay accurate.")
    if st.session_state.get("flash"):
        st.success(st.session_state.pop("flash"))
    INIT = "Initial email – not yet sent"
    R1 = "Reminder – sent, not started yet"
    R2 = "Reminder – started, not signed off"
    mode = st.radio("Who to email", [INIT, R1, R2])
    reminder = mode != INIT
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
    if picks:
        st.write("Copy these into the **BCC** field in Outlook:")
        st.code("; ".join(picks), language=None)
        sel = t[t.email.isin(picks)]
        st.download_button("Download list (CSV)", sel[["company", "contact_name", "email"]].to_csv(index=False), "recipients.csv")
    st.caption("After you have sent the email in Outlook, click the button below. The next batch will then appear.")
    if st.button(f"Mark {len(picks)} supplier(s) as sent", disabled=not picks):
        for r in t[t.email.isin(picks)].itertuples():
            q("INSERT INTO sends(supplier_id,kind,status) VALUES(?,?,?)", [int(r.id), "reminder" if reminder else "initial", "ok"])
        st.session_state["flash"] = f"Marked {len(picks)} supplier(s) as sent."
        st.rerun()

else:
    st.title("Import lists")
    st.write("Download the responses of a Microsoft Form (Excel) and upload them here. People are matched by email.")
    kind = st.radio("Which form is this?", ["Start form – who started the course", "Sign-off form – who signed off"])
    is_start = kind.startswith("Start")
    up = st.file_uploader("Microsoft Forms responses", type=["xlsx", "csv"])
    if up:
        d = pd.read_csv(up) if up.name.endswith(".csv") else pd.read_excel(up)
        guess = next((i for i, c in enumerate(d.columns) if "email" in str(c).lower()), 0)
        col = st.selectbox("Email column", d.columns, index=guess)
        if st.button("Update dashboard"):
            emails = [e for e in d[col].dropna().astype(str).str.strip().str.lower().unique() if "@" in e]
            known = dict(zip(df.email, df.token))
            clicked = set(df[df.first_click.notna()].email)
            try:
                seen = {r["email"] for r in q("SELECT DISTINCT email FROM visitors")}
            except Exception:
                seen = set()
            hit = new_unlisted = unmatched = 0
            for em in emails:
                if is_start:
                    if em in known:
                        if em not in clicked:
                            q("INSERT INTO events(token,type,ua) VALUES(?,'click','start form')", [known[em]]); hit += 1
                    elif em not in seen:
                        q("INSERT INTO visitors(email,ua) VALUES(?,?)", [em, "start form"]); new_unlisted += 1
                elif em in known:
                    q("UPDATE suppliers SET signed_off_at=CURRENT_TIMESTAMP WHERE email=? AND signed_off_at IS NULL", [em]); hit += 1
                else:
                    unmatched += 1
            if is_start:
                st.success(f"{hit} suppliers marked as started. {new_unlisted} new emails added to Unlisted visitors on the Dashboard.")
            else:
                st.success(f"{hit} suppliers matched. {unmatched} emails in the file are not in your supplier list.")
    st.divider()
    st.subheader("Or mark one supplier as signed off manually")
    m = st.selectbox("Supplier email", df.email.tolist() if len(df) else [])
    if st.button("Mark as signed off") and m:
        q("UPDATE suppliers SET signed_off_at=CURRENT_TIMESTAMP WHERE email=?", [m]); st.rerun()
