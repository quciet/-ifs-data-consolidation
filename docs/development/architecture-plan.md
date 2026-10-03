# Incremental architecture work

Agreed direction: a reusable processing engine, consistent workflow interface,
and an optional replaceable model API interface. Users supply folder paths; a
managed user workspace is not required. Native delivery manifests remain the
authority for their operations. No real data migration is implied.

1. Portable installation and resources (implemented foundation): package maintained
   runbooks/templates and pinned lookup; configure paths; exercise an installed
   wheel outside the repository on synthetic data. Keep source-specific scripts
   in the checkout until their dependencies and paths are migrated explicitly.
2. Consistent workflow entry points and task records (next): define shared input,
   output, scope, status and resumption contracts. Reference existing native
   evidence instead of copying it into a competing tracking system. Distinguish
   implemented operations from tasks needing model/human interpretation.
3. Repository organization: move implementations/docs by responsibility, update
   references and packaging, preserve historical notebooks and evidence. Maintained
   resource files have one source location; distribution copies are build artifacts.
4. Optional AI API runner: provider adapters invoke the same bounded processing
   interface. Exact-value/provenance checks remain in Python. Keep credentials out
   of task records; define what local evidence may be sent before implementing calls.

Each increment uses isolated synthetic tests. Directory reorganization, model API
execution and processing of real inputs have not been implemented by milestone 1.
