# Product scope and release gates

## Current customer and value

The current implementation targets an individual KiCad user preparing a prototype
or assembly order. Its concrete value is detecting inconsistent source files,
organizing evidence, and keeping review decisions and revision comparisons in one place.

Current capabilities include BOM/placement validation with a part-selection
worksheet, durable review notes, indexed project history, revision comparisons,
and a portable review package. The app looks up LCSC products and PDFs, supports
editable design intent with explanations of intentional choices, and produces
ordered change plans with validated excerpt citations. It provisionally selects
LCSC candidates, reads their datasheets, and exports a grouped BOM, native KiCad
CPL, and sourcing review worksheet.
The local app remains usable without any AI service. When configured and requested,
the selected AI provider reconsiders the structured evidence, context and selected
datasheet passages.

## What is still required before a paid release

1. **Validate against independent boards.** Build an anonymized regression corpus
   across analogue, RF, battery, USB and hierarchical designs. Record which known
   faults are caught and which expected patterns generate unnecessary reviews.
   Current real-board testing is concentrated on the smartwatch example.
2. **Support hierarchical designs and assembly variants.** Root-only schematics are
   explicitly partial today. Variant-specific population and split top/bottom CPL
   files need explicit selection and consistent identity through the data model.
3. **Verify selected parts.** The current public LCSC lookup needs monitoring for
   format changes and a supplier agreement/API for commercial scale. Compare package,
   pinout and relevant ratings before presenting a candidate as compatible. Matching
   a part number and validating a PDF quotation do not verify the electrical conclusion.
4. **Expand the sourced rule catalogue.** Each part requirement needs a manufacturer
   source, an exact supported identity/package, tests and a coverage statement.
   Generic LLM guesses must never become electrical errors.
5. **Finish installation and recovery.** Ship a signed desktop installer or a tested
   self-hosted package, add a KiCad version matrix, database migrations, backup/restore,
   cancellation and visible recovery for interrupted jobs.

## Additional requirements for a hosted service

The localhost server must not be exposed directly to the internet. A hosted product
needs authenticated accounts, project ownership checks on every operation, isolated
storage per customer, retention/deletion controls, and secret management.

Run parsing and KiCad in disposable workers with CPU, memory, disk and time limits.
Use a durable queue and job-specific result IDs; a failed worker must not affect
another project. Keep the current single-workspace controller out of that request path.
Add upload quotas, queue capacity limits, telemetry without design contents, and
backups with tested restoration. Decide the commercial plan before adding billing,
entitlements and AI spend limits.

## Claims this version can support

- Identifies discrepancies among supported KiCad, BOM and placement inputs.
- Shows which checks ran and which evidence is missing.
- Records a user's review decisions without changing the original findings.
- Compares supported revisions and flags lost check coverage.

It cannot support a claim that the board will work, that it meets a manufacturer's
process limits, or that its components are currently available and compatible.
