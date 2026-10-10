import secrets, time
import pandas as pd
import streamlit as st
from d1 import q
from mailer import send_mail

st.set_page_config(page_title="Supplier Code of Conduct Tracker", page_icon="🧭", layout="wide")
NAVY = "#2a5a8c"
st.markdown(f"<style>h1,h2,h3{{color:{NAVY}}}[data-testid=stMetricValue]{{color:{NAVY}}}</style>", unsafe_allow_html=True)

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

def ist(series):
    return pd.to_datetime(series, errors="coerce").dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.strftime("%d %b %Y %H:%M")

page = st.sidebar.radio("Menu", ["Dashboard", "Suppliers", "Send & Follow-up", "Import sign-offs"])
if st.sidebar.button("Refresh"): st.rerun()
df = load()

if page == "Dashboard":
    st.title("Supplier Code of Conduct – Awareness Campaign")
    n = len(df)
    sent = int((df.sends > 0).sum()) if n else 0
    started = int((df.first_click.notna() | df.signed_off_at.notna()).sum()) if n else 0
    done = int((df.status == "Signed off").sum()) if n else 0
    for col, (lbl, v) in zip(st.columns(4), [("Suppliers", n), ("Emails sent", sent), ("Course started", started), ("Signed off", done)]):
        col.metric(lbl, v)
    if n:
        st.progress(done / n, text=f"Sign-off completion: {done / n:.0%}")
    st.caption("Course started = someone clicked the supplier's link (a forwarded email counts under the original supplier). Signed off comes from the Microsoft Form list you upload on the Import sign-offs page.")
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
        st.dataframe(out, use_container_width=True, hide_index=True)
        st.download_button("Download CSV", out.to_csv(index=False), "supplier_tracker.csv")
        st.subheader("Recent activity")
        ev = pd.DataFrame(q("SELECT s.company,s.email,COUNT(*) clicks,MAX(e.ts) ts FROM events e JOIN suppliers s ON s.token=e.token WHERE e.type='click' GROUP BY s.id ORDER BY MAX(e.ts) DESC LIMIT 25"),
                          columns=["company", "email", "clicks", "ts"])
        if len(ev):
            ev["ts"] = ist(ev["ts"])
            ev.columns = ["Company", "Email", "Clicks", "Latest click"]
            st.dataframe(ev, use_container_width=True, hide_index=True)

elif page == "Suppliers":
    st.title("Suppliers")
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
    with st.form("add"):
        c1, c2, c3 = st.columns(3)
        co, nm, em = c1.text_input("Company"), c2.text_input("Contact name"), c3.text_input("Email")
        if st.form_submit_button("Add supplier") and "@" in em:
            q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)", [co, nm, em.strip().lower(), secrets.token_urlsafe(8)])
            st.rerun()
    st.dataframe(df[["company", "contact_name", "email", "status"]], use_container_width=True, hide_index=True)

elif page == "Send & Follow-up":
    st.title("Send & Follow-up")
    mode = st.radio("Who to email", ["Not yet sent – initial email", "Sent but not signed off – reminder"])
    reminder = mode.startswith("Sent")
    if reminder:
        days = st.number_input("Skip suppliers emailed within the last (days)", 0, 30, 3)
        t = df[(df.sends > 0) & df.signed_off_at.isna()]
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days)
        t = t[pd.to_datetime(t.last_sent) < cutoff]
    else:
        t = df[df.sends == 0]
    picks = st.multiselect(f"Recipients ({len(t)} eligible)", t.email.tolist(), default=t.email.tolist())
    st.caption("Tip: add yourself as a supplier first to test the email and tracking.")
    if st.button(f"Send to {len(picks)} supplier(s)", disabled=not picks):
        bar, ok = st.progress(0), 0
        for i, r in enumerate(t[t.email.isin(picks)].itertuples()):
            try:
                send_mail(r.email, r.token, reminder); status = "ok"; ok += 1
            except Exception as e:
                status = f"error: {e}"[:200]; st.warning(f"{r.email}: {status}")
            q("INSERT INTO sends(supplier_id,kind,status) VALUES(?,?,?)", [int(r.id), "reminder" if reminder else "initial", status])
            bar.progress((i + 1) / len(picks)); time.sleep(1)
        st.success(f"Sent {ok} of {len(picks)}")

else:
    st.title("Import sign-offs")
    st.write("Download the responses of the Microsoft Form (Excel) and upload them here. Suppliers are matched by email.")
    up = st.file_uploader("Microsoft Forms responses", type=["xlsx", "csv"])
    if up:
        d = pd.read_csv(up) if up.name.endswith(".csv") else pd.read_excel(up)
        guess = next((i for i, c in enumerate(d.columns) if "email" in str(c).lower()), 0)
        col = st.selectbox("Email column", d.columns, index=guess)
        if st.button("Mark as signed off"):
            known = set(df.email)
            hit = 0
            for em in d[col].dropna().astype(str).str.strip().str.lower().unique():
                if em in known:
                    q("UPDATE suppliers SET signed_off_at=CURRENT_TIMESTAMP WHERE email=? AND signed_off_at IS NULL", [em]); hit += 1
            st.success(f"{hit} suppliers matched. {d[col].nunique() - hit} emails in the file are not in your supplier list.")
    st.divider()
    st.subheader("Or mark one supplier manually")
    m = st.selectbox("Supplier email", df.email.tolist() if len(df) else [])
    if st.button("Mark as signed off") and m:
        q("UPDATE suppliers SET signed_off_at=CURRENT_TIMESTAMP WHERE email=?", [m]); st.rerun()
