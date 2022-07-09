import os.path
import signal
from dataclasses import dataclass
from threading import Thread, Lock
from typing import Set, List, Optional, Union

from ..cache import TasksCache
from ..tasks import SocketType
from .supervisor import Supervisor, SupervisorProgram


@dataclass
class SocketSpec:
    type: SocketType
    path: str
    http_hostnames: Optional[Set[str]] = None
    tcp_port: Optional[int] = None


def unix_proxy_location_conf(path: str) -> str:
    return f"""
        proxy_pass http://unix:{path};
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    """


class SocketRedirect:
    _supervisor: Supervisor
    _program_name: str
    _http_listen: Optional[Union[int, str]]
    _sockets: List[SocketSpec]
    _default_http_socket: Optional[str]

    def __init__(
            self,
            supervisor: Supervisor,
            program_name: Optional[str] = None,
            http_listen: Optional[Union[int, str]] = None,
            sockets: Optional[List[SocketSpec]] = None,
            default_http_socket: Optional[str] = None,
    ):
        self._http_listen = http_listen
        if sockets is None:
            sockets = []
        if program_name is None:
            program_name = "nginx"
        self._program_name = program_name
        self._supervisor = supervisor
        self._sockets = sockets
        self._default_http_socket = default_http_socket
        self._nginx_dir = os.path.join(supervisor.tmp_dir, program_name)
        os.makedirs(self._nginx_dir, exist_ok=True)
        self._write_config()

    def _write_config(self):
        with open(os.path.join(self._nginx_dir, "nginx.conf"), "w") as c:
            c.write(f"""
                daemon off;
                error_log stderr info;
                pid {self._nginx_dir}/nginx.pid;

                events {{
                }}""")

            if self._http_listen is not None:
                c.write(f"""
                    http {{
                        access_log stderr;
                        client_body_temp_path {self._nginx_dir}/client_body;
                        proxy_temp_path {self._nginx_dir}/proxy;
                        fastcgi_temp_path {self._nginx_dir}/fastcgi;
                        uwsgi_temp_path {self._nginx_dir}/uwsgi;
                        scgi_temp_path {self._nginx_dir}/scgi;

                        map $http_upgrade $connection_upgrade {{
                            default upgrade;
                            ''      close;
                        }}

                        server {{
                            listen {self._http_listen};
                            location / {{
                                proxy_set_header Host $host;
                                {unix_proxy_location_conf(self._default_http_socket) if self._default_http_socket is not None else "return 404;"}
                            }}
                        }}
                """)
                for spec in self._sockets:
                    if spec.type != SocketType.HTTP:
                        continue
                    # Notice that we don't explicitly pass X-Forwarded-* headers.
                    # nginx already does this by default, so we have no need to do it.
                    # Outermost proxy should have `recommendedProxySettings = true`
                    # and it will just work.
                    #
                    # Don't forget ProxyFix middleware for Flask!
                    hostnames = " ".join([name for hostname in spec.http_hostnames for name in [hostname, f"*.{hostname}"]])
                    c.write(f"""
                        server {{
                            listen {self._http_listen};
                            server_name {hostnames};
                            location / {{
                                proxy_set_header Host $host;
                                {unix_proxy_location_conf(spec.path)}
                            }}
                        }}
                    """)
                c.write("""
                    }
                """)

            c.write(f"""
                stream {{
            """)
            for spec in self._sockets:
                if spec.type != SocketType.TCP:
                    continue
                c.write(f"""
                    server {{
                        listen {spec.tcp_port};
                        proxy_pass unix:{spec.path};
                    }}
                """)
            c.write("""
                }
            """)

    @property
    def spec(self):
        return SupervisorProgram(
            exec=["nginx", "-p", self._nginx_dir, "-e", "stderr", "-c", "nginx.conf"],
            auto_restart="true",
        )

    async def reload(self):
        self._write_config()
        if self._program_name in self._supervisor.programs:
            await self._supervisor.signal(self._program_name, signal.SIGHUP)

    async def set_default_http_socket(self, default_http_socket: Optional[str]):
        self._default_http_socket = default_http_socket
        await self.reload()

    @property
    def sockets(self) -> List[SocketSpec]:
        return self._sockets

    async def set_sockets(self, sockets: List[SocketSpec]):
        self._sockets = sockets
        await self.reload()
