# Accepted local releases

`promote` copies a validated candidate pair into a new named folder with its reports
and provenance. It refuses overwrites and candidates changed after validation.
It does not switch config.local.json automatically. Use `configure` explicitly to
make an accepted release the next baseline. Releases are excluded from Git.
