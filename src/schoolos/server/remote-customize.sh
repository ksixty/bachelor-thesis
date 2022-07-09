set -eo pipefail
shopt -s inherit_errexit

proxyDb="/var/lib/schoolos-proxy/proxy-db.json"

usage() {
  echo "$0 [ -s new_short_id ] [ -l new_long_id ] [ -S new_ssh_private ] [ -o path/to/new_ovas ] [ -t new_tag ] { -u machine_id | -c short_id }" >&2
  exit 1
}

new_short_id=""
new_long_id=""
new_ssh_private=""
new_ovas_path=""
new_tag=""

short_id=""
machine_id=""

while getopts "u:c:s:l:S:o:t:" arg; do
  case "$arg" in
    c)
      short_id="$OPTARG"
      ;;
    u)
      machine_id="$OPTARG"
      ;;
    s)
      new_short_id="$OPTARG"
      ;;
    l)
      new_long_id="$OPTARG"
      ;;
    S)
      new_ssh_private="$OPTARG"
      ;;
    o)
      new_ovas_path="$OPTARG"
      ;;
    t)
      new_tag="$OPTARG"
      ;;
    *)
      usage
      exit 1
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

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

ssh -fNMS "$tmpdir/sock" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p "$port" root@127.0.0.1
callSsh() {
  ssh -S "$tmpdir/sock" placeholder "$@"
}

callSsh -- mkdir -p /provision

if [ -n "$new_short_id" ]; then
  echo "$new_short_id" | callSsh -- sh -c "'cat > /provision/short-id'"
fi

if [ -n "$new_long_id" ]; then
  echo "$new_long_id" | callSsh -- sh -c "'cat > /provision/long-id'"
fi

if [ -n "$new_ssh_private" ]; then
  cat "$new_ssh_private" | callSsh -- sh -c "'cat > /provision/ssh-private; chmod 600 /provision/ssh-private'"
fi

if [ -n "$new_id_proxy" ]; then
  cat "$new_id_proxy" | callSsh -- sh -c "'mkdir -p /root/.ssh; cat > /root/.ssh/id_proxy; chmod 600 /root/.ssh/id_proxy'"
fi

if [ -n "$new_ovas_path" ]; then
  ssh -S "$tmpdir/sock" placeholder -- sh -c "'mkdir -p /home/user/ovas; chown 1000 100 /home/user{,/ovas}'"
  for ova in "$new_ovas_path/"*.ova; do
    fname="$(basename "$ova")"
    cat "$ova" | callSsh -- sh -c "'cat > \"/home/user/ovas/$fname\"; chown 1000 100 \"/home/user/ovas/$fname\"'"
  done
fi

if [ -n "$new_tag" ]; then
  echo "$new_tag" | callSsh -- sh -c "'cat > /etc/schoolos-tag'"
fi

callSsh -- systemctl restart schoolos-ssh-proxy || true
