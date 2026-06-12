# Nightly builds

Nightly builds publish the current `main` state without turning it into a release. They are intended for testing, early validation and reproducing issues between tagged releases.

## Docker

Docker nightlies are published to GHCR because container tags are a natural fit for a moving preview channel.

The nightly Docker workflow publishes these tags:

- `nightly`: moving pointer to the newest successful nightly build
- `nightly-YYYYMMDD`: date based build tag
- `nightly-<short-sha>`: commit based build tag without the date
- `nightly-YYYYMMDD-<short-sha>`: traceable build tag for debugging

Stable release tags are untouched. The cleanup step only runs after the image was built and pushed successfully, only considers package versions with nightly tags, and keeps the newest 14 nightly container versions. This keeps GHCR from accumulating old preview images while still leaving enough history for short-term rollback and issue reproduction. A failed nightly never prunes previously published nightlies.

## LXC

LXC nightlies are intentionally handled differently from Docker nightlies. GitHub releases should stay reserved for versioned releases, because LXC release assets are downloaded by users and referenced from release notes. Creating a release per night would leave stale release entries and assets behind.

For nightly LXC builds, the nightly LXC workflow dispatches the existing `lxc-template.yml` workflow. That keeps the release and nightly LXC templates on the same build path instead of duplicating the template logic.

The existing LXC workflow uploads these GitHub Actions artifacts:

- `lxc-amd64`
- `lxc-arm64`
- `app-bundle`

The nightly dispatcher triggers the template build, waits for it to finish, and only then deletes matching LXC artifacts older than 14 days. Pruning is skipped whenever the dispatched build fails (or cannot be located), so the last good nightly artifacts survive a prolonged build outage instead of expiring while no fresh build replaces them. No GitHub release is created for nightlies and no nightly release tag has to move.

## Why this split

Docker users expect a mutable channel like `nightly`; LXC users usually download a concrete template archive. Keeping Docker nightlies in GHCR and LXC nightlies as short-lived CI artifacts gives both formats the right lifecycle:

- normal tagged releases remain permanent and documented
- nightly builds are easy to try
- old preview artifacts are removed automatically or by a targeted cleanup step
- stable release assets and tags are not touched by nightly maintenance
