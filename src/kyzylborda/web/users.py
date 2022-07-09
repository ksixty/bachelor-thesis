from typing import Optional, cast
import logging
import uuid
from datetime import datetime, timezone
from itsdangerous.url_safe import URLSafeTimedSerializer
import itsdangerous.exc
from sqlalchemy.orm.exc import ObjectDeletedError, NoResultFound
from sqlalchemy.exc import IntegrityError
from flask import g, current_app, url_for, render_template, abort, request, redirect
from flask_login import LoginManager, login_user
from flask_babel import lazy_gettext as _
from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, PasswordField
from wtforms.validators import Email, Regexp, DataRequired
import jwt
from jwt.exceptions import InvalidTokenError

from ..utils import utc_now
from ..db import User
from .. import mail
from .utils import hide_referrer


logger = logging.getLogger(__name__)


# Full printable ASCII + Unicode letters
USER_NAME_REGEX = r"[\w\x20-\x7E]{1,32}"


class SignupForm(FlaskForm):
    email = StringField(_("E-mail"), validators=[Email()])
    name = StringField(_("Team name"), validators=[Regexp(USER_NAME_REGEX)])
    consent = BooleanField(_("I agree to Privacy Policy"), validators=[DataRequired()])


class LoginForm(FlaskForm):
    login = StringField(_("User name"), validators=[Regexp(USER_NAME_REGEX)])
    password = PasswordField(_("Password"), validators=[DataRequired()])


class ResetTokenForm(FlaskForm):
    email = StringField(_("E-mail"), validators=[Email()])


class AppUser:
    def __init__(self, user: User):
        self.user = user

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.user.id)


