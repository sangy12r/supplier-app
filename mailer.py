import smtplib, streamlit as st
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

SUBJECT = "Partnership Commitment – Complying with the Supplier Code of Conduct"

def build_html(token):
    s = st.secrets
    base = s["TRACK_BASE"].rstrip("/")
    deadline = s.get("DEADLINE", "18 December 2025")
    scoc = s.get("SCOC_URL", "")
    scoc_txt = f'<a href="{scoc}">Wilhelmsen Supplier Code of Conduct (SCoC)</a>' if scoc else "Wilhelmsen Supplier Code of Conduct (SCoC)"
    scoc_txt2 = f'<a href="{scoc}">Supplier Code of Conduct</a>' if scoc else "Supplier Code of Conduct"
    logo = s.get("LOGO_URL", "")
    logo_html = f'<img src="{logo}" alt="Wilhelmsen Port Services" height="50"><br>' if logo else ""
    return f"""<div style="font-family:Arial,sans-serif;font-size:13px;color:#444;max-width:640px;margin:auto">
<div style="text-align:center">{logo_html}
<h2 style="font-family:Georgia,serif;color:#1f4e79;font-weight:normal">Partnership Commitment – Complying with the Supplier Code of Conduct</h2></div>
<p>Dear Partners and Suppliers,</p>
<p>As valued partners working in collaboration with Wilhelmsen, it is essential that we collectively prioritise Governance, Risk, and Compliance (GRC) in all our business activities. Our mutual goal is to ensure that every party involved, whether directly or indirectly, adheres to the {scoc_txt} and consistently upholds the highest standards of social responsibility.</p>
<p>Wilhelmsen is unwavering in its commitment to conducting business in a safe, responsible, and ethical manner, both within our own operations and across all business relationships. We operate with zero tolerance for corruption, modern slavery, and child labour, and we are committed to fostering and maintaining a safe and healthy working environment wherever we operate. Accordingly, we expect all suppliers and partners to demonstrate an ongoing commitment to safety, responsible business practices, and sustainable development.</p>
<p>To further reinforce these principles, we are initiating an awareness campaign aimed at emphasising the importance of ethical and responsible business conduct among all our partners. We urge you to report any compliance concerns you may encounter, particularly those related to the {scoc_txt2}. These can be reported to us at <a href="mailto:wps.global.compliance@wilhelmsen.com">wps.global.compliance@wilhelmsen.com</a>, or alternatively, through our whistleblowing channel.</p>
<p>Please complete the awareness session by accessing the link provided below and ensure your participation by <b>{deadline}</b>.</p>
<p style="text-align:center;margin:24px 0"><a href="{base}/c/{token}" style="background:#2a5a8c;color:#fff;padding:14px 40px;text-decoration:none;display:inline-block">Click to Access the Awareness Session</a></p>
<p>Thank you for your continued support in achieving our business objectives. We welcome your comments and feedback at <a href="mailto:wps.supplier.governance@wilhelmsen.com">wps.supplier.governance@wilhelmsen.com</a>.</p>
<p>Best regards,<br>Global Procurement Team<br>Wilhelmsen Port Services</p>
</div>"""

def build_text(token):
    s = st.secrets
    base = s["TRACK_BASE"].rstrip("/")
    deadline = s.get("DEADLINE", "18 December 2025")
    return (
        "Dear Partners and Suppliers,\n\n"
        "As part of our commitment to safe, responsible and ethical business, Wilhelmsen is running an "
        "awareness campaign on the Supplier Code of Conduct.\n\n"
        f"Please complete the awareness session using the link below by {deadline}:\n"
        f"{base}/c/{token}\n\n"
        "Compliance concerns can be reported to wps.global.compliance@wilhelmsen.com.\n"
        "Comments and feedback: wps.supplier.governance@wilhelmsen.com\n\n"
        "Best regards,\nGlobal Procurement Team\nWilhelmsen Port Services\n"
    )

def send_mail(to, token, reminder=False):
    s = st.secrets
    msg = MIMEMultipart("alternative")
    msg["Subject"] = ("Reminder: " if reminder else "") + SUBJECT
    msg["From"] = f"{s.get('FROM_NAME', 'Wilhelmsen Port Services')} <{s['SMTP_USER']}>"
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(build_text(token), "plain", "utf-8"))
    msg.attach(MIMEText(build_html(token), "html", "utf-8"))
    with smtplib.SMTP(s.get("SMTP_HOST", "smtp.gmail.com"), int(s.get("SMTP_PORT", 587))) as srv:
        srv.starttls()
        srv.login(s["SMTP_USER"], s["SMTP_PASS"])
        srv.sendmail(s["SMTP_USER"], [to], msg.as_string())
