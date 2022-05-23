{ lib, pkgs, ... }:

with lib;

let
  ugractfWallpaper = pkgs.python3.pkgs.callPackage ./wallpaper { };

  setWallpaper = pkgs.writeScriptBin "ugractf-set-wallpaper" ''
    #!${pkgs.bash}/bin/bash -e
    export PATH="${makeBinPath (with pkgs; [ coreutils ugractfWallpaper gnugrep xfce.xfconf ])}"

    mode="$1"

    machine_id="$(cat /etc/machine-id)"
    wallpaper_args=(--machine-id "$machine_id")

    fail="0"

    short_id="$(cat /provision/short-id 2>/dev/null || true)"
    if [ -n "$short_id" ]; then
      wallpaper_args+=(--short-id "$short_id")
    else
      mode="fail"
    fi

    long_id="$(cat /provision/long-id 2>/dev/null || true)"
    if [ -n "$long_id" ]; then
      wallpaper_args+=(--long-id "$long_id")
    else
      mode="fail"
    fi

    if [ "$mode" = "in-progress" ]; then
      wallpaper_args+=(--in-progress)
    elif [ "$mode" = "fail" ]; then
      wallpaper_args+=(--fail)
    elif [ "$mode" != "normal" ]; then
      echo "Usage: $0 { normal | in-progress | fail }" >&2
      exit 1
    fi

    wallpaper "''${wallpaper_args[@]}" > "$HOME/wallpaper.png.tmp"
    mv -f $HOME/wallpaper.png.tmp "$HOME/wallpaper.png"

    for prop in $(xfconf-query --channel xfce4-desktop --property /backdrop --list | grep last-image); do
      xfconf-query --channel xfce4-desktop --property "$prop" --set "$HOME/wallpaper.png"
    done
  '';

  setInitialWallpaper = pkgs.writeScriptBin "ugractf-set-initial-wallpaper" ''
    #!${pkgs.stdenv.shell} -e
    export PATH="${makeBinPath (with pkgs; [ setWallpaper coreutils gnugrep xfce.xfconf ])}"

    # Sleep till Xfce gets fully initialized.
    for i in $(seq 1 60); do
      if xfconf-query --channel xfce4-desktop --property /backdrop --list 2>/dev/null | grep -q last-image; then
        break
      fi
      sleep 0.5
    done

    exec ugractf-set-wallpaper in-progress
  '';

in {
  imports = [ ./ugractf-common.nix ];

  schoolos = {
    userSkeleton = ./skel;

    openssh = {
      rootPublicKeyFile = ./id_root.pub;
      proctorPublicKeyFile = ./ssh_host_ed25519_key.pub;
    };
  };

  # money desk swimming having
  users.users.root.hashedPassword = "$6$P7TZM9NSJSL4AVhp$LJWejpAtleD5rRxRhb5nsgf8AsrD2ogxwwown/DVrEo28s5rVyfbOqV3jxNgdTzkNSWZIlZpRVZFKPjHYAoUn.";

  environment.systemPackages = with pkgs; [
    setWallpaper
    setInitialWallpaper
  ];

  systemd.user.services.ugractf-update-wallpaper = {
    description = "Set wallpaper depending on current health.";
    path = with pkgs; [ machineHealth setWallpaper ];
    after = [ "schoolos-import-ovas.service" ];
    serviceConfig = {
      Type = "oneshot";
    };
    script = ''
      if [ -z "$DISPLAY" ]; then
        echo "No DISPLAY set" >&2
        exit 1
      fi

      mode="normal"
      if ! schoolos-health >/dev/null; then
        mode="fail"
      fi

      exec ugractf-set-wallpaper "$mode"
    '';
  };

  systemd.user.timers.ugractf-update-wallpaper = {
    description = "Periodically set wallpaper depending on current health.";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnActiveSec = 30; # To ensure network is up and stuff.
      OnUnitActiveSec = 60;
    };
  };
}
