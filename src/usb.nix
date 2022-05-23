{ lib, config, pkgs, ... }:

with lib;

let
  name = "schoolos";
in {
  fileSystems."/" = {
    device = "/dev/disk/by-label/${name}";
    autoResize = true;
    fsType = "ext4";
  };

  # Needed on some hardware, I think on EFI boot but I'm not sure.
  systemd.services.find-boot = {
    description = "Find and mount /boot if it still not mounted.";
    wantedBy = [ "multi-user.target" ];
    path = [ pkgs.utillinux pkgs.gnugrep ];
    serviceConfig = {
      Type = "oneshot";
    };
    script = ''
      if ! grep -qs "/boot " /proc/mounts; then
        efifs="$(lsblk -no pkname ${config.fileSystems."/".device})1"
        mount -t vfat "/dev/$efifs" /boot
      fi
    '';
  };

  boot = {
    growPartition = true;
    loader.grub = {
      device = mkDefault "/dev/sda";
      efiSupport = true;
      efiInstallAsRemovable = true;
    };
  };

  system.build.raw = import <nixpkgs/nixos/lib/make-disk-image.nix> {
    inherit lib config pkgs;
    partitionTableType = "hybrid";
    label = name;
    format = "raw";
  };
}
