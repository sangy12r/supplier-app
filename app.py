import base64, io, os, re, secrets, time, urllib.parse
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from d1 import q
from mailer import invite_content, thankyou_content, send_mail, describe_sender

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

page = st.sidebar.radio("Menu", ["Dashboard", "Suppliers", "Send & Follow-up", "Sign-off & Import"])
if st.sidebar.button("Refresh"): st.rerun()
df = load()

if page == "Dashboard":
    n = len(df)
    sent = int((df.sends > 0).sum()) if n else 0
    started = int((df.first_click.notna() | df.signed_off_at.notna()).sum()) if n else 0
    done = int((df.status == "Signed off").sum()) if n else 0
    thanked = int(df.thankyou_at.notna().sum()) if n else 0
    waiting = int(((df.sends > 0) & df.first_click.isna() & df.signed_off_at.isna()).sum()) if n else 0
    cards = [
        ("Suppliers", n, NAVY),
        ("Emails sent", sent, BLUE),
        ("Sent, not started", waiting, "#e8a317"),
        ("Course started", started, TEAL),
        ("Signed off", done, GREEN),
        ("Thank-you sent", thanked, PURPLE),
    ]
    for col, (lbl, v, c) in zip(st.columns(6), cards):
        col.markdown(f'<div class="kpi" style="border-top:5px solid {c}">{lbl}<b style="color:{c}">{v}</b></div>', unsafe_allow_html=True)
    st.write("")
    if n:
        st.progress(done / n, text=f"Sign-off completion: {done / n:.0%} ({done}/{n} signed off · {thanked}/{done if done else 0} thanked)")
    st.caption("Course started = the supplier entered their email on the start page and pressed Start. Signed off = completed via the built-in /signoff page or uploaded from the Microsoft Sign-off Form.")
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
            "Thank-you sent": ist(v.thankyou_at),
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
                "Thank-you sent": ist(d.thankyou_at),
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
    tab_add, tab_del = st.tabs(["Add suppliers", "Delete suppliers"])
    with tab_add:
        up = st.file_uploader("Upload list (Excel/CSV with columns: company, contact_name, email)", type=["xlsx", "csv"])
        if up and st.button("Import"):
            d = read_table(up)
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
    st.write(f"Send invitations, reminders or **Certificate of Completion thank-you emails** straight from the shared mailbox (`{SHARED_MAILBOX}`). Each supplier gets their own email and the app records it automatically.")
    if st.session_state.get("flash"):
        st.success(st.session_state.pop("flash"))
    if st.session_state.get("flash_warn"):
        st.warning(st.session_state.pop("flash_warn"))

    INIT = "Initial email – not yet sent"
    R1 = "Reminder – sent, not started yet"
    R2 = "Reminder – started, not signed off"
    TY = "Thank you / Certificate – signed off, not yet thanked"

    mode = st.radio("Who to email", [INIT, R1, R2, TY])

    if mode == TY:
        include_already = st.checkbox("Include suppliers who were already sent a thank-you email", value=False)
        t = df[df.signed_off_at.notna()] if include_already else df[df.signed_off_at.notna() & df.thankyou_at.isna()]
    else:
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

    if mode == TY:
        st.subheader("Thank-You / Certificate of Completion Email Template")
        st.caption(f"Send from Shared Mailbox: **{SHARED_MAILBOX}**")
        default_subj = "Certificate of Completion – Wilhelmsen Port Services Supplier Code of Conduct"
        default_body = (
            "Dear Valued Partner,\n\n"
            "Thank you for completing the Wilhelmsen Port Services Supplier Code of Conduct Awareness Session and submitting your Partnership Commitment sign-off. "
            "This email serves as your official confirmation and Certificate of Completion, recognizing your organization's commitment to our shared GRC and compliance standards.\n\n"
            "We appreciate your continued partnership and active support in upholding responsible business practices.\n\n"
            "Best regards,\n"
            "Supplier Governance Team\n"
            "Wilhelmsen Port Services\n"
            f"{SHARED_MAILBOX}"
        )
        st.caption("Each supplier's email greets them by their saved contact name (for example 'Dear Ann,'). 'Dear Valued Partner' is used only when no name is saved.")
        subj = st.text_input("Email Subject", value=default_subj)
        body_txt = st.text_area("Email Body (2–3 lines Certificate of Completion)", value=default_body, height=180)

        st.markdown(
            f"""<div class="cert-box">
            <div style="font-size:12px;text-transform:uppercase;letter-spacing:1px;color:{TEAL};font-weight:700">Wilhelmsen Port Services · Certificate of Completion</div>
            <div style="font-size:16px;font-weight:700;color:{NAVY};margin:4px 0 8px">Partnership Commitment – Complying with the Supplier Code of Conduct</div>
            <div style="font-size:14px;line-height:1.55;white-space:pre-line">{body_txt}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    kind_val = "thankyou" if mode == TY else ("reminder" if mode != INIT else "initial")
    secrets_ok = all(k in st.secrets for k in ("SMTP_USER", "SMTP_PASS", "TRACK_BASE"))

    def content_for(name):
        if mode == TY:
            return thankyou_content(name, subj, body_txt)
        return invite_content(name, reminder=(mode != INIT))

    st.subheader("Send from the app")
    if not secrets_ok:
        st.info("To enable sending, add SMTP_USER, SMTP_PASS and TRACK_BASE in Streamlit Secrets (Settings > Secrets).")
    test_to = st.text_input("Step 1: send a test email to your own address first")
    if st.button("Send test email", disabled=not (secrets_ok and test_to.strip())):
        try:
            ts, tx, th = content_for("Test Name")
            reply = send_mail(test_to.strip(), "[TEST] " + ts, tx, th)
            st.success(f"The mail server accepted the test email for {test_to.strip()}. Check your inbox (and Junk). Accepted does not always mean delivered.")
            st.code(f"{describe_sender()}\nServer reply: {reply}", language=None)
        except Exception as e:
            st.error(f"Test failed: {e}")
    sure = st.checkbox("Step 2: I checked the test email and want to send to the selected suppliers")
    if st.button(f"Send to {len(picks)} supplier(s) now", disabled=not (secrets_ok and picks and sure)):
        rows_ = list(t[t.email.isin(picks)].itertuples())
        bar, ok, fails = st.progress(0), 0, []
        for i, r in enumerate(rows_):
            nm = "" if pd.isna(r.contact_name) else str(r.contact_name).strip()
            try:
                xs, xt, xh = content_for(nm)
                send_mail(r.email, xs, xt, xh)
                status = "ok"; ok += 1
            except Exception as e:
                status = f"error: {e}"[:200]
                fails.append(f"{r.email}: {status}")
            q("INSERT INTO sends(supplier_id,kind,status) VALUES(?,?,?)", [int(r.id), kind_val, status])
            bar.progress((i + 1) / len(rows_))
            try:
                time.sleep(float(st.secrets.get("SEND_DELAY_SECONDS", 0.5)))
            except Exception:
                time.sleep(0.5)
        if mode == TY:
            log("Thank-you email sent", f"{ok} supplier(s) from {SHARED_MAILBOX}")
        st.session_state["flash"] = f"Sent {ok} of {len(rows_)} email(s)."
        if fails:
            st.session_state["flash_warn"] = "These failed and were NOT marked as sent: " + " | ".join(fails[:10])
        st.rerun()

    with st.expander("Prefer to send from Outlook yourself? (manual option)"):
        if picks:
            st.write(f"**1. From field in Outlook:** `{SHARED_MAILBOX}`")
            st.write("**2. Copy these into the BCC field in Outlook:**")
            st.code("; ".join(picks), language=None)
            sel = t[t.email.isin(picks)]
            st.download_button("Download recipient list (CSV)", sel[["company", "contact_name", "email"]].to_csv(index=False), "recipients.csv")
        st.caption("After you have sent the email in Outlook, click the button below so the dashboard records it.")
        btn_label = f"Mark {len(picks)} supplier(s) as thank-you sent" if mode == TY else f"Mark {len(picks)} supplier(s) as sent"
        if st.button(btn_label, disabled=not picks):
            for r in t[t.email.isin(picks)].itertuples():
                q("INSERT INTO sends(supplier_id,kind,status) VALUES(?,?,?)", [int(r.id), kind_val, "ok"])
            if mode == TY:
                log("Thank-you email sent", f"{len(picks)} supplier(s) from {SHARED_MAILBOX}")
            st.session_state["flash"] = f"Marked {len(picks)} supplier(s) as {'thank-you sent' if mode == TY else 'sent'}."
            st.rerun()

else:
    st.title("Sign-off & Import")
    tab_sync, tab_msform, tab_manual = st.tabs([
        "1. Upload MS Form Responses (Excel/CSV)",
        "2. In-App Sign-Off Form (MS Form + Worker Link)",
        "3. Manual / Direct Sign-Off",
    ])

    with tab_sync:
        st.subheader("Import Microsoft Form Sign-Off or Start Responses")
        st.write(
            "Download the Excel file from your **Partnership commitment – Complying with the Supplier Code of Conduct** Microsoft Form and upload it here. "
            "Suppliers are matched case-insensitively by their **Email ID** (`Please enter your Email ID`), and their Company & Contact Name are updated automatically."
        )
        st.markdown(f"🔗 **Open your Microsoft Form responses page:** [Partnership commitment – Complying with the Supplier Code of Conduct]({MS_SIGNOFF_FORM_URL})")
        kind = st.radio("Which form is this?", ["Sign-off form – who signed off", "Start form – who started the course"])
        is_start = kind.startswith("Start")
        up = st.file_uploader("Microsoft Forms responses (.xlsx or .csv)", type=["xlsx", "csv"])
        if up:
            d = read_table(up)
            guess_em = pick_email_col(d)
            c1, c2, c3 = st.columns(3)
            col = c1.selectbox("Email ID column", d.columns, index=guess_em)
            comp_default = pick_col_by_keywords(d, ["company"])
            comp_opts = ["(none)"] + list(d.columns)
            comp_sel = c2.selectbox("Company column", comp_opts, index=comp_opts.index(comp_default) if comp_default in comp_opts else 0)
            name_default = pick_col_by_keywords(d, ["full name", "contact", "your name"], exclude_exact=["name"])
            name_opts = ["(none)"] + list(d.columns)
            name_sel = c3.selectbox("Full Name column", name_opts, index=name_opts.index(name_default) if name_default in name_opts else 0)

            comp_col = None if comp_sel == "(none)" else comp_sel
            name_col = None if name_sel == "(none)" else name_sel

            id_col = next((c for c in d.columns if str(c).strip().lower() == "id"), None)
            last_id = None
            if not is_start:
                try:
                    lr = q("SELECT detail FROM log WHERE action='Sign-off import checkpoint' ORDER BY id DESC LIMIT 1")
                    if lr:
                        v0 = lr[0]["detail"] if isinstance(lr[0], dict) else lr[0][0]
                        last_id = int(float(v0))
                except Exception:
                    last_id = None
            d_use = d
            if not is_start and id_col is not None and last_id is not None:
                new_only = st.checkbox(f"Only new responses (skip rows already imported; last imported Id = {last_id})", value=True)
                if new_only:
                    d_use = d[pd.to_numeric(d[id_col], errors="coerce") > last_id]
            started_only = False
            if not is_start:
                started_only = st.checkbox("Only mark as signed off if the supplier started the course in this app (ignores older responses)", value=True)
            rows = email_rows(d_use, col, comp_col, name_col)
            st.caption(f"Detected **{len(rows)}** unique valid email address(es) in the uploaded file.")
            st.dataframe(rows.head(10), use_container_width=True, hide_index=True)

            auto_add_unlisted = st.checkbox("Automatically add signed-off suppliers even if their email wasn't in the original supplier list", value=True)

            if st.button("Update dashboard with these responses", type="primary"):
                known = {str(e).strip().lower(): tok for e, tok in zip(df.email, df.token)}
                clicked = {str(e).strip().lower() for e in df[df.first_click.notna()].email}
                try:
                    seen = {str(r["email"]).strip().lower() for r in q("SELECT DISTINCT email FROM visitors")}
                except Exception:
                    seen = set()
                hit = new_unlisted = 0
                unmatched = []
                skipped_not_started = []
                for r in rows.itertuples(index=False):
                    em = str(r.email).strip().lower()
                    co = str(r.company).strip() if pd.notna(r.company) else ""
                    nm = str(r.contact_name).strip() if pd.notna(r.contact_name) else ""
                    if is_start:
                        if em in known:
                            if em not in clicked:
                                q("INSERT INTO events(token,type,ua) VALUES(?,'click','start form')", [known[em]])
                                hit += 1
                        elif em not in seen:
                            q("INSERT INTO visitors(email,ua) VALUES(?,?)", [em, "start form"])
                            new_unlisted += 1
                    else:
                        if started_only and em not in clicked and em not in seen:
                            skipped_not_started.append(em)
                            continue
                        if em in known:
                            q("UPDATE suppliers SET signed_off_at=COALESCE(signed_off_at, CURRENT_TIMESTAMP), "
                              "company=CASE WHEN (company IS NULL OR company='') AND ? != '' THEN ? ELSE company END, "
                              "contact_name=CASE WHEN (contact_name IS NULL OR contact_name='') AND ? != '' THEN ? ELSE contact_name END "
                              "WHERE LOWER(TRIM(email))=?", [co, co, nm, nm, em])
                            hit += 1
                        elif auto_add_unlisted:
                            q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token,signed_off_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)",
                              [co, nm, em, secrets.token_urlsafe(8)])
                            q("UPDATE suppliers SET signed_off_at=COALESCE(signed_off_at, CURRENT_TIMESTAMP) WHERE LOWER(TRIM(email))=?", [em])
                            log("Signed-off person added to supplier list", f"{em} | company: {co}")
                            hit += 1
                        else:
                            unmatched.append((em, co, nm))
                if is_start:
                    st.success(f"{hit} suppliers marked as started. {new_unlisted} new emails added to Unlisted visitors on the Dashboard.")
                else:
                    st.session_state["unmatched_so"] = unmatched
                    st.session_state["skipped_so"] = skipped_not_started
                    if id_col is not None:
                        mx = pd.to_numeric(d[id_col], errors="coerce").max()
                        if pd.notna(mx):
                            log("Sign-off import checkpoint", str(int(mx)))
                    st.success(f"{hit} supplier(s) marked as Signed Off! Go to 'Send & Follow-up' to send their Certificate of Completion thank-you email.")
                    time.sleep(1)
                    st.rerun()

        pend = st.session_state.get("unmatched_so")
        if pend and not is_start:
            st.subheader("Signed off, but not in your supplier list")
            st.caption("These people completed the sign-off form with an email that is not in your list (for example a forwarded invitation).")
            st.dataframe(pd.DataFrame(pend, columns=["Email", "Company (from the form)", "Full Name"]), use_container_width=True, hide_index=True)
            if st.button("Add these people to the supplier list as signed off"):
                for item in pend:
                    em, co = item[0], item[1]
                    nm = item[2] if len(item) > 2 else ""
                    q("INSERT OR IGNORE INTO suppliers(company,contact_name,email,token) VALUES(?,?,?,?)", [co, nm, em, secrets.token_urlsafe(8)])
                    q("UPDATE suppliers SET signed_off_at=COALESCE(signed_off_at, CURRENT_TIMESTAMP) WHERE LOWER(TRIM(email))=?", [em])
                    log("Signed-off person added to supplier list", f"{em} | company: {co}")
                st.session_state.pop("unmatched_so", None)
                st.success(f"Added {len(pend)} people as signed off.")
                time.sleep(1)
                st.rerun()

        skp = st.session_state.pop("skipped_so", None)
        if skp and not is_start:
            st.warning(f"{len(skp)} response(s) were ignored because those emails have not started the course in this app: " + ", ".join(skp[:20]) + (" ..." if len(skp) > 20 else ""))

        with st.expander("Made a mistake? Remove a sign-off"):
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

    with tab_msform:
        st.subheader("Sign-Off Links & Embedded Microsoft Form")
        st.info(
            "💡 **Want Sign-Off status to update automatically without uploading Excel files?**\n"
            "Your updated `worker.js` now includes a built-in **`/signoff`** page with the exact same 3 questions as your Microsoft Form "
            "(*Full name*, *Company name*, *Email ID*). Any supplier who submits `https://<your-worker>/signoff` is **instantly marked as Signed Off** in this app!"
        )
        st.write("**Microsoft Form Link:**")
        st.code(MS_SIGNOFF_FORM_URL, language=None)
        st.write("**Embedded Microsoft Sign-Off Form Preview:**")
        components.iframe(MS_SIGNOFF_FORM_URL, height=700, scrolling=True)

    with tab_manual:
        st.subheader("Record Sign-Off Directly in the App")
        st.write("Mark an existing supplier as signed off, or enter a new supplier's sign-off directly.")
        with st.form("direct_signoff_form"):
            c1, c2, c3 = st.columns(3)
            so_name = c1.text_input("1. Full name (optional)")
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
