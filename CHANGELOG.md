# Changelog

## 1.1.15 - 2026-11-26

- Update go-redis to v9.24.0 and keep Flow Steward's RESP reply limits when clearing cached read errors.
- Update Google Cloud Auth to v0.24.5 and the Google API client to v0.317.0.
- Pin the Gorilla WebSocket fix for GHSA-w67g-5rqw-f597 in both Linux runtime targets.

## 1.1.14 - 2026-11-24

- Update the pinned Toolbox source to v1.18.0 and refresh the reviewed Linux AMD64 and ARM64 dependency locks.
- Update Cloud SQL Connector, Spanner, LZ4 and the OpenTelemetry Auto SDK in the build graph.
- Update development PyYAML to 6.0.4.

## 1.1.13 - 2026-11-21

- Refresh the reviewed Toolbox Go dependency lock and rebuild both Linux runtime targets.

## 1.1.12

- Enforce Dataset size and selected-column limits before dispatch.
- Bound native reply size, collection entries, and nesting before parsing.
- Check every resolved endpoint against the administrator network policy.
- Stage only reviewed release files without following symlinks.

## 1.1.11

- Rebuild Linux AMD64 and ARM64 native payloads with grpc-gateway v2.34.0 and its required indirect modules on the pinned Toolbox v1.17.0 and verified Go 1.27.4 baseline.
- Preserve the existing connector controls, approved profiles and dependency security fixes.

## 1.1.10

### Added

- Added a Support tab with links for reporting a problem, asking a question or requesting a feature in this extension's GitHub repository, and what to include in a report.

## 1.1.9

- Rebuild Linux AMD64 and ARM64 native payloads with google.golang.org/api v0.315.0 on the pinned Toolbox v1.17.0 and verified Go 1.27.4 baseline.
- Preserve the other selected dependency versions, connector controls and approved profiles; reject the superseded Google API module during package verification.


## 1.1.8

- Refresh Toolbox to v1.17.0 and the reviewed dependency lock; build both native targets in CI while preserving connector safety and profile restrictions.
