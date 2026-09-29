#!/bin/sh
# How long this Mac has been up, its load averages, and how many times this
# card has run since the plugin was installed.
#
# Its job is to exercise uDeck's plugin repository rather than to be useful:
# it is the first plugin in github.com/iillyyaa1997/udeck-plugins, and it
# proves the whole path from a repository to a card. The run counter is the
# trace that path leaves. It lives in UDECK_CACHE_DIR, which uDeck removes
# together with the plugin -- so a plugin removed and installed again starts
# counting from 1, and a counter that does not is a removal that left
# something behind.
#
# POSIX sh and nothing a stock macOS lacks: sysctl, date, sed, cat, mkdir, mv.
# It writes nothing but its counter, and nothing outside UDECK_CACHE_DIR --
# the plugin's own folder has to stay exactly what the repository holds, or
# uDeck calls it modified.

sysctl=/usr/sbin/sysctl

case "${UDECK_LANG:-en}" in
  ru)
    label_up='работает'
    label_load='нагрузка (1, 5, 15 мин)'
    label_runs='запусков с установки'
    uptime_long='%d д %d ч %d мин'
    uptime_hours='%d ч %d мин'
    uptime_minutes='%d мин'
    unknown='неизвестно'
    no_boottime='sysctl kern.boottime не ответил понятно'
    no_loadavg='sysctl vm.loadavg не ответил понятно'
    no_cache='UDECK_CACHE_DIR не задан, запуски не считаются'
    no_count='счётчик запусков не записался в UDECK_CACHE_DIR'
    ;;
  *)
    label_up='up'
    label_load='load (1, 5, 15 min)'
    label_runs='runs since install'
    uptime_long='%dd %dh %dm'
    uptime_hours='%dh %dm'
    uptime_minutes='%dm'
    unknown='unknown'
    no_boottime='sysctl kern.boottime gave nothing readable'
    no_loadavg='sysctl vm.loadavg gave nothing readable'
    no_cache='UDECK_CACHE_DIR is not set, so runs are not counted'
    no_count='the run counter could not be written to UDECK_CACHE_DIR'
    ;;
esac

state=ok
reasons=''

# A reason becomes a row of its own, and the card says "unknown" rather than
# "ok": "I could not find out" must not look like "everything is fine".
explain() {
  state=unknown
  reasons="$reasons,
    { \"text\": \"$1\" }"
  printf 'uptime: %s\n' "$1" >&2
}

# A whole number, or nothing. A leading zero is refused too: sh reads 010 as
# octal, and 08 is not a number to it at all.
whole() {
  case "$1" in
    ''|*[!0-9]*|0?*) ;;
    *) printf '%s' "$1" ;;
  esac
}

# kern.boottime reads "{ sec = 1759046400, usec = 123456 } Sun Sep 28 ...".
boot=$(whole "$("$sysctl" -n kern.boottime 2>/dev/null | sed -n 's/^{ sec = \([0-9][0-9]*\),.*$/\1/p')")
now=$(whole "$(date +%s)")
up_value=$unknown
up_state=unknown
if [ -z "$boot" ] || [ -z "$now" ] || [ "$now" -lt "$boot" ]; then
  explain "$no_boottime"
else
  seconds=$((now - boot))
  days=$((seconds / 86400))
  hours=$((seconds % 86400 / 3600))
  minutes=$((seconds % 3600 / 60))
  if [ "$days" -gt 0 ]; then
    # shellcheck disable=SC2059 # the format is one of the strings above
    up_value=$(printf "$uptime_long" "$days" "$hours" "$minutes")
  elif [ "$hours" -gt 0 ]; then
    # shellcheck disable=SC2059
    up_value=$(printf "$uptime_hours" "$hours" "$minutes")
  else
    # shellcheck disable=SC2059
    up_value=$(printf "$uptime_minutes" "$minutes")
  fi
  up_state=ok
fi

# vm.loadavg reads "{ 1.23 1.45 1.67 }".
load_value=$("$sysctl" -n vm.loadavg 2>/dev/null |
  sed -n 's/^{ *\([0-9][0-9.,]*\) \([0-9][0-9.,]*\) \([0-9][0-9.,]*\) *}$/\1 · \2 · \3/p')
load_state=ok
if [ -z "$load_value" ]; then
  load_value=$unknown
  load_state=unknown
  explain "$no_loadavg"
fi

# The counter. uDeck creates UDECK_CACHE_DIR before every run; mkdir -p is for
# a run by hand. The new count is written beside the old one and renamed over
# it, so a run stopped halfway leaves the previous count rather than half a
# file.
runs_value=$unknown
runs_state=unknown
if [ -z "${UDECK_CACHE_DIR:-}" ]; then
  explain "$no_cache"
else
  counter="$UDECK_CACHE_DIR/runs"
  runs=$(whole "$(cat "$counter" 2>/dev/null)")
  runs=$((${runs:-0} + 1))
  if mkdir -p "$UDECK_CACHE_DIR" 2>/dev/null &&
     printf '%s\n' "$runs" > "$counter.new.$$" 2>/dev/null &&
     mv -f "$counter.new.$$" "$counter" 2>/dev/null; then
    runs_value=$runs
    runs_state=ok
  else
    rm -f "$counter.new.$$" 2>/dev/null
    explain "$no_count"
  fi
fi

# One kv row; the value is tinted only when it could not be found out.
kv() {
  if [ "$3" = ok ]; then
    printf '{ "kv": ["%s", "%s"] }' "$1" "$2"
  else
    printf '{ "kv": ["%s", "%s", "%s"] }' "$1" "$2" "$3"
  fi
}

cat <<JSON
{
  "state": "$state",
  "rows": [
    $(kv "$label_up" "$up_value" "$up_state"),
    $(kv "$label_load" "$load_value" "$load_state"),
    $(kv "$label_runs" "$runs_value" "$runs_state")$reasons
  ],
  "ttl": 120
}
JSON
