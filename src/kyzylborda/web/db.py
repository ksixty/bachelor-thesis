import sqlalchemy
from flask import g

from ..db import Base


def set_up_database(app, url):
    db_engine = sqlalchemy.create_engine(url)
    db_factory = sqlalchemy.orm.sessionmaker(bind=db_engine)
    db = sqlalchemy.orm.scoped_session(db_factory)
    app.db = db

    @app.before_first_request
    def create_database():
        Base.metadata.create_all(db_engine)

    @app.teardown_request
    def remove_session(exception=None):
        db.remove()
