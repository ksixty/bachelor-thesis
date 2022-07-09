{ lib, config, pkgs, ... }:

with import ../lib { inherit lib; };

let
  cfg = config.schoolos;

  userLoginSnippet = ''
    if [ "$(id -u)" -gt "999" ] && [ -z "$SSH_CLIENT" ]; then
      if [ ! -e ~/.firstrun ]; then
        ${optionalString (cfg.userSkeleton != null) ''
          cp -fr --no-preserve=mode ${cfg.userSkeleton}/. $HOME/
        ''}

        ${cfg.firstRunScript}

        touch ~/.firstrun
      fi
    fi
  '';

in {
  imports = [
    <nixpkgs/nixos/modules/profiles/all-hardware.nix>
  ];

  options = {
    schoolos = {

      proctorHost = mkOption {
        type = types.str;
        description = "Proctoring host address.";
      };

      userSkeleton = mkOption {
        type = types.nullOr types.path;
        description = "User skeleton directory.";
        default = null;
      };

      firstRunScript = mkOption {
        type = types.lines;
        description = "Script which is executed for all non-system users on the first login.";
        default = "";
      };

    };
  };

  config = {
    assertions = [
      { assertion =
          let hashed = config.users.users.root.hashedPassword;
          in hashed != "" && hashed != null;
        message = "Root password must be non-empty";
      }
    ];

    networking.hostId = "f788df16"; # Needed for ZFS.

    networking.networkmanager.enable = true;

    networking.nameservers = [
      "1.1.1.1"
      "1.0.0.1"
      "8.8.8.8"
      "8.8.4.4"
    ];

    boot = {
      loader.timeout = mkForce 0;
      supportedFilesystems = [ "btrfs" "reiserfs" "vfat" "f2fs" "xfs" "zfs" "ntfs" "cifs" ];
    };

    console = {
      font = "ter-v16n";
      packages = with pkgs; [ terminus_font ];
    };

    environment.systemPackages = with pkgs; [
      htop
      vim
    ];

    services.xserver = {
      enable = true;
      libinput.enable = true;
      desktopManager.xfce.enable = true;
      displayManager = {
        setupCommands = userLoginSnippet;
        lightdm.enable = true;
        autoLogin = {
          enable = true;
        };
      };
    };

    environment.loginShellInit = userLoginSnippet;

    programs.nm-applet.enable = true;

    fonts.fonts = [ pkgs.dejavu_fonts ];
    fonts.fontconfig.enable = true;

    documentation.enable = false;
    documentation.nixos.enable = false;

    users.mutableUsers = false;
  };
}
