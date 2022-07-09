{ lib
, bash
, stdenv
, buildPythonPackage
, pkgs
, werkzeug
, flask
, flask_login
, flask_wtf
, flask-accept
, flask-cors
, Babel
, flask-babel
, sqlalchemy
, psycopg2
, dataclasses-json
, email_validator
, pytz
, dateutil
, pyyaml
, watchdog
, mypy
, gunicorn
, pyjwt
, aiohttp-xmlrpc
, enableSupervisor ? stdenv.isLinux
, supervisor
, coreutils
, nginx
, bubblewrap
, docker
, docker-compose
}:

let
  myWerkzeug = werkzeug.overrideAttrs (oldAttrs: rec {
    postPatch = ''
      substituteInPlace src/werkzeug/_reloader.py \
        --replace "rv = [sys.executable]" "return sys.argv"
    '';
    doCheck = false;
  });

  myFlask = flask.override ({ werkzeug = myWerkzeug; });

  scriptsPath = [ coreutils docker-compose bubblewrap pkgs.docker ];
  supervisorPath = [ nginx ];
  shellPath = [ myFlask gunicorn mypy ] ++ lib.optionals enableSupervisor (supervisorPath ++ scriptsPath);

in buildPythonPackage {
  name = "kyzylborda";

  src = lib.cleanSourceWith {
    filter = name: type: let baseName = baseNameOf (toString name); in !lib.hasSuffix ".nix" baseName;
    src = lib.cleanSource ./.;
  };

  buildInputs = [ bash ];

  propagatedBuildInputs = [
    flask
    flask_login
    flask_wtf
    flask-babel
    flask-accept
    flask-cors
    sqlalchemy
    psycopg2
    dataclasses-json
    email_validator
    pytz
    dateutil
    pyyaml
    watchdog
    pyjwt
    aiohttp-xmlrpc
  ] ++ lib.optional enableSupervisor supervisor;

  nativeBuildInputs = [
    Babel
  ];

  shellHook = ''
    export PATH="$PWD/scripts:${lib.makeBinPath shellPath}:$PATH"
  '';

  makeWrapperArgs = lib.optionals enableSupervisor [ "--prefix" "PATH" ":" (lib.makeBinPath supervisorPath) ];

  postFixup = ''
    patchShebangs $out/bin
    for i in scripts/*; do
      name="$(basename "$i")"
      wrapProgram "$out/bin/$name" --prefix PATH : ${lib.makeBinPath scriptsPath}
    done
  '';
}
