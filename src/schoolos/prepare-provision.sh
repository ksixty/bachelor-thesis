#!/usr/bin/env nix-shell
#!nix-shell -i bash -p jq
set -eo pipefail
shopt -s nullglob inherit_errexit

tmpdir="$(mktemp -d)"
chmod 700 "$tmpdir"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

cat "$1" > "$tmpdir/out"
while read pair; do
  values=($pair)
  id="${values[0]}"
  hasPrivate="${values[1]}"
  if [ "$hasPrivate" != "true" ]; then
    rm -f "$tmpdir/ssh-key" "$tmpdir/ssh-key.pub"
    ssh-keygen -N "" -t ed25519 -f "$tmpdir/ssh-key" >/dev/null
    private="$(cat "$tmpdir/ssh-key")"
    public="$(cat "$tmpdir/ssh-key.pub")"
  else
    private="$(jq -r --arg id "$id" '.[$id].sshPrivate' "$1")"
    echo "$private" > "$tmpdir/ssh-key"
    chmod 600 "$tmpdir/ssh-key"
    public="$(ssh-keygen -y -f "$tmpdir/ssh-key")"
  fi
  jq --arg id "$id" --arg public "$public" --arg private "$private" -r '.[$id].sshPublic = $public | .[$id].sshPrivate = $private' "$tmpdir/out" > "$tmpdir/out.tmp"
  mv "$tmpdir/out.tmp" "$tmpdir/out"
done < <(jq -r 'keys[] as $k | "\($k) \(.[$k] | has("sshPrivate"))"' "$1")

cat "$tmpdir/out"
