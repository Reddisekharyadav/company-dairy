"""
WorkSense — Email report sender.

Reads SMTP credentials from config.settings (which in turn reads from
environment variables WS_EMAIL_TO, WS_EMAIL_FROM, WS_EMAIL_USER, WS_EMAIL_PASS,
WS_EMAIL_SMTP, WS_EMAIL_PORT).

For Gmail: create an "App Password" at
  https://myaccount.google.com/apppasswords
and set WS_EMAIL_USER=you@gmail.com  WS_EMAIL_PASS=<16-char-app-password>
"""
import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime

log = logging.getLogger('worksense.email')


class EmailNotConfiguredError(RuntimeError):
    """Raised when SMTP credentials / recipient are not set."""


def _load_settings():
    """Lazy import to avoid circular imports at module load time."""
    from config.settings import settings
    return settings


def send_report(pdf_path: str, period: str = 'today', extra_body: str = '') -> None:
    """
    Send *pdf_path* as an email attachment.

    Parameters
    ----------
    pdf_path   : Absolute path to the PDF report file.
    period     : Human-readable label, e.g. 'today' or 'this week'.
    extra_body : Optional plain-text summary to embed in the email body.

    Raises
    ------
    EmailNotConfiguredError  - if any required credential is missing.
    smtplib.SMTPException    - on SMTP-level failures (re-raised as-is).
    """
    cfg = _load_settings()

    missing = [k for k, v in {
        'WS_EMAIL_TO':   cfg.email_to,
        'WS_EMAIL_USER': cfg.email_user,
        'WS_EMAIL_PASS': cfg.email_pass,
    }.items() if not v]

    if missing:
        raise EmailNotConfiguredError(
            f"Email not configured. Missing: {', '.join(missing)}.\n"
            "Set these environment variables:\n"
            "  WS_EMAIL_TO   - recipient address\n"
            "  WS_EMAIL_USER - your Gmail address (sender)\n"
            "  WS_EMAIL_PASS - Gmail App Password (16 chars, no spaces)\n"
            "  WS_EMAIL_SMTP - SMTP host (default: smtp.gmail.com)\n"
            "  WS_EMAIL_PORT - SMTP port (default: 587)"
        )

    sender    = cfg.email_from or cfg.email_user
    recipient = cfg.email_to
    now_str   = datetime.now().strftime('%Y-%m-%d')
    subject   = f"WorkSense Activity Report - {period.title()} ({now_str})"

    # ── Compose message ───────────────────────────────────────────────────────
    msg = MIMEMultipart()
    msg['From']    = sender
    msg['To']      = recipient
    msg['Subject'] = subject

    body_lines = [
        "Hi,\n",
        f"Please find attached your WorkSense AI activity report for {period}.",
        f"Generated on {now_str}.\n",
    ]
    if extra_body:
        body_lines.append("-" * 50)
        body_lines.append(extra_body)
        body_lines.append("-" * 50)
    body_lines += [
        "\nThis report was generated automatically by WorkSense AI.",
        "To stop receiving these emails, remove WS_EMAIL_TO from your environment.",
    ]
    msg.attach(MIMEText('\n'.join(body_lines), 'plain', 'utf-8'))

    # ── Attach PDF ────────────────────────────────────────────────────────────
    pdf_path = str(pdf_path)
    if os.path.exists(pdf_path):
        with open(pdf_path, 'rb') as fh:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(fh.read())
        encoders.encode_base64(part)
        part.add_header(
            'Content-Disposition',
            f'attachment; filename="{os.path.basename(pdf_path)}"',
        )
        msg.attach(part)
    else:
        log.warning('PDF not found at %s - sending email without attachment.', pdf_path)

    # ── Send ──────────────────────────────────────────────────────────────────
    log.info('Sending report email to %s via %s:%s', recipient, cfg.email_smtp, cfg.email_port)
    with smtplib.SMTP(cfg.email_smtp, cfg.email_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(cfg.email_user, cfg.email_pass)
        server.sendmail(sender, recipient, msg.as_string())
    log.info('Report email sent successfully to %s', recipient)
