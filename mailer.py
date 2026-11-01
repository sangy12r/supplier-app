"""Sends supplier emails through Mailjet SMTP (or any SMTP server) using Streamlit Secrets.

Secrets used (Streamlit -> Settings -> Secrets):
  SMTP_HOST   default "in-v3.mailjet.com"
  SMTP_PORT   default 587  (587 = STARTTLS, 465 = SSL)
  SMTP_USER   Mailjet username / API key
  SMTP_PASS   Mailjet password / secret key
  FROM_EMAIL  default "wps.supplier.governance@wilhelmsen.com" (must be an address Mailjet allows)
  FROM_NAME   default "WPS Supplier Governance"
  REPLY_TO    optional, replies go here (e.g. the shared mailbox) instead of the sending address
  SEND_DELAY_SECONDS  optional, pause between emails (default 0.5; use 2.5 for Microsoft 365 SMTP)
  TRACK_BASE  your worker address, e.g. https://supplier-link.sangy12r.workers.dev
  DEADLINE    optional, e.g. "18 December 2026" (left out of the email if not set)
  SCOC_URL    optional link to the Supplier Code of Conduct
  LOGO_URL    optional logo image link
"""
import html as _html
import smtplib
import streamlit as st
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

SUBJECT = "Partnership Commitment – Complying with the Supplier Code of Conduct"
DEFAULT_FROM = "wps.supplier.governance@wilhelmsen.com"


def _secret(key, default=""):
    try:
        return st.secrets[key] if key in st.secrets else default
    except Exception:
        return default


def start_link():
    return str(_secret("TRACK_BASE")).rstrip("/") + "/start"


def invite_content(name="", reminder=False):
    """Returns (subject, plain_text, html) for the invitation or reminder email."""
    link = start_link()
    deadline = str(_secret("DEADLINE")).strip()
    scoc = str(_secret("SCOC_URL")).strip()
    logo = str(_secret("LOGO_URL")).strip()
    greet = f"Dear {name.strip()}," if name and name.strip() else "Dear Partners and Suppliers,"
    when = f" by <b>{_html.escape(deadline)}</b>" if deadline else ""
    when_txt = f" by {deadline}" if deadline else ""
    scoc_a = f'<a href="{_html.escape(scoc)}">Wilhelmsen Supplier Code of Conduct (SCoC)</a>' if scoc else "Wilhelmsen Supplier Code of Conduct (SCoC)"
    scoc_b = f'<a href="{_html.escape(scoc)}">Supplier Code of Conduct</a>' if scoc else "Supplier Code of Conduct"
    logo_html = f'<img src="{_html.escape(logo)}" alt="Wilhelmsen Port Services" height="50"><br>' if logo else ""
    subject = ("Reminder: " if reminder else "") + SUBJECT
    intro_rem = "<p><i>This is a friendly reminder. If you have already completed the awareness session and sign-off, thank you, and please ignore this message.</i></p>" if reminder else ""
    intro_rem_txt = "This is a friendly reminder. If you have already completed the awareness session and sign-off, thank you, and please ignore this message.\n\n" if reminder else ""
    html = f"""<div style="font-family:Arial,sans-serif;font-size:13px;color:#444;max-width:640px;margin:auto">
<div style="text-align:center">{logo_html}
<h2 style="font-family:Georgia,serif;color:#1f4e79;font-weight:normal">Partnership Commitment – Complying with the Supplier Code of Conduct</h2></div>
<p>{_html.escape(greet)}</p>{intro_rem}
<p>As valued partners working in collaboration with Wilhelmsen, it is essential that we collectively prioritise Governance, Risk, and Compliance (GRC) in all our business activities. Our mutual goal is to ensure that every party involved, whether directly or indirectly, adheres to the {scoc_a} and consistently upholds the highest standards of social responsibility.</p>
<p>Wilhelmsen is unwavering in its commitment to conducting business in a safe, responsible, and ethical manner, both within our own operations and across all business relationships. We operate with zero tolerance for corruption, modern slavery, and child labour, and we are committed to fostering and maintaining a safe and healthy working environment wherever we operate. Accordingly, we expect all suppliers and partners to demonstrate an ongoing commitment to safety, responsible business practices, and sustainable development.</p>
<p>To further reinforce these principles, we are initiating an awareness campaign aimed at emphasising the importance of ethical and responsible business conduct among all our partners. We urge you to report any compliance concerns you may encounter, particularly those related to the {scoc_b}. These can be reported to us at <a href="mailto:wps.global.compliance@wilhelmsen.com">wps.global.compliance@wilhelmsen.com</a>, or alternatively, through our whistleblowing channel.</p>
<p>Please complete the awareness session by accessing the link provided below{when}.</p>
<p style="text-align:center;margin:24px 0"><a href="{_html.escape(link)}" style="background:#2a5a8c;color:#fff;padding:14px 40px;text-decoration:none;display:inline-block">Click to Access the Awareness Session</a></p>
<p>Thank you for your continued support in achieving our business objectives. We welcome your comments and feedback at <a href="mailto:wps.supplier.governance@wilhelmsen.com">wps.supplier.governance@wilhelmsen.com</a>.</p>
<p>Best regards,<br>Supplier Governance Team<br>Wilhelmsen Port Services</p>
</div>"""
    text = (
        f"{greet}\n\n{intro_rem_txt}"
        "As valued partners working in collaboration with Wilhelmsen, it is essential that we collectively prioritise "
        "Governance, Risk, and Compliance (GRC) in all our business activities. Wilhelmsen operates with zero tolerance "
        "for corruption, modern slavery, and child labour, and we expect all suppliers and partners to uphold the "
        "Wilhelmsen Supplier Code of Conduct.\n\n"
        f"Please complete the awareness session using the link below{when_txt}:\n{link}\n\n"
        "Compliance concerns can be reported to wps.global.compliance@wilhelmsen.com.\n"
        "Comments and feedback: wps.supplier.governance@wilhelmsen.com\n\n"
        "Best regards,\nSupplier Governance Team\nWilhelmsen Port Services\n"
    )
    return subject, text, html


