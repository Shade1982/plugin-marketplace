<!--
Thanks for contributing to the Gameyfin plugin marketplace!
Please read the "Submitting a plugin" section of the README before opening a pull request.
-->

## Plugin

- Plugin ID:
- Project / source code:
- Type of change: <!-- new plugin / new release / metadata update / removal -->

## Checklist

- [ ] The pull request only changes `plugins/<plugin-id>.json` (not `plugins.json`)
- [ ] `id` and `version` match `Plugin-Id` and `Plugin-Version` in the `MANIFEST.MF` of the JAR
- [ ] `requires` matches `Plugin-Requires` in the `MANIFEST.MF` of the JAR
- [ ] The JAR is **not** signed and is hosted at a permanent URL (e.g. a GitHub release asset) that will never change
- [ ] `sha512sum` is the SHA-512 checksum of the JAR
- [ ] I have tested the release with the Gameyfin version(s) given in `requires`
- [ ] The source code is publicly available and the plugin doesn't collect data without the user's consent
- [ ] I'm listed in `maintainers` (or I'm a maintainer of the plugin and listed there already)
