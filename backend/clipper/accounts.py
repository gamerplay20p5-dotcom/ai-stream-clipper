"""Account self-service: password policy, single-use password links and the outgoing e-mail (SMTP).

* Passwords are never stored: only an Argon2id hash (``security.hash_password``).
* A password link (invite or reset) is a random 256-bit token shown/sent ONCE; the database keeps only its
  SHA-256, the account, the purpose and the expiry. Using it (or issuing a new one) invalidates the others.
* The SMTP password is encrypted at rest with AES-256-GCM (same key derivation as the OAuth secrets,
  ``publish.crypto``) and never returned to the browser.
"""

from __future__ import annotations

import hashlib
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import utcnow
from .models import AppSetting, PasswordToken, User
from .publish import crypto

MIN_LEN = 10
INVITE_TTL = timedelta(hours=72)
RESET_TTL = timedelta(hours=2)
COMMON = {"1234567890", "12345678910", "0123456789", "senha12345", "password123", "qwertyuiop", "aaaaaaaaaa",
          "abcdefghij", "1111111111", "senhasenha", "clipper123", "admin12345"}
SMTP_KEY = "smtp"


def password_problems(pw: str, email: str = "") -> list[str]:
    out = []
    if len(pw) < MIN_LEN:
        out.append(f"a senha precisa ter pelo menos {MIN_LEN} caracteres")
    if len(pw) > 256:
        out.append("a senha pode ter no máximo 256 caracteres")
    low = pw.lower()
    local = email.split("@")[0].lower() if email else ""
    if low in COMMON or len(set(pw)) < 4:
        out.append("esta senha é fácil demais de adivinhar")
    if local and len(local) >= 4 and local in low:
        out.append("a senha não pode conter o seu e-mail")
    return out


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_link(db: Session, user: User, purpose: str, by: str) -> str:
    """New single-use token for ``user`` (older unused ones stop working). Returns the RAW token once."""
    now = utcnow()
    db.execute(update(PasswordToken).where(PasswordToken.user_id == user.id, PasswordToken.used_at.is_(None))
               .values(used_at=now))
    raw = secrets.token_urlsafe(32)
    db.add(PasswordToken(user_id=user.id, token_hash=_digest(raw), purpose=purpose, created_by=by[:255],
                         expires_at=now + (INVITE_TTL if purpose in ("invite", "signup") else RESET_TTL)))
    db.flush()
    return raw


def find_link(db: Session, raw: str) -> tuple[PasswordToken, User] | None:
    if not raw or len(raw) > 200:
        return None
    t = db.scalar(select(PasswordToken).where(PasswordToken.token_hash == _digest(raw)))
    if t is None or t.used_at is not None or t.expires_at < utcnow():
        return None
    u = db.get(User, t.user_id)
    if u is None or not u.active:
        return None
    return t, u


def mask_email(email: str) -> str:
    name, _, dom = email.partition("@")
    return (name[:2] + "•" * max(1, len(name) - 2)) + "@" + dom


# ------------------------------------------------------------------ e-mail (SMTP)
def smtp_config(db: Session) -> dict:
    row = db.get(AppSetting, SMTP_KEY)
    return dict(row.value) if row and row.value else {}


def smtp_public(db: Session) -> dict:
    c = smtp_config(db)
    return {"host": c.get("host", ""), "port": c.get("port", 587), "username": c.get("username", ""),
            "from_addr": c.get("from_addr", ""), "security": c.get("security", "starttls"),
            "has_password": bool(c.get("password_enc")), "configured": smtp_ready(db)}


def smtp_ready(db: Session) -> bool:
    c = smtp_config(db)
    return bool(c.get("host") and c.get("from_addr"))


def save_smtp(db: Session, *, host: str, port: int, username: str, password: str | None, from_addr: str,
              security: str) -> None:
    c = smtp_config(db)
    c.update({"host": host.strip(), "port": int(port), "username": username.strip(), "from_addr": from_addr.strip(),
              "security": security})
    if password is not None:  # None = keep the stored one; "" = remove it
        c["password_enc"] = crypto.encrypt(password) if password else ""
    row = db.get(AppSetting, SMTP_KEY)
    if row is None:
        db.add(AppSetting(key=SMTP_KEY, value=c))
    else:
        row.value = c


def send_mail(db: Session, to: str, subject: str, body: str) -> None:
    c = smtp_config(db)
    if not (c.get("host") and c.get("from_addr")):
        raise RuntimeError("o envio de e-mail não está configurado")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = c["from_addr"], to, subject
    msg.set_content(body)
    pw = crypto.decrypt(c["password_enc"]) if c.get("password_enc") else ""
    ctx = ssl.create_default_context()
    port = int(c.get("port") or 587)
    if c.get("security") == "ssl":
        server = smtplib.SMTP_SSL(c["host"], port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(c["host"], port, timeout=20)
        if c.get("security", "starttls") == "starttls":
            server.starttls(context=ctx)
    try:
        if c.get("username"):
            server.login(c["username"], pw if isinstance(pw, str) else str(pw))
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def link_mail(url: str, purpose: str) -> tuple[str, str]:
    if purpose == "signup":
        return ("Confirme seu e-mail no AI Stream Clipper / Confirm your e-mail",
                "Olá! Recebemos um cadastro com este e-mail no AI Stream Clipper by HTS.\n"
                f"Para confirmar que o e-mail é seu e criar a sua senha, use este link (vale por 72 horas, uma vez só):\n{url}\n"
                "Se não foi você, ignore este e-mail: nenhuma conta é usada sem esta confirmação.\n\n"
                "Hi! Someone signed up for AI Stream Clipper by HTS with this e-mail.\n"
                f"To confirm it is yours and create your password, use this link (valid for 72 hours, once):\n{url}\n"
                "If it wasn't you, ignore this e-mail: no account can be used without this confirmation.\n")
    if purpose == "invite":
        return ("Seu acesso ao AI Stream Clipper / Your AI Stream Clipper access",
                "Olá! Uma conta foi criada para você no AI Stream Clipper by HTS.\n"
                f"Crie a sua senha neste link (vale por 72 horas, uma vez só):\n{url}\n\n"
                "Hi! An account was created for you on AI Stream Clipper by HTS.\n"
                f"Create your password with this link (valid for 72 hours, once):\n{url}\n")
    return ("Redefinir a senha / Reset your password",
            "Recebemos um pedido para redefinir a sua senha do AI Stream Clipper.\n"
            f"Use este link (vale por 2 horas, uma vez só):\n{url}\n"
            "Se não foi você, ignore este e-mail: a senha atual continua valendo.\n\n"
            "We received a request to reset your AI Stream Clipper password.\n"
            f"Use this link (valid for 2 hours, once):\n{url}\n"
            "If it wasn't you, ignore this e-mail: your current password still works.\n")
