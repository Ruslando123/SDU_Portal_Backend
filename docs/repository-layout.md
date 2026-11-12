# Repository layout and publishing workflow

The workspace is stored in two private GitHub repositories:

| Repository | Contents |
| --- | --- |
| [SDU_Portal_Backend](https://github.com/Ruslando123/SDU_Portal_Backend) | `backend/`, `infrastructure/`, shared `docs/`, root configuration examples and the frontend revision pointer |
| [SDU_Portal_Frontend](https://github.com/Ruslando123/SDU_Portal_Frontend) | Next.js Student Portal, Performance Lab, frontend tests, package lockfile and sanitized Phase 6 snapshot |

The backend repository keeps its existing directory layout. The frontend is a Git submodule at `frontend/`, so Compose/demo scripts, Gradle documentation checks and server-side report readers keep their existing relative paths. Shared documentation and API contracts are maintained in the backend repository. A backend commit pins one exact frontend commit; Git does not duplicate the frontend source into the backend repository.

## Get the project on another computer

Authenticate Git for both private repositories, then:

```sh
git clone --recurse-submodules https://github.com/Ruslando123/SDU_Portal_Backend.git SDU_Portal
cd SDU_Portal
```

For a checkout originally made without `--recurse-submodules`:

```sh
git submodule update --init --recursive
```

Follow the [root setup guide](../README.md) or [Windows local testing guide](windows-local-quickstart.md). Cloning does not start paid cloud resources. Supply fresh local secrets using the documented scripts. Runtime `.env` files, private keys, session artifacts, database exports, generated binaries, dependency caches and raw local benchmark directories are ignored. Source scripts, Gradle wrapper, dependency lockfiles, written benchmark reports and the sanitized frontend Phase 6 snapshot are tracked.

Cloud run artifacts remain operator-controlled local data, outside Git. A fresh checkout displays No Data / Not Tested for reports that have not been transferred through an approved sanitized export. Never copy session cookies or management credentials into the browser or commit them to either repository.

## Update the complete checkout

From the backend repository root:

```sh
git pull --ff-only
git submodule update --init --recursive
```

Commit or safely preserve local changes before updating. Do not use forced checkout/reset to discard another person's work.

## Develop and publish frontend changes

A submodule checkout can be detached at the pinned commit. Create a branch before editing:

```sh
git -C frontend switch -c codex/frontend-change
# Edit and run the appropriate frontend checks.
git -C frontend add <reviewed-files>
git -C frontend commit -m "Describe the frontend change"
git -C frontend push -u origin codex/frontend-change
```

After the frontend revision is pushed (and merged when using a pull request), update the backend's `frontend` pointer to that revision and commit it together with related backend/docs changes. Push the frontend revision before publishing a backend pointer to it. Avoid `git submodule update --remote` when reproducing a historical benchmark: use the exact pinned commit instead.

## Release evidence

The initial publication includes the optional SQL projector and corrected drain-first recovery from [the measured experiment](memory-rabbitmq-projector-optimization.md). That experiment did not qualify 2,500 VUs. Historical migrations and benchmark reports are preserved; publication does not change measured results or start another benchmark.
