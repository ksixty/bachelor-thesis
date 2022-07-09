set -eo pipefail
shopt -s nullglob inherit_errexit

proxyDb="/var/lib/schoolos-proxy/proxy-db.json"

usage() {
  echo "$0 { -u machine_id | -c short_id } [ssh-args...]" >&2
  exit 1
}

short_id=""
machine_id=""

while getopts "u:c:" arg; do
  case "$arg" in
    u)
      machine_id="$OPTARG"
      ;;
    c)
      short_id="$OPTARG"
      ;;
    *)
      usage
      ;;
  esac
done

shift $((OPTIND-1))

if [ -n "$short_id" ]; then
  [ -z "$machine_id" ] || usage

  port="$(jq -r --arg short_id "$short_id" '.clients[$short_id].port' "$proxyDb" 2>/dev/null)"
  if [ -z "$port" ] || [ "$port" = "null" ]; then
    echo "Machine with short id $short_id is not accessible now" >&2
    exit 1
  fi
elif [ -n "$machine_id" ]; then
  [ -z "$short_id" ] || usage

  port="$(jq -r --arg machine_id "$machine_id" '.fallback[$machine_id].port' "$proxyDb" 2>/dev/null)"
  if [ -z "$port" ] || [ "$port" = "null" ]; then
    echo "Machine with machine id $machine_id is not accessible now" >&2
    exit 1
  fi
else
  usage
fi

exec ssh root@127.0.0.1 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p "$port" "$@"
