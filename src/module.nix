{ lib, config, pkgs, ... }:

with import ../lib { inherit lib; };

let
  cfg = config.schoolos;

  machineInfo = pkgs.writeScriptBin "schoolos-info" ''
    #!${pkgs.stdenv.shell}
    export PATH="${makeBinPath (with pkgs; [ coreutils ])}"

    echo "short-id: $(cat /provision/short-id 2>/dev/null)"
    echo "long-id: $(cat /provision/long-id 2>/dev/null)"
    echo "machine-id: $(cat /etc/machine-id 2>/dev/null)"
    echo "tag: $(cat /etc/schoolos-tag 2>/dev/null)"
  '';

  machineHealth = pkgs.writeScriptBin "schoolos-health" ''
    #!${pkgs.stdenv.shell}
    export PATH="/run/wrappers/bin:${makeBinPath (with pkgs; [ coreutils config.systemd.package ])}"
    source ${./health-lib.sh}

    [ -e /provision/short-id ] && [ -e /provision/long-id ]
    outputTest "System is provisioned" "$?"
    runTest "Hardware virtualization enabled" [ -e /dev/kvm ]
    runTest "Proctor host reachable" ping -c 1 ${cfg.proctorHost}
    runTest "SSH tunnel active" systemctl is-active --quiet schoolos-ssh-proxy.service
    runTest "SSH key is correct" [ "$(cat /run/schoolos-proxy-fallback)" = "0" ]
    runTest "Screencast active" systemctl is-active --quiet schoolos-record-screencast.service
    runTest "Screencasts are uploaded" [ "$(systemctl is-failed schoolos-upload-screencasts.service)" != "failed" ]

    [ ! -e /home/user/ovas ] || [ "$(ls /home/user/ovas 2>/dev/null | wc -l)" = "0" ]
    outputTest "OVAs are imported" "$?"

    exit "$returnCode"
  '';

  setHostname = pkgs.writeScriptBin "schoolos-set-hostname" ''
    #!${pkgs.stdenv.shell} -e
    export PATH="${makeBinPath (with pkgs; [ inetutils coreutils ])}"

    if [ -e /provision/short-id ]; then
      hostname="schoolos-$(cat /provision/short-id)"
    else
      hostname="schoolos"
    fi

    hostname "$hostname"
    echo "$hostname" > /etc/hostname
  '';

  userCheckSnippet = ''
    if [ "$(id -u)" = "1000" ] && [ -z "$SSH_CLIENT" ] && [ "$DISPLAY" != ":0" ]; then
      logger "Tried to log in as user from display '$DISPLAY', stdin '$(readlink /proc/$$/fd/0)'"
      echo "Logging from anywhere except first display instance is forbidden. This will be reported." >&2
      exit 1
    fi
  '';

  desktopSnippet = ''
    desktop="$(xdg-user-dir DESKTOP)"
    mkdir -p "$desktop"
    ln -sf /run/current-system/sw/share/applications/virtualbox.desktop "$desktop"
  '';

  allowX11ForScreencast = ''
    ${pkgs.xorg.xhost}/bin/xhost +si:localuser:schoolos-screencast
  '';

