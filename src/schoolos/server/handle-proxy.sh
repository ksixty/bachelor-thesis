set -eo pipefail

exec 3<&0 </dev/null

proxyDb="$HOME/proxy-db.json"
short_id="$1"
args=($SSH_ORIGINAL_COMMAND)

if [ "${args[0]}" = "get-info" ]; then
  if [ -z "$short_id" ]; then
    echo "get-info is not available in fallback mode" >&2
    exit 1
  fi

  long_id="$(jq -r --arg short_id "$short_id" '.[$short_id].longId' "$clientsDb")"

  echo "${short_id@A}"
  echo "${long_id@A}"
elif [ "${args[0]}" = "update" ]; then
  machine_id="${args[1]}"
  if [ -z "$machine_id" ]; then
    echo "machine id is not specified" >&2
    exit 1
  fi
  port="${args[2]}"
  if [ -z "$port" ]; then
    echo "port is not specified" >&2
    exit 1
  fi
  tag="${args[3]}"

  exec 200>"$proxyDb.lock"
  flock 200

  if [ ! -e "$proxyDb" ]; then
    echo '{"fallback": {}, "clients": {}}' > "$proxyDb"
  fi

  updateProxyDb() {
    jq --arg modified "$(date -u -Iseconds)" "$@" "$proxyDb" > "$proxyDb.tmp"
    mv -f "$proxyDb.tmp" "$proxyDb"
  }

  if [ -z "$short_id" ]; then
    updateProxyDb --arg machine_id "$machine_id" --argjson port "$port" --arg tag "$tag" '.fallback[$machine_id] += {modified: $modified, port: $port, tag: $tag}'
  else
    updateProxyDb --arg short_id "$short_id" --arg machine_id "$machine_id" --argjson port "$port" --arg tag "$tag" '.clients[$short_id] += {modified: $modified, machineId: $machine_id, port: $port, tag: $tag} | del(.fallback[$machine_id])'
  fi
else
  logger "Unknown handle-backup-proxy command: $SSH_ORIGINAL_COMMAND."
  echo "Unknown command: $SSH_ORIGINAL_COMMAND. This will be reported." >&2
  exit 1
fi
