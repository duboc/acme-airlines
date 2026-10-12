#!/bin/sh
# Prints the ENGINE_MODE for the hosted demo: `agent-engine` only when the Cloud Run service
# already has BOTH AGENT_ENGINE_CUSTOMER and AGENT_ENGINE_SALES (written by the engine deploy),
# otherwise `local`, so a missing or broken engine deploy never breaks the hosted demo.
# Usage: select-engine-mode.sh <env var name>...   (one name per argument or line)
set -- $(printf '%s\n' "$@")
c=0; s=0
for n in "$@"; do
  [ "$n" = AGENT_ENGINE_CUSTOMER ] && c=1
  [ "$n" = AGENT_ENGINE_SALES ] && s=1
done
if [ "$c$s" = 11 ]; then echo agent-engine; else echo local; fi