class KyzylUsers:
    smtp_server: Optional[mail.SMTPServer]
    from_address: Optional[str]
    login_manager: LoginManager
    reset_token_serializer: URLSafeTimedSerializer
    external_key: Optional[str]
    registration_enabled: bool

    def __init__(self, app, from_address: Optional[str]=None, smtp_server: Optional[mail.SMTPServer]=None, reset_token_timeout: int=600, external_key: Optional[str]=None, registration_enabled: bool=True):
        if registration_enabled and (from_address is None or smtp_server is None):
            raise RuntimeError("Registration is enabled but SMTP is not configured")

        self.from_address = from_address
        self.smtp_server = smtp_server

        self.login_manager = LoginManager()
        self.login_manager.init_app(app)

        self.reset_token_serializer = URLSafeTimedSerializer(app.secret_key, salt=b"kyzylborda")
        self.reset_token_timeout = reset_token_timeout

        self.external_key = external_key
        self.registration_enabled = registration_enabled

        @self.login_manager.user_loader
        def load_user(str_user_id):
            try:
                user_id = int(str_user_id)
            except ValueError:
                return None
            try:
                user = current_app.db.get(User, user_id)
            except ObjectDeletedError:
                return None

            if user is None:
                return None
            else:
                # Don't reload user changes.
                current_app.db.expunge(user)
                return AppUser(user)

    def _send_token(self, user: User, mail_template: str, login_route: str):
        text = render_template(mail_template, link=url_for(login_route, token=user.token.hex, _external=True))
        body = mail.Email(
            from_address=cast(str, self.from_address),
            subject=_("Your link to access the board"),
            text=text,
            to=[user.email],
        )
        mail.send_email(cast(mail.SMTPServer, self.smtp_server), body.make_email())

    @hide_referrer
    def token_login_route(self, success_view):
        try:
            token = uuid.UUID(request.args["token"])
        except (KeyError, ValueError) as e:
            logger.warn(f"Invalid login link", exc_info=e)
            abort(400)
        try:
            user = current_app.db.query(User).filter_by(token=token).one()
        except NoResultFound as e:
            logger.warn(f"Invalid user credentials", exc_info=e)
            abort(401)
        login_user(AppUser(user), remember=True)
        return success_view()

    @hide_referrer
    def external_login_route(self, success_view):
        if self.external_key is None:
            logger.warn(f"Tried to use external login, but no key has been loaded")
            abort(401)

        try:
            token = request.args["token"]
        except (KeyError, ValueError) as e:
            logger.warn(f"No external token present", exc_info=e)
            abort(400)

        try:
            decoded = jwt.decode(token, algorithms=["ES256"], key=self.external_key, options={"require_exp": True, "verify_exp": True, "require_iat": True, "verify_iat": True})
        except jwt.exceptions.InvalidTokenError as e:
            logger.warn(f"Invalid external token", exc_info=e)
            abort(401)

        db = current_app.db

        try:
            user = db.query(User).filter(User.login == decoded["login"], User.token.is_(None)).one()
        except NoResultFound as e:
            user = User(
                login=decoded["login"],
                name=decoded["name"],
                tags=decoded.get("tags", ["default"]),
            )
            db.add(user)
            db.commit()

        login_user(AppUser(user), remember=True)
        return success_view()

    def password_login_route(self, success_url: str, error_view):
        form = LoginForm()
        if not form.validate_on_submit():
            return error_view(form=form)

        db = current_app.db

        try:
            user = db.query(User).filter(User.login == form.login.data.strip(), User.password == form.password.data).one()
        except NoResultFound as e:
            logger.warn(f"Invalid user credentials", exc_info=e)
            abort(401)
        login_user(AppUser(user), remember=True)
        return redirect(success_url, code=303)

    def signup_route(self, success_url: str, error_view, mail_template: str, login_route: str, resend_token: bool=False):
        if not self.registration_enabled:
            abort(404)

        form = SignupForm()
        if not form.validate_on_submit():
            return error_view(form=form)

        db = current_app.db

        email=form.email.data.lower()
        user = User(
            login=email,
            name=form.name.data.strip(),
            email=email,
            token=uuid.uuid4(),
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as e:
            logger.warn("Error while creating new user", exc_info=e)
            db.rollback()
            if resend_token:
                try:
                    existing_user = db.query(User).filter_by(email=user.email).one()
                except NoResultFound as e:
                    return error_view(form=form, errors=[_("Name already exists.")])
                self._send_token(existing_user, mail_template, login_route)
                logger.info(f"Resending user '{existing_user.login}' with same email their token from the signup form")
                return error_view(form=form, errors=[_("Email already exists, just in case we have resent you your token.")])
            else:
                return error_view(form=form, errors=[_("Name or email already exist.")])

        logger.info(f"New user '{user.login}' registered with name '{user.name}'")
        self._send_token(user, mail_template, login_route)
        db.commit()

        return redirect(success_url, code=303)

    def reset_token_route(self, success_url: str, error_view, mail_template: str):
        form = ResetTokenForm()

        if not form.validate_on_submit():
            return error_view(form=form)

        if self.smtp_server is None:
            return error_view(form=form, errors=[_("Email sending is disabled.")])

        email = form.email.data.lower()
        try:
            user = current_app.db.query(User).filter(User.email == email, User.token.isnot(None)).one()
        except NoResultFound as e:
            logger.warn(f"User with email '{email}' for resetting password not found", exc_info=e)
            return error_view(form=form, errors=[_("User not found.")])

        token = self.reset_token_serializer.dumps({ "action": "reset_token", "id": user.id })
        text = render_template(mail_template, link=url_for("do_reset_token", token=token, _external=True))
        body = mail.Email(
            from_address=cast(str, self.from_address),
            subject=_("Reset your access token"),
            text=text,
            to=[email],
        )
        mail.send_email(self.smtp_server, body.make_email())

        return redirect(success_url, code=303)

    @hide_referrer
    def do_reset_token_route(self, success_url: str, mail_template: str, login_route: str):
        try:
            token = self.reset_token_serializer.loads(request.args["token"], max_age=self.reset_token_timeout)
            if token["action"] != "reset_token":
                raise RuntimeError("Invalid action")
            user_id = token["id"]
        except (KeyError, RuntimeError, itsdangerous.exc.BadData) as e:
            logger.warn(f"Invalid reset token link", exc_info=e)
            abort(400)

        try:
            user = current_app.db.query(User).get(user_id)
        except ObjectDeletedError:
            user = None

        if user is None:
            logger.warn(f"User with id '{user_id}' not found when following reset token link")
            abort(404)

        user.token = uuid.uuid4()
        current_app.db.commit()

        self._send_token(user, mail_template, login_route)
        return redirect(success_url, code=303)
