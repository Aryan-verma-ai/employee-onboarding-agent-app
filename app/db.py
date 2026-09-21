from fastapi import Depends
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .auth import get_principal
from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
)
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def sqlite_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def set_security_context(session, transaction, connection):
    if connection.dialect.name != "postgresql":
        return
    principal = session.info.get("principal")
    # Transaction-local settings cannot leak into the pooled connection's next user.
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant, true), set_config('app.subject', :subject, true), set_config('app.is_hr', :hr, true)"
        ),
        {
            "tenant": principal.tenant_id if principal else "",
            "subject": principal.subject if principal else "",
            "hr": "true" if principal and principal.is_hr else "false",
        },
    )


def get_db(principal=Depends(get_principal)):
    with SessionLocal() as session:
        session.info["principal"] = principal
        yield session