in {
  imports = [ ./platform.nix ];

  options = {
    schoolos = {

      openssh = {
        rootPublicKeyFile = mkOption {
          type = types.path;
          description = "Public key accepted for logging in as root.";
        };

        proctorPublicKeyFile = mkOption {
          type = types.path;
          description = "Proctoring host public key file.";
        };
      };

      screencast = {
        chunkDuration = mkOption {
          type = types.int;
          description = "Screencast chunk duration.";
          default = 5 * 60;
        };
      };

    };
  };

  config = {
    schoolos.firstRunScript = desktopSnippet;

    networking.networkmanager.enable = true;

    system.name = "schoolos";
    networking.hostName = ""; # Configure it dynamically.
    networking.fqdn = "schoolos.${config.networking.domain}";

    services.openssh = {
      enable = true;
      passwordAuthentication = false;
      kbdInteractiveAuthentication = false;
      knownHosts.${cfg.proctorHost}.publicKeyFile = cfg.openssh.proctorPublicKeyFile;
      extraConfig = ''
        AllowGroups wheel root
      '';
    };

    system.activationScripts.schoolos-set-hostname = "${setHostname}/bin/schoolos-set-hostname";

    systemd.services.schoolos-ssh-proxy = {
      description = "SSH reverse proxy.";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      path = with pkgs; [ openssh setHostname inotify-tools config.systemd.package ];
      serviceConfig = {
        Restart = "always";
        RestartSec = 5;
        PrivateTmp = true;
      };
      script = ''
        set -euET -o pipefail
        shopt -s inherit_errexit

        # Open tunnel.
        openSsh() {
          ssh -o ServerAliveInterval=15 -fNMS /tmp/ssh schoolos-proxy@${cfg.proctorHost} "$@"
        }
        callSsh() {
          ssh -S /tmp/ssh placeholder "$@"
        }

        fallback="1"
        if [ -e /provision/ssh-private ]; then
          fallback="0"
          openSsh -i /provision/ssh-private || fallback="1"
        fi
        if [ "$fallback" != "0" ]; then
          echo "WARNING: Using fallback key"
          openSsh -i /root/.ssh/id_proxy
          echo 1 > /run/schoolos-proxy-fallback
        else
          eval "$(callSsh -- get-info)"

          echo "$long_id" > /provision/long-id

          old_short_id="$(cat /provision/short-id 2>/dev/null || true)"
          if [ "$old_short_id" != "$short_id" ]; then
            echo "$short_id" > /provision/short-id
            schoolos-set-hostname
            # We need to restart display manager, because X11 auth breaks.
            systemctl restart display-manager
          fi

          echo 0 > /run/schoolos-proxy-fallback
        fi

        port="$(callSsh -O forward -R 0:127.0.0.1:22)"
        echo "Port $port has been allocated"

        # Send my mapping.
        machine_id="$(cat /etc/machine-id)"
        tag="$(cat /etc/schoolos-tag 2>/dev/null || true)"
        while true; do
          callSsh -- update "$machine_id" "$port" "$tag"
          # Wait while checking that SSH control socket is still alive.
          inotifywait -qqt 30 /tmp/ssh || true
        done
      '';
    };

    systemd.services.schoolos-record-screencast = {
      description = "Record screencast.";
      wantedBy = [ "graphical.target" ];
      wants = [ "display-manager.service" ];
      after = [ "display-manager.service" ];
      path = with pkgs; [ ffmpeg ];
      serviceConfig = {
        Restart = "always";
        StateDirectory = "schoolos-screencast";
        RestartSec = 5;
        User = "schoolos-screencast";
        Group = "schoolos-screencast";
      };
      script = ''
        export DISPLAY=:0

        while true; do
          file_name="video-$(date +"%FT%T.%6N").mkv"
          echo "Recording $file_name"
          ffmpeg \
            -t ${toString cfg.screencast.chunkDuration} \
            -framerate 2 \
            -f x11grab -i :0 \
            -vf "pad=iw:ih+34:0:0,drawtext=x=10:y=h-32:fontsize=30:fontcolor=White:text=%{gmtime}" \
            -c:v libx264rgb -crf 0 \
            "/var/lib/schoolos-screencast/$file_name"
        done
      '';
    };

    systemd.services.schoolos-upload-screencasts = {
      description = "Upload screencasts.";
      path = with pkgs; [ openssh lsof ];
      after = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
      };
      script = ''
        casts="$(ls /var/lib/schoolos-screencast | sort)"
        if [ -n "$casts" ]; then
          for cast in $casts; do
            path="/var/lib/schoolos-screencast/$cast"
            if [ "$(lsof -t "$path" | wc -w)" = "0" ]; then
              ssh -i /provision/ssh-private schoolos-proctor@${cfg.proctorHost} -- upload-screencast "$cast" < "$path"
              rm "$path"
            fi
          done
        fi
      '';
    };

    systemd.timers.schoolos-upload-screencasts = {
      description = "Periodically upload screencasts.";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnActiveSec = 60;
        OnUnitActiveSec = 60;
      };
    };

    systemd.user.services.schoolos-import-ovas = {
      description = "Import OVAs to VirtualBox.";
      wantedBy = [ "default.target" ];
      path = with pkgs; [ config.virtualisation.virtualbox.host.package ];
      serviceConfig = {
        Type = "oneshot";
      };
      script = ''
        shopt -s nullglob
        for ova in ~/ovas/*.ova; do
          fname="$(basename "$ova")"
          vmname="''${fname%.ova}"
          echo "Importing $vmname"

          should_install="1"
          if VBoxManage showvminfo "$vmname" >/dev/null 2>&1; then
            if [ -e ~/ovas/"$fname.lock" ]; then
              # If an import was in progress before, remove current VM; most likely it's broken.
              echo "Removing VM that wasn't fully imported"
              VBoxManage unregister --delete "$vmname" || true
            else
              should_install="0"
            fi
          fi

          if [ "$should_install" != "0" ]; then
            touch ~/ovas/"$fname.lock"
            VBoxManage import "$ova" --vsys 0 --eula accept --vmname "$vmname"
            VBoxManage snapshot "$vmname" take "pristine"
            rm ~/ovas/"$fname.lock"
          else
            echo "Already imported"
          fi
          rm -f "$ova"
        done
      '';
    };

    nixpkgs.overlays = [ (self: super: {
      inherit machineInfo machineHealth setHostname;
    }) ];

    environment.systemPackages = with pkgs; [
      openssh
      machineInfo
      machineHealth
      setHostname
    ];

    environment.loginShellInit = mkBefore userCheckSnippet;

    virtualisation.virtualbox.host.enable = true;

    services.xserver.displayManager = {
      setupCommands = mkMerge [(mkBefore userCheckSnippet) allowX11ForScreencast];
      autoLogin.user = "user";
    };

    users = {
      extraUsers = {
        root = {
          openssh.authorizedKeys.keyFiles = [ cfg.openssh.rootPublicKeyFile ];
        };

        user = {
          uid = 1000;
          isNormalUser = true;
          password = "";
          extraGroups = [ "vboxusers" ];
        };

        schoolos-screencast = {
          isSystemUser = true;
          group = "schoolos-screencast";
          home = "/var/lib/schoolos-screencast";
        };
      };
      extraGroups = {
        schoolos-screencast = {};
      };
    };

    security.pam.loginLimits = [
      # Give proctoring more space to breathe.
      { domain = "user";
        type = "hard";
        item = "priority";
        value = "10";
      }
    ];
  };
}
