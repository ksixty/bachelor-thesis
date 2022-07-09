{ configuration ? <schoolos-config>
, system ? "x86_64-linux"
}:

let
  eval = import <nixpkgs/nixos/lib/eval-config.nix> {
    inherit system;
    modules = [ ./module.nix configuration ];
  };

  evalUsb = import <nixpkgs/nixos/lib/eval-config.nix> {
    inherit system;
    modules = [ ./module.nix ./usb.nix configuration ];
  };
in {
  inherit (eval.config.system.build) vm;
  inherit (evalUsb.config.system.build) raw;
}
