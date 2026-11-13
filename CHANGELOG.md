# Changelog

## 1.1.9

- Rebuild Linux AMD64 and ARM64 native payloads with google.golang.org/api v0.315.0 on the pinned Toolbox v1.17.0 and verified Go 1.27.4 baseline.
- Preserve the other selected dependency versions, connector controls and approved profiles; reject the superseded Google API module during package verification.


## 1.1.8

- Refresh Toolbox to v1.17.0 and the reviewed dependency lock; build both native targets in CI while preserving connector safety and profile restrictions.
