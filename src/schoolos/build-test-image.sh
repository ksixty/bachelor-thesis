#!/usr/bin/env nix-shell
#!nix-shell -i bash -p bash
set -eo pipefail
shopt -s nullglob inherit_errexit

usage() {
  echo "$0 -c path/to/configuration.nix path/to/image.iso" >&2
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

image_path="$1"
shift || usage

result="$(nix-build ./test --no-out-link -A isoImage -I schoolos-config="$configuration_path")"
cp --no-preserve=mode --reflink=auto "$result/iso/schoolos-test.iso" "$image_path"
