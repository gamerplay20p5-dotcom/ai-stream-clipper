# Somente leitura: quantas contas de cliente existem e quantas assinaturas (sem e-mails nem nomes)
from sqlalchemy import func, select
from clipper.config import get_settings
from clipper.db import init_engine, session_scope
from clipper.models import AccountSubscription, User
init_engine(get_settings().database_url) if callable(globals().get("init_engine")) else None
with session_scope() as db:
    print("  contas de cliente ativas:", db.scalar(select(func.count()).select_from(User).where(User.role == "customer", User.active.is_(True))))
    print("  assinaturas registradas:", db.scalar(select(func.count()).select_from(AccountSubscription)))
