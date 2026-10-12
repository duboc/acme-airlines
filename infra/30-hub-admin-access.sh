#!/usr/bin/env bash
# Addendum A, Task 1: temporary hub admin access for the operator's CLI.
#
# The hub runs in IAP proxy-auth mode, so there is no non-interactive way for
# the scion CLI to act as the admin user. While enabled, this adds a
# federation trusted issuer to settings.yaml. The issuer accepts Google ID
# tokens minted by the gcloud CLI (aud = gcloud client ID) for users in
# duboc.altostrat.com. The tokens map to the existing admin user via the
# normal sign-in policy. IAP still guards the public URL; the CLI talks to
# http://localhost:8080 on the VM over IAP SSH.
#
# Usage: infra/30-hub-admin-access.sh enable|disable|status
# Disable as soon as admin work is done.
set -euo pipefail
# shellcheck source=infra/lib.sh
source "$(dirname "$0")/lib.sh"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

SETTINGS=/home/scion/.scion/settings.yaml
BEGIN="# BEGIN acme-admin-federation (infra/30-hub-admin-access.sh)"
END="# END acme-admin-federation"

case "${1:-status}" in
  enable)
    vm_ssh "
      set -euo pipefail
      if sudo grep -qF '${BEGIN}' ${SETTINGS}; then echo 'already enabled'; exit 0; fi
      sudo cp -a ${SETTINGS} ${SETTINGS}.bak-\$(date +%Y%m%d%H%M%S)
      # settings.yaml ends inside the top-level server: block, so a block
      # indented by two spaces and appended at EOF lands under server:.
      # Guarantee the marker starts on its own line even if the file lacks a
      # trailing newline (otherwise disable's range delete would eat that line).
      echo | sudo tee -a ${SETTINGS} >/dev/null
      sudo tee -a ${SETTINGS} >/dev/null <<'EOF'
  ${BEGIN}
  federation:
    enabled: true
    trusted_issuers:
      - issuer_url: \"https://accounts.google.com\"
        jwks_url: \"https://www.googleapis.com/oauth2/v3/certs\"
        issuer_type: \"user\"
        expected_audience: \"${GCLOUD_CLIENT_ID}\"
        allowed_domains: [\"${ORG_DOMAIN}\"]
  ${END}
EOF
      sudo systemctl restart scion-hub
      for i in \$(seq 1 90); do curl -sf http://localhost:8080/healthz >/dev/null && break; sleep 2; done
      if curl -sf http://localhost:8080/healthz >/dev/null; then echo 'enabled; hub healthy'; else echo 'ERROR: hub not healthy 180s after restart' >&2; exit 1; fi
    " ;;
  disable)
    vm_ssh "
      set -euo pipefail
      if ! sudo grep -qF '${BEGIN}' ${SETTINGS}; then echo 'already disabled'; exit 0; fi
      sudo sed -i '/${BEGIN//\//\\/}/,/${END//\//\\/}/d' ${SETTINGS}
      sudo systemctl restart scion-hub
      for i in \$(seq 1 90); do curl -sf http://localhost:8080/healthz >/dev/null && break; sleep 2; done
      if curl -sf http://localhost:8080/healthz >/dev/null; then echo 'disabled; hub healthy'; else echo 'ERROR: hub not healthy 180s after restart' >&2; exit 1; fi
    " ;;
  status)
    if vm_ssh "sudo grep -qF '${BEGIN}' ${SETTINGS}" 2>/dev/null; then echo enabled; else echo disabled; fi ;;
  *) echo "usage: $0 enable|disable|status" >&2; exit 2 ;;
esac
