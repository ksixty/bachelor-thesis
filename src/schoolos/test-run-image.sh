#!/usr/bin/env nix-shell
#!nix-shell -i bash -p qemu -p libguestfs-with-appliance
set -eo pipefail
shopt -s nullglob inherit_errexit

usage() {
  echo "$0 -i id_proxy -c path/to/configuration.nix [ -o path/to/ovas ] [ -s short_id ] [ -l long_id ] [ -S ssh_private ] [ -t tag ] [ -d disk_image ]" >&2
  exit 1
}

id_proxy=""
configuration_path=""
ovas_path=""
short_id=""
long_id=""
ssh_private=""
tag=""
disk_image="schoolos.qcow2"

while getopts "i:c:o:s:l:S:d:t:" arg; do
  case "$arg" in
    i)
      id_proxy="$OPTARG"
      ;;
    c)
      configuration_path="$OPTARG"
      ;;
    o)
      ovas_path="$OPTARG"
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
    d)
      disk_image="$OPTARG"
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

[ -n "$id_proxy" ] || usage
[ -n "$configuration_path" ] || usage

result="$(nix-build ./client --no-out-link -A vm -I schoolos-config="$configuration_path")"
run="$result/bin/run-schoolos-vm"

if [ ! -e "$disk_image" ]; then
  qemu-img create -f qcow2 "$disk_image" 32G

  tmpdir="$(mktemp -d)"
  cleanup() {
    rm -rf "$tmpdir"
  }

  trap cleanup EXIT

  mkdir -p "$tmpdir/.ssh"
  cp "$id_proxy" "$tmpdir/.ssh/id_proxy"
  chmod 600 "$tmpdir/.ssh/id_proxy"

  guestfish_args=(
    launch :
    mke2fs /dev/sda :
    mount /dev/sda / :
    mkdir-p /root :
    chmod 0600 /root :
    copy-in "$tmpdir/.ssh" /root/ :
    chown 0 0 /root/.ssh :
    chown 0 0 /root/.ssh/id_proxy :
    copy-in "$tmpdir/provision" /
  )

  mkdir -p "$tmpdir/provision"
  if [ -n "$short_id" ]; then
    echo "$short_id" > "$tmpdir/provision/short-id"
    guestfish_args+=(: chown 0 0 /provision/short-id)
  fi
  if [ -n "$long_id" ]; then
    echo "$long_id" > "$tmpdir/provision/long-id"
    guestfish_args+=(: chown 0 0 /provision/long-id)
  fi
  if [ -n "$tag" ]; then
    echo "$tag" > "$tmpdir/schoolos-tag"
    guestfish_args+=(
      : mkdir-p /etc
      : copy-in "$tmpdir/schoolos-tag" /etc
      : chown 0 0 /etc/schoolos-tag
    )
  fi
  if [ -n "$ssh_private" ]; then
    cp "$ssh_private" "$tmpdir/provision/ssh-private"
    chmod 600 "$tmpdir/provision/ssh-private"
    guestfish_args+=(: chown 0 0 /provision/ssh-private)
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

  guestfish --format=qcow2 --rw -a "$disk_image" "${guestfish_args[@]}"
  rm -rf "$tmpdir"
  trap - EXIT
fi

NIX_DISK_IMAGE="$disk_image" exec "$run" -cpu host -smp 2 "$@"
