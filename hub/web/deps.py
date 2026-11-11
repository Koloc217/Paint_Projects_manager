"""FastAPI dependencies shared by the routers."""
from hub.core.database import SessionLocal


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
