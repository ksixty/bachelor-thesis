#!/usr/bin/env nix-shell
#!nix-shell -i bash -p libguestfs-with-appliance
set -eo pipefail
shopt -s inherit_errexit

usage() {
  echo "$0 [ -G grow_size_in_bytes ] [ -s short_id ] [ -l long_id ] [ -S ssh_private ] [ -i id_proxy ] [ -o path/to/ovas ] [ -t tag ] path/to/image.img" >&2
  exit 1
}

grow_size=""
short_id=""
long_id=""
ssh_private=""
id_proxy=""
ovas_path=""
tag=""

while getopts "G:s:l:S:i:o:t:" arg; do
  case "$arg" in
    G)
      grow_size="$OPTARG"
      ;;
    s)
      short_id="$OPTARG"
      ;;
    l)
      long_id="$OPTARG"
      ;;
    S)
      ssh_private="$OPTARG"
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

image_path="$1"
[ -n "$image_path" ] || usage

if [ -n "$grow_size" ]; then
  qemu-img resize -f raw "$image_path" +"$grow_size"
  end_sectors=34 # No idea why, found by binary search.
  sectors="$(guestfish --format=raw --rw -a "$image_path" launch : part-expand-gpt /dev/sda : blockdev-getsz /dev/sda)"
  guestfish --format=raw --rw -a "$image_path" launch : part-resize /dev/sda 3 $((sectors - end_sectors)) : resize2fs /dev/sda3
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

guestfish_args=(mkdir-p /provision)

if [ -n "$short_id" ]; then
  echo "$short_id" > "$tmpdir/short-id"
  guestfish_args+=(
    : copy-in "$tmpdir/short-id" /provision
    : chown 0 0 /provision/short-id
  )
fi

if [ -n "$long_id" ]; then
  echo "$long_id" > "$tmpdir/long-id"
  guestfish_args+=(
    : copy-in "$tmpdir/long-id" /provision
    : chown 0 0 /provision/long-id
  )
fi

if [ -n "$ssh_private" ]; then
  cp "$ssh_private" "$tmpdir/ssh-private"
  chmod 600 "$tmpdir/ssh-private"
  guestfish_args+=(
    : copy-in "$tmpdir/ssh-private" /provision
    : chown 0 0 /provision/ssh-private
  )
fi

if [ -n "$id_proxy" ]; then
  cp "$id_proxy" "$tmpdir/id_proxy"
  chmod 600 "$tmpdir/id_proxy"
  guestfish_args=(
    mkdir-p /root/.ssh :
    copy-in "$tmpdir/id_proxy" /root/.ssh :
    chown 0 0 /root/.ssh/id_proxy
  )
fi

if [ -n "$ovas_path" ]; then
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
