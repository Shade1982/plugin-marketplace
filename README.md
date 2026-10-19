# Gameyfin Plugin Marketplace

This repository hosts the index of the **official Gameyfin plugin marketplace**. Every Gameyfin instance knows it
out of the box and shows its plugins in *Administration → Plugins → Marketplace*.

The repository only contains **metadata**. The plugin JARs stay with their developers (e.g. as GitHub release assets),
and each release in the index pins the SHA-512 checksum of its JAR. Gameyfin verifies that checksum before it installs
anything.

- [Repository layout](#repository-layout)
- [Submitting a plugin (plugin developers)](#submitting-a-plugin-plugin-developers)
- [Manifest reference](#manifest-reference)
- [Reviewing submissions (marketplace maintainers)](#reviewing-submissions-marketplace-maintainers)
- [Hosting a custom marketplace](#hosting-a-custom-marketplace)
- [Testing locally](#testing-locally)

### What Gameyfin checks when installing a plugin

- The download must not exceed 512 MB, and its SHA-512 checksum must match `sha512sum`.
- `Plugin-Id` and `Plugin-Version` in the JAR's `MANIFEST.MF` must match the plugin ID and release version.
- `Plugin-Requires` must be compatible with the running Gameyfin version.
- Trust levels:
    - **Unsigned** JARs from a marketplace are classified as *third-party*. They are installed **disabled** and an
      administrator has to enable them explicitly.
    - JARs **signed** with a key other than the Gameyfin key are classified as *untrusted* and can't be enabled at all.
      This is why third-party plugins must be published unsigned.
    - Plugins signed by the Gameyfin team are *official* and are enabled automatically.
- If a plugin that is already installed gets reinstalled from a different source, it is disabled again unless it is
  official.

## Repository layout

| Path                                 | Description                                                                         |
|--------------------------------------|-------------------------------------------------------------------------------------|
| `plugins/<plugin-id>.json`           | One manifest per plugin. **This is the only thing contributors edit.**              |
| `plugins.json`                       | Generated index consumed by Gameyfin. Never edit it manually or in a PR.            |
| `schema/plugin-manifest.schema.json` | JSON schema for editor auto-completion. `scripts/validate.py` is authoritative.     |
| `examples/`                          | Example manifest.                                                                   |
| `scripts/marketplace.py`             | Shared validation and index logic (Python 3.10+, standard library only).            |
| `scripts/validate.py`                | Validates manifests, downloads and inspects releases.                               |
| `scripts/build_index.py`             | Generates `plugins.json`.                                                           |
| `.github/workflows/`                 | CI: PR validation, index publishing, weekly re-check.                               |

## Submitting a plugin (plugin developers)

### 1. Prepare the plugin JAR

The JAR's `META-INF/MANIFEST.MF` must contain at least:

```
Plugin-Id: com.example.gameyfin.awesome-metadata
Plugin-Version: 1.1.0
Plugin-Class: com.example.gameyfin.awesome.AwesomeMetadataPlugin
Plugin-Requires: >=2.5.1
Plugin-Name: Awesome Metadata
Plugin-Description: Fetches game metadata from the Awesome Games Database.
Plugin-Author: Jane Doe
Plugin-License: MIT
Plugin-Url: https://github.com/janedoe/gameyfin-awesome-metadata
```

- `Plugin-Id`: use a reverse domain name or a GitHub-based ID that you own, e.g. `io.github.janedoe.awesome-metadata`.
  IDs starting with `org.gameyfin.` are reserved for the Gameyfin team.
- `Plugin-Version`: must be a [semantic version](https://semver.org) and has to be increased for every release.
- `Plugin-Requires`: the Gameyfin versions the plugin works with, e.g. `>=2.5.1` or `>=2.5.1 & <3.0.0`. Gameyfin treats
  a plain version such as `2.5.1` as `>=2.5.1`. The marketplace requires this attribute to be set.
- **Do not sign the JAR** (see [What Gameyfin checks](#what-gameyfin-checks-when-installing-a-plugin)).

The plugins in the [Gameyfin repository](https://github.com/gameyfin/gameyfin/tree/main/plugins) are good examples of
how to structure and build a plugin.

### 2. Publish the JAR

Upload the JAR to a **permanent, immutable** HTTPS URL. A GitHub release asset is ideal:
`https://github.com/<you>/<repo>/releases/download/v1.1.0/awesome-metadata-1.1.0.jar`.

Never replace the file behind a published URL. The checksum would no longer match, installation would fail for
everyone, and the weekly check would report the plugin. Publish a new version instead.

Compute the checksum:

```shell
sha512sum awesome-metadata-1.1.0.jar        # Linux
shasum -a 512 awesome-metadata-1.1.0.jar    # macOS
(Get-FileHash -Algorithm SHA512 .\awesome-metadata-1.1.0.jar).Hash.ToLower()   # PowerShell
```

### 3. Open a pull request

1. Fork this repository.
2. Create `plugins/<plugin-id>.json`. Copy
   [`examples/com.example.gameyfin.awesome-metadata.json`](examples/com.example.gameyfin.awesome-metadata.json) and
   adjust it.
3. Optionally, run the validation locally: `python3 scripts/validate.py plugins/<plugin-id>.json`.
4. Open a pull request against `main` and fill in the checklist. The `Validate plugin manifests` check shows any
   problems in the job summary and as annotations on the changed file.

   Workflows of first-time contributors only run after a maintainer approves them.

### Publishing an update

Add a new entry to `releases`. Order doesn't matter, because the index lists the newest version first. Open a PR the
same way as for the initial submission.

- Already published releases are **immutable**: their `url` and `sha512sum` must not change. You can still fix
  `changelog`, `date`, or `requires`, but CI doesn't re-check them against the JAR.
- To yank a broken release, remove it from `releases`. Instances that already installed it keep it, but nobody can
  install it from the marketplace anymore.
- To remove the plugin from the marketplace, delete the manifest.

### Maintainers

`maintainers` lists the GitHub accounts that are responsible for the plugin. This field is not published in
`plugins.json`. CI warns when a PR is opened by someone who isn't a maintainer of the plugin, and reviewers then make
sure that the plugin's maintainers agree with the change.

## Manifest reference

See [`schema/plugin-manifest.schema.json`](schema/plugin-manifest.schema.json) and the
[example](examples/com.example.gameyfin.awesome-metadata.json). Unknown fields are rejected.

| Field              | Required | Description                                                                               |
|--------------------|:--------:|-------------------------------------------------------------------------------------------|
| `id`               |    ✔    | Plugin ID (`[A-Za-z0-9][A-Za-z0-9._-]{1,127}`). Must equal `Plugin-Id` and the file name. |
| `name`             |    ✔    | Display name (max. 100 characters).                                                       |
| `shortDescription` |          | Shown on the plugin card (max. 300 characters).                                           |
| `description`      |    ✔    | Full description, Markdown supported.                                                     |
| `provider`         |    ✔    | Author or organization.                                                                   |
| `license`          |    ✔    | License, preferably an [SPDX identifier](https://spdx.org/licenses/).                     |
| `projectUrl`       |    ✔    | Homepage or source code (https).                                                          |
| `logo`             |          | https URL of a PNG, JPEG, GIF, WebP or SVG image (max. 1 MB). Square images work best.    |
| `types`            |    ✔    | Implemented extension points: `GameMetadataProvider`, `DownloadProvider`, `GameyfinTask`. |
| `tags`             |          | Up to 10 lowercase tags (`[a-z0-9][a-z0-9-]{0,29}`).                                      |
| `maintainers`      |    ✔    | GitHub usernames (not published).                                                         |
| `releases`         |    ✔    | List of releases (see below).                                                             |

| Release field | Required | Description                                                                  |
|---------------|:--------:|------------------------------------------------------------------------------|
| `version`     |    ✔    | Semantic version, must equal `Plugin-Version`.                               |
| `date`        |    ✔    | Release date: `yyyy-MM-dd` or an ISO-8601 date-time.                         |
| `requires`    |    ✔    | Compatible Gameyfin versions, must equal `Plugin-Requires` (e.g. `>=2.5.1`). |
| `url`         |    ✔    | Permanent https download URL of the JAR (max. 512 MB).                       |
| `sha512sum`   |    ✔    | SHA-512 checksum of the JAR, 128 lowercase hex characters.                   |
| `changelog`   |          | Changes in this release, Markdown supported.                                 |

The generated `plugins.json` has the shape `{ "plugins": [ ... ] }`. It contains the fields above except `maintainers`
and `$schema`. Plugins are sorted by ID and releases newest first.

## Reviewing submissions (marketplace maintainers)

CI covers the mechanical checks. The review is about trust. Before approving a PR:

- [ ] The PR only touches `plugins/<plugin-id>.json`. Changes to anything else, such as `scripts/` or `.github/`, need
  extra scrutiny.
- [ ] The `Validate plugin manifests` check passed. Read its warnings: non-maintainer author, reserved ID, removed
  releases, changed maintainers.
- [ ] **New plugins:**
    - [ ] The source code is public and matches the plugin description.
    - [ ] The license allows distribution.
    - [ ] The submitter is plausibly the author.
    - [ ] The ID doesn't impersonate another project.
- [ ] **New releases:** the JAR was built from the public source. Ideally it was built by CI and attached to a tagged
  release in the plugin's repository.
- [ ] **Updates by non-maintainers:** one of the plugin's maintainers approves the change in the PR.
- [ ] Prefer squash merges, so `main` keeps one commit per change.

When a plugin turns out to be malicious, delete its manifest immediately. The index is updated within minutes, and
Gameyfin instances see the change within an hour because of the cache. Already installed copies are not removed
automatically, so announce the incident as well.

When the scheduled check opens an issue labeled `marketplace-check`, contact the plugin maintainers. If a release can't
be fixed, remove it.

## Hosting a custom marketplace

Anyone can host a marketplace. Administrators add it in *Administration → Plugins → Marketplace → Marketplaces*.
Gameyfin accepts:

| URL                                                   | Resolved index                                               |
|-------------------------------------------------------|--------------------------------------------------------------|
| `https://github.com/<owner>/<repo>`                   | `raw.githubusercontent.com/<owner>/<repo>/HEAD/plugins.json` |
| `https://github.com/<owner>/<repo>/tree/<ref>/<dir>`  | `<dir>/plugins.json` at `<ref>`                              |
| `https://github.com/<owner>/<repo>/blob/<ref>/<file>` | `<file>` at `<ref>`                                          |
| any URL ending in `.json`                             | used as-is                                                   |
| any other URL                                         | treated as a directory: `<url>/plugins.json`                 |

Additional rules:

- URLs without a scheme get `https://`.
- `http://` and `file:` URLs are only allowed when `gameyfin.plugins.marketplace.allow-insecure-urls=true`. Never
  enable this in production.
- URLs containing credentials are rejected.
- The index is limited to 5 MB.
- In a custom index, relative release and logo URLs are resolved against the index URL.
- `sha512sum` may also be `.sha512`, meaning the release URL with its extension replaced by `.sha512`, or the URL of a
  checksum file. This repository always requires the literal checksum.

The easiest way to run a custom marketplace is to fork this template and use the same scripts and workflows.

Other marketplace-related Gameyfin properties:

| Property                                          | Default |
|---------------------------------------------------|---------|
| `gameyfin.plugins.marketplace.cache-ttl`          | `1h`    |
| `gameyfin.plugins.marketplace.connect-timeout`    | `10s`   |
| `gameyfin.plugins.marketplace.request-timeout`    | `2m`    |

## Testing locally

Requirements: Python 3.10+ and git. No packages are needed.

```shell
# Structural checks of all manifests (no downloads)
python3 scripts/validate.py --all --offline

# Full check of one manifest (downloads and inspects every release)
python3 scripts/validate.py plugins/com.example.gameyfin.awesome-metadata.json

# Same checks as the PR workflow (only new/changed releases compared to main)
python3 scripts/validate.py --base-ref origin/main --pr-author <your-github-login>

# Check that plugins.json is up to date / regenerate it
python3 scripts/build_index.py --check
python3 scripts/build_index.py
```

To test a plugin end-to-end with a local Gameyfin instance before publishing it:

1. Serve the JAR locally, e.g. `python3 -m http.server 8000` in the directory that contains the JAR.
2. Write a manifest with `"url": "http://localhost:8000/<file>.jar"`, then validate it:
   `python3 scripts/validate.py --allow-insecure plugins/<id>.json`.
3. Generate a test index:
   `python3 scripts/build_index.py --allow-insecure --output /path/served/by/http.server/plugins.json`.
4. Start Gameyfin with `gameyfin.plugins.marketplace.allow-insecure-urls=true`, e.g. via the environment variable
   `GAMEYFIN_PLUGINS_MARKETPLACE_ALLOWINSECUREURLS=true`.
5. Add `http://localhost:8000/plugins.json` as a marketplace and install the plugin.

Don't commit the test manifest or the generated index.
