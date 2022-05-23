#!/usr/bin/env nix-shell
#!nix-shell -i bash -p qemu
set -eo pipefail
shopt -s nullglob inherit_errexit

usage() {
  echo "$0 -c path/to/configuration.nix" >&2
  exit 1
}

configuration_path=""

while getopts "c:" arg; do
  case "$arg" in
    c)
      configuration_path="$OPTARG"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

shift $((OPTIND-1))

[ -n "$configuration_path" ] || usage

result="$(nix-build ./test --no-out-link -A vm -I schoolos-config="$configuration_path")"
echo "$result"
run="$result/bin/run-schoolos-test-vm"

exec "$run" -cpu host -smp 2 "$@"
