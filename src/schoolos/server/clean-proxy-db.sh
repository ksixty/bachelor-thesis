set -eo pipefail

proxyDb="$HOME/proxy-db.json"

exec 200>"$proxyDb.lock"
flock 200

if [ ! -e "$proxyDb" ]; then
  echo '{"fallback": {}, "clients": {}}' > "$proxyDb"
fi

updateProxyDb() {
  jq "$@" "$proxyDb" > "$proxyDb.tmp"
  mv -f "$proxyDb.tmp" "$proxyDb"
}

cutoff_date="$(date -u -Iseconds --date='5 minutes ago')"

while read pair; do
  values=($pair)
  machine_id="${values[0]}"
  modified="${values[1]}"

  if [[ "$cutoff_date" > "$modified" ]]; then
    updateProxyDb --arg machine_id "$machine_id" 'del(.fallback[$machine_id])'
  fi
done < <(jq -r '.fallback | keys[] as $k | "\($k) \(.[$k].modified)"' "$proxyDb")

while read pair; do
  values=($pair)
  short_id="${values[0]}"
  modified="${values[1]}"

  if [[ "$cutoff_date" > "$modified" ]]; then
    updateProxyDb --arg short_id "$short_id" 'del(.clients[$short_id])'
  fi
done < <(jq -r '.clients | keys[] as $k | "\($k) \(.[$k].modified)"' "$proxyDb")
