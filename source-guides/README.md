# Source library index

For another installation, use the source-library location shown by `ifs-data paths`
or explicitly supplied by the user. Read that library's README.md and source guide.
The absolute links below describe this repository owner's library only; they are
not portable defaults. See [path configuration](../docs/user-guide/installation.md).

The authoritative library is **D:\IFs Source Library**. Maintain one folder per
source with `SOURCE_GUIDE.md` inside. This repository contains pointers only;
do not create or update duplicate guides here.

- [Library index and maintenance procedure](<D:/IFs Source Library/README.md>)
- [Template for a new source guide](<D:/IFs Source Library/TEMPLATE.md>)

| Source | Aliases | Authoritative guide |
|---|---|---|
| UNU-WIDER Government Revenue Dataset | ICTD, UNU-WIDER GRD, government revenue | [ICTD](<D:/IFs Source Library/ICTD/SOURCE_GUIDE.md>) |
| Climate Watch historical emissions | Climate Watch, ClimateWatch, WRI, CAIT | [ClimateWatch](<D:/IFs Source Library/ClimateWatch/SOURCE_GUIDE.md>) |

During preprocessing, find the source in the library, create its folder/guide if
absent, and update supported findings. Add new sources to both the library index
and this pointer index. Raw data, imports and run-specific evidence remain in the
user's update folder. Keep the guide snapshot used by each run with that evidence.

Shared workflow code, tests and executable recipes remain in this repository.
Library Recipes folders may link to those canonical files rather than duplicate
them. A source's Documentation folder holds supplied manuals and authorized wiki
snapshots with provenance. Online fetching still requires explicit instruction.

If the library is unavailable, report the missing path; do not silently create a
second maintained collection. Resolve library write permissions normally. Back up
the external library separately from this repository. No watcher or automatic
backup has been configured.
