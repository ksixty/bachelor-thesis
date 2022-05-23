#!/usr/bin/env nix-shell
#!nix-shell -i bash -p libguestfs-with-appliance
set -eo pipefail
shopt -s nullglob inherit_errexit

usage() {
  echo "$0 -c path/to/configuration.nix -i id_proxy [ -o path/to/ovas ] [ -t tag ] path/to/image.img" >&2
  exit 1
}

configuration_path=""
id_proxy=""
ovas_path=""
tag=""

while getopts "c:i:o:t:" arg; do
  case "$arg" in
    c)
      configuration_path="$OPTARG"
      ;;
    i)
      id_proxy="$OPTARG"
      ;;
    o)
      ovas_path="$OPTARG"
      ;;
    t)
      tag="$OPTARG"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

shift $((OPTIND-1))

[ -n "$configuration_path" ] || usage
[ -n "$id_proxy" ] || usage

image_path="$1"
shift || usage

result="$(nix-build ./client --no-out-link -A raw -I schoolos-config="$configuration_path")"

cp "$result/nixos.img" "$image_path"
chmod 644 "$image_path"

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

cp "$id_proxy" "$tmpdir/id_proxy"
chmod 600 "$tmpdir/id_proxy"
guestfish_args=(
  mkdir-p /root/.ssh :
  copy-in "$tmpdir/id_proxy" /root/.ssh :
  chown 0 0 /root/.ssh/id_proxy
)

if [ -n "$ovas_path" ]; then
  grow_size="$(du -bs "$ovas_path" | cut -f 1)"
  echo "Total OVA images size: $grow_size"
  qemu-img resize -f raw "$image_path" +"$grow_size"
  end_sectors=34 # No idea why, found by binary search.
  sectors="$(guestfish --format=raw --rw -a "$image_path" launch : part-expand-gpt /dev/sda : blockdev-getsz /dev/sda)"
  guestfish --format=raw --rw -a "$image_path" launch : part-resize /dev/sda 3 $((sectors - end_sectors)) : resize2fs /dev/sda3

  guestfish_args+=(
    : mkdir-p /home/user/ovas
    : chown 1000 100 /home/user
    : chown 1000 100 /home/user/ovas
  )
  for ova in "$ovas_path/"*.ova; do
    fname="$(basename "$ova")"
    guestfish_args+=(
      : copy-in "$ova" /home/user/ovas
      : chown 1000 100 "/home/user/ovas/$fname"
    )
  done
fi

if [ -n "$tag" ]; then
  echo "$tag" > "$tmpdir/schoolos-tag"
  guestfish_args+=(
    : copy-in "$tmpdir/schoolos-tag" /etc/
    : chown 0 0 /etc/schoolos-tag
  )
fi

guestfish --format=raw --rw -m /dev/sda3:/ -a "$image_path" "${guestfish_args[@]}"
