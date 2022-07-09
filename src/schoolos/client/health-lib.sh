green='\033[0;32m'
yellow='\033[1;33m'
red='\033[0;31m'
nc='\033[0m'

status() {
  message="$1"
  shift

  status="$1"
  shift

  if [ "$status" = "0" ]; then
    echo -e "${message}: ${green}ok${nc}"
  else
    echo -e "${message}: ${red}fail${nc}"
  fi
}

returnCode=0

outputTest() {
  message="$1"
  shift

  status="$1"
  shift

  if [ "$status" != "0" ]; then
    returnCode=1
  fi
  status "$message" "$status"
}

runTest() {
  message="$1"
  shift

  "$@" >/dev/null 2>&1
  outputTest "$message" "$?"
}
