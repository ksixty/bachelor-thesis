{ lib, config, pkgs, ... }:

with import ../lib { inherit lib; };

let
  cfg = config.schoolos;

  clientsDb = pkgs.writeText "clients-db.json" (builtins.toJSON (mapAttrs (id: info: {
    longId = info.longId;
    tag = info.tag;
  }) cfg.clients));

  proxyCommand = pkgs.writeScript "handle-proxy" ''
    #!${pkgs.bash}/bin/bash
    export PATH=${makeBinPath (with pkgs; [ jq coreutils utillinux ])}
    clientsDb="${clientsDb}"
    source ${./handle-proxy.sh}
  '';

  proctorCommand = pkgs.writeScript "handle-proctor" ''
    #!${pkgs.bash}/bin/bash
    export PATH=${makeBinPath (with pkgs; [ coreutils utillinux inetutils ])}
    source ${./handle-proctor.sh}
  '';

  sshToClientCommand = pkgs.writeScriptBin "schoolos-ssh-to-client" ''
    #!${pkgs.bash}/bin/bash
    export PATH=${makeBinPath (with pkgs; [ jq coreutils openssh ])}
    source ${./ssh-to-client.sh}
  '';

  customizeCommand = pkgs.writeScriptBin "schoolos-remote-customize" ''
    #!${pkgs.bash}/bin/bash
    export PATH=${makeBinPath (with pkgs; [ jq coreutils openssh ])}
    source ${./remote-customize.sh}
  '';

  massCustomizeCommand = pkgs.writeScriptBin "schoolos-mass-remote-customize" ''
    #!${pkgs.bash}/bin/bash
    export PATH=${makeBinPath (with pkgs; [ jq coreutils customizeCommand ])}
    source ${./mass-remote-customize.sh}
  '';

  cleanProxyDb = pkgs.writeScript "clean-proxy-db" ''
    #!${pkgs.bash}/bin/bash
    export PATH=${makeBinPath (with pkgs; [ jq coreutils utillinux ])}
    source ${./clean-proxy-db.sh}
  '';

  clientsCommand = pkgs.writeScriptBin "schoolos-clients" ''
    #!${pkgs.stdenv.shell}
    export PATH=${makeBinPath (with pkgs; [ jq ])}
    [ -e /var/lib/schoolos-proxy/proxy-db.json ] || exit 0
    exec jq -r '(.clients | to_entries | map({shortId: .key} + .value)) + (.fallback | to_entries | map({machineId: .key} + .value)) | sort_by(.modified) | reverse | .[]' /var/lib/schoolos-proxy/proxy-db.json
  '';

  clientModule = {
    options = {
      longId = mkOption {
        type = types.str;
        description = "Human-readable name for the client.";
      };

      tag = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Client tag, for matching with machines.";
      };

      openssh.publicKey = mkOption {
        type = types.str;
        description = "OpenSSH public key.";
      };
    };
  };

  authorizedKeysCommands = cmd: keys: concatStringsSep "\n" (mapAttrsToList (id: key:
    ''command="${cmd} ${id}" ${key}'') keys);

  clientKeys = mapAttrs (name: client: client.openssh.publicKey) cfg.clients;

  proxyAuthorizedKeys = pkgs.writeText "proxy-authorized-keys" ''
    command="${proxyCommand}" ${cfg.openssh.fallbackProxyKey}
    ${authorizedKeysCommands proxyCommand clientKeys}
  '';

  proctorAuthorizedKeys = pkgs.writeText "proctor-authorized-keys" (authorizedKeysCommands proctorCommand clientKeys);

in {
  options = {
    schoolos = {

      openssh = {
        fallbackProxyKey = mkOption {
          type = types.str;
          description = "Public SSH key accepted as a fallback for proxying client machines.";
        };
      };

      clients = mkOption {
        type = types.attrsOf (types.submodule clientModule);
        description = "Proctored clients, keyed by name.";
      };

      rawClients = mkOption {
        type = types.nullOr (types.attrsOf types.anything);
        description = "Raw clients JSON dictionary.";
      };

    };
  };

  config = {
    schoolos.clients = mkIf (cfg.rawClients != null) (mapAttrs (id: attrs: {
      longId = attrs.longId;
      tag = attrs.tag or null;
      openssh.publicKey = replaceStrings ["\n"] [""] attrs.sshPublic;
    }) cfg.rawClients);

    networking.firewall.allowedTCPPorts = [ 13370 ];

    services.openssh = {
      enable = true;
      ports = [ 22 13370 ];
      extraConfig = ''
        Match User schoolos-proxy
          AllowTcpForwarding remote
          X11Forwarding no
          ClientAliveInterval 15

        Match User schoolos-proctor
          AllowTcpForwarding no
          X11Forwarding no
          ClientAliveInterval 15
      '';
    };

    systemd.services.schoolos-clean-proxy-db = {
      description = "Remove stale entries from proxy-db.";
      serviceConfig = {
        ExecStart = cleanProxyDb;
        Type = "oneshot";
        User = "schoolos-proxy";
      };
    };

    systemd.timers.schoolos-clean-proxy-db = {
      description = "Periodically remove stale entries from proxy-db.";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnActiveSec = 60;
        OnUnitActiveSec = 60;
      };
    };

    environment.systemPackages = [
      clientsCommand
      sshToClientCommand
      customizeCommand
      massCustomizeCommand
    ];

    users.users = {
      schoolos-proctor = {
        isSystemUser = true;
        group = "schoolos-proctor";
        shell = pkgs.bashInteractive;
        home = "/var/lib/schoolos-proctor";
        createHome = true;
        openssh.authorizedKeys.keyFiles = [ proctorAuthorizedKeys ];
      };

      schoolos-proxy = {
        isSystemUser = true;
        group = "schoolos-proxy";
        shell = pkgs.bashInteractive;
        home = "/var/lib/schoolos-proxy";
        createHome = true;
        openssh.authorizedKeys.keyFiles = [ proxyAuthorizedKeys ];
      };
    };

    users.groups = {
      schoolos-proctor = {};
      schoolos-proxy = {};
    };
  };
}
