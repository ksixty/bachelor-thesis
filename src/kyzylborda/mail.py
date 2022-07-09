from dataclasses import dataclass, field
from typing import List, Optional
from email.header import Header
from email.utils import parseaddr
from email.message import Message
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from smtplib import SMTP, SMTP_SSL, SMTPNotSupportedError


@dataclass(frozen=True)
class SMTPServer:
    host: str
    port: int
    login: Optional[str] = None
    password: Optional[str] = None
    ssl: bool = False
    starttls: bool = False


# Fix display of addresses in K-9 - encode them as Thunderbird does.
def addresses_header(addrs: List[str]) -> str:
    header = Header()
    left = len(addrs)
    for addr in addrs:
        realname, email = parseaddr(addr)
        comma = "" if left == 1 else ","
        if len(realname) == 0 and len(email) == 0:
            header.append(addr + comma)
        else:
            header.append(realname)
            header.append(f"<{email}>{comma}")
        left -= 1
    return header.encode()


@dataclass
class Email:
    from_address: str
    subject: str
    text: str
    to: List[str]
    cc: List[str] = field(default_factory=lambda: [])
    bcc: List[str] = field(default_factory=lambda: [])

    def make_email(self) -> MIMEText:
        msg = MIMEText(self.text)
        msg["From"] = addresses_header([self.from_address])
        msg["Subject"] = str(self.subject)
        msg["To"] = addresses_header(self.to)
        msg["Cc"] = addresses_header(self.cc)
        msg["Bcc"] = addresses_header(self.bcc)
        return msg


def send_email(server: SMTPServer, email: Message):
    if not server.ssl:
        connection = SMTP(server.host, server.port)
    else:
        connection = SMTP_SSL(server.host, server.port)
    with connection as sess:
        if server.starttls:
            try:
                resp = sess.starttls()
                if resp[0] != 220:
                    raise RuntimeError("STARTTLS failed")
            except SMTPNotSupportedError:
                raise RuntimeError("STARTTLS not supported")
        if server.login is not None:
            if server.password is None:
                raise RuntimeError("Password is not specified, but login is")
            sess.login(server.login, server.password)
        sess.send_message(email)
