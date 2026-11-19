# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | yes |
| older | no |

## Reporting a vulnerability

Please do not open a public issue for security problems. Use the repository's
**Security → Report a vulnerability** page (GitHub private advisories) and include:

- affected version and Windows/Python version,
- a description of the issue and impact,
- reproduction steps or a proof of concept,
- any relevant log excerpts (remove local paths and personal data).

You can expect an acknowledgement within a few days. Confirmed issues will be fixed in
the current 1.0.x line and credited in the release notes unless you ask otherwise.

## Scope notes

- The application downloads files over HTTPS from the source site and writes them to a
  folder chosen by the user.
- It stores no credentials and sends no telemetry.
- Local state is limited to `.rdownloader_state.cfg`, `.rdownloader_done.cfg` and the `cache/`
  folder next to the executable.
