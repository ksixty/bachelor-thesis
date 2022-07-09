set -eo pipefail
shopt -s inherit_errexit

proxyDb="/var/lib/schoolos-proxy/proxy-db.json"

usage() {
  echo "$0 -t tag -C raw-clients.json assignments.json" >&2
  exit 1
}

tag=""

while getopts "t:C:" arg; do
  case "$arg" in
    t)
      tag="$OPTARG"
      ;;
    C)
      clientsDb="$OPTARG"
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

shift $((OPTIND-1))

[ -n "$tag" ] || usage
[ -n "$clientsDb" ] || usage

assignsDb="$1"
[ -n "$assignsDb" ] || usage

if [ ! -e "$assignsDb" ]; then
  echo '{}' > "$assignsDb"
fi

updateAssignsDb() {
  jq "$@" "$assignsDb" > "$assignsDb.tmp"
  mv -f "$assignsDb.tmp" "$assignsDb"
}

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

availableMachines=($(jq -r --arg tag "$tag" -r '.fallback | to_entries[] | select(.value.tag == $tag) | .key' "$proxyDb"))
provisionedMachines=($(jq --arg tag "$tag" -r 'to_entries[] | select(.value.tag == $tag) | .key' "$clientsDb"))

for id in "${provisionedMachines[@]}"; do
  existingMachineId="$(jq -r --arg short_id "$id" '.[$short_id]' "$assignsDb")"
  if [ "$existingMachineId" != "null" ]; then
    continue
  fi

  echo "Provisioning client $id..."
  emptyMachineId="${availableMachines[0]}"
  if [ -z "$emptyMachineId" ]; then
    echo "No empty machines with tag $tag"
    exit 1
  fi
  availableMachines=("${availableMachines[@]:1}")

  jq -r --arg short_id "$id" '.[$short_id].sshPrivate' "$clientsDb" > "$tmpdir/ssh-private"
  if ! [ -s "$tmpdir/ssh-private" ]; then
    echo "Private SSH key not found"
    exit 1
  fi

  schoolos-remote-customize -S "$tmpdir/ssh-private" -u "$emptyMachineId"
  updateAssignsDb --arg short_id "$id" --arg machine_id "$emptyMachineId" '.[$short_id] = $machine_id'
  echo "Finished provisioning $id"
done
