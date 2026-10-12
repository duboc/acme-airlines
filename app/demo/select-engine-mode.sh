#!/bin/sh
# Prints the ENGINE_MODE for the hosted demo.
# Usage: select-engine-mode.sh <HOSTED_ENGINE_MODE: local|auto> <service env var name>...
#   local: always `local`. auto: `agent-engine` only if the Cloud Run service has BOTH
#   AGENT_ENGINE_CUSTOMER and AGENT_ENGINE_SALES (written by the engine deploy), else `local`,
#   so a missing or broken engine deploy never breaks the hosted demo.
policy=$1; shift
c=0; s=0
for n in $(printf '%s\n' "$@"); do
  [ "$n" = AGENT_ENGINE_CUSTOMER ] && c=1
  [ "$n" = AGENT_ENGINE_SALES ] && s=1
done
if [ "$policy" = auto ] && [ "$c$s" = 11 ]; then echo agent-engine; else echo local; fi
