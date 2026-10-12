#!/usr/bin/env bash
# Container smoke test: run the built image the way Cloud Run does (non-root, read-only rootfs,
# an unwritable HOME, no network data) and assert startup gets past import. Regression test for
# #191, where `uv run` failed on a missing $HOME cache dir and every collector execution exited 2.
# usage: smoke-image.sh <image> collector|dashboard
set -euo pipefail
image=$1 kind=$2
fixtures=$(cd "$(dirname "$0")/../fixtures" && pwd)  # outside the image build context; mounted
flags=(--rm --read-only --user 10001 -e HOME=/nonexistent -e OBSERVATORY_DATA=fake
  -e OBSERVATORY_FIXTURES=/fixtures -v "$fixtures:/fixtures:ro")

[ "$(docker run "${flags[@]}" --entrypoint id "$image" -u)" != 0 ] || { echo "FAIL: runs as root"; exit 1; }
if docker inspect -f '{{json .Config.Cmd}} {{json .Config.Entrypoint}}' "$image" | grep -Eq '"uv"'; then
  echo "FAIL: image runs uv at runtime"; exit 1
fi

case $kind in
  collector)
    out=$(docker run "${flags[@]}" "$image" 2>&1) || { echo "$out"; echo "FAIL: collector exited non-zero"; exit 1; }
    echo "$out"
    grep -q "wrote .* rows to agent_snapshots" <<<"$out" || { echo "FAIL: collector wrote no snapshot"; exit 1; }
    ;;
  dashboard)
    cid=$(docker run -d "${flags[@]}" -e PORT=8080 "$image")
    trap 'docker rm -f "$cid" >/dev/null' EXIT
    for _ in $(seq 20); do
      if docker exec "$cid" python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/collector-health')" 2>/dev/null; then
        echo "dashboard OK"; exit 0
      fi
      sleep 1
    done
    docker logs "$cid"; echo "FAIL: dashboard did not serve"; exit 1
    ;;
  *) echo "usage: $0 <image> collector|dashboard"; exit 2 ;;
esac
