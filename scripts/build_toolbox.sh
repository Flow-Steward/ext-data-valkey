#!/bin/sh
# Reviewed Toolbox v1.17.0, fixed module graph, official Go 1.27.4 and connector-only registrations.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BOOTSTRAP_IMAGE=golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0
TOOLBOX_COMMIT=45ed205cc8682ed066c82e0b65bcd0e8fc3becce
SOURCE=${TOOLBOX_SOURCE:-${FS_TOOLBOX_SOURCE_DIR:-}}
OWNED_SOURCE=
cleanup() { if [ -n "$OWNED_SOURCE" ]; then rm -rf -- "$OWNED_SOURCE"; fi; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
if [ -z "$SOURCE" ]; then
 OWNED_SOURCE=$(mktemp -d "${TMPDIR:-/tmp}/fs-toolbox-source.XXXXXX")
 git -C "$OWNED_SOURCE" init -q
 git -C "$OWNED_SOURCE" remote add origin https://github.com/googleapis/mcp-toolbox.git
 git -C "$OWNED_SOURCE" fetch --depth 1 origin "$TOOLBOX_COMMIT"
 git -C "$OWNED_SOURCE" checkout --detach FETCH_HEAD
 SOURCE=$OWNED_SOURCE
fi
SOURCE=$(CDPATH= cd -- "$SOURCE" && pwd)
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$TOOLBOX_COMMIT"
test -z "$(git -C "$SOURCE" status --porcelain --untracked-files=all --ignored)"
docker run --rm --name "fs-toolbox-valkey-$$" --label flow-steward.build=toolbox-v1.17.0 --cpus 2 --memory 4g \
 -v "$SOURCE:/upstream:ro" -v "$ROOT:/out" \
 -v flow-steward-db-batch-go-cache:/go/pkg/mod -v flow-steward-db-batch-build-cache:/root/.cache/go-build \
 "$BOOTSTRAP_IMAGE" sh /out/scripts/build_inside.sh
python3 "$ROOT/scripts/provenance.py"
python3 "$ROOT/scripts/native_build_info.py"