def thankyou_content(name, subject, body_txt):
    """Returns (subject, plain_text, html) for the thank-you / certificate email."""
    body = body_txt
    if name and name.strip() and "Dear Valued Partner" in body:
        body = body.replace("Dear Valued Partner", f"Dear {name.strip()}", 1)
    paras = "".join(f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in body.split("\n\n") if p.strip())
    html = f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#1b2a3a;max-width:640px;margin:auto;line-height:1.55">{paras}</div>'
    return subject, body, html


def describe_sender():
    """Shows which settings are really in use (the key is partly hidden)."""
    user = str(_secret("SMTP_USER", ""))
    masked = (user[:4] + "..." + user[-2:]) if len(user) > 8 else user
    return (f"Server: {_secret('SMTP_HOST', 'in-v3.mailjet.com')}:{_secret('SMTP_PORT', 587)} | "
            f"Login: {masked} | From: {_secret('FROM_EMAIL', DEFAULT_FROM)}")


def send_mail(to, subject, text, html):
    host = str(_secret("SMTP_HOST", "in-v3.mailjet.com"))
    port = int(_secret("SMTP_PORT", 587))
    user = _secret("SMTP_USER")
    pwd = _secret("SMTP_PASS")
    sender = str(_secret("FROM_EMAIL", DEFAULT_FROM))
    name = str(_secret("FROM_NAME", "WPS Supplier Governance"))
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((name, sender))
    msg["To"] = to
    reply_to = str(_secret("REPLY_TO", "")).strip()
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    if port == 465:
        srv = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        srv = smtplib.SMTP(host, port, timeout=30)
    try:
        if port != 465:
            srv.starttls()
        srv.login(user, pwd)
        code, resp = srv.mail(sender)
        if code != 250:
            raise smtplib.SMTPSenderRefused(code, resp, sender)
        code, resp = srv.rcpt(to)
        if code not in (250, 251):
            raise smtplib.SMTPRecipientsRefused({to: (code, resp)})
        code, resp = srv.data(msg.as_string())
        if code != 250:
            raise smtplib.SMTPDataError(code, resp)
        return f"{code} " + (resp.decode("utf-8", "replace") if isinstance(resp, bytes) else str(resp))
    finally:
        try:
            srv.quit()
        except Exception:
            pass
