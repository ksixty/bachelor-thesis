set -eo pipefail

exec 3<&0 </dev/null

short_id="$1"
if [ -z "$short_id" ]; then
  logger "Internal error: no short_id specified for proctor script"
  echo "Internal error" >&2
  exit 1
fi

args=($SSH_ORIGINAL_COMMAND)

if [ "${args[0]}" = "upload-screencast" ]; then
  original_file_name="${args[1]}"

  file_name="screencast-$(date +"%FT%T.%6N")"
  full_path="$short_id/$file_name"
  if [ -e "$full_path.mkv" ]; then
    suffix="1"
    while [ -e "$full_path.$suffix.mkv" ]; do
      suffix="$((suffix + 1))"
    done
    full_path="$full_path.$suffix"
  fi

  mkdir -p "$short_id"
  cleanup() {
    rm -f "$full_path.tmp"
  }
  trap cleanup EXIT TERM INT HUP PIPE
  cat <&3 > "$full_path.tmp"
  mv "$full_path.tmp" "$full_path.mkv"
  echo "$original_file_name" > "$full_path.name"
  logger "Accepted screencast from $machine_id with original name $original_file_name to $full_path"
else
  logger "Unknown handle-proctor command: $SSH_ORIGINAL_COMMAND."
  echo "Unknown command: $SSH_ORIGINAL_COMMAND. This will be reported." >&2
  exit 1
fi
