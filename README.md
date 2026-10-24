# AetherFlow Blueprint

This repository is prepared to import the **AetherFlow Blueprint v1.2** package.

## Import the full package

Upload the file `aetherflow-blueprint-v1.2.zip` to the repository root using GitHub's **Add file → Upload files** page, then commit the upload to `main`.

The workflow at `.github/workflows/import-blueprint.yml` will validate the ZIP, extract the contents of its `aetherflow-blueprint/` directory into the repository root, remove the uploaded archive, and commit the imported source tree.

> **Current state:** the repository bootstrap and import workflow have been committed. The full blueprint source tree is not yet present; it will appear after the ZIP is uploaded and the workflow completes.

## Project notes

AetherFlow is a local-first video-processing orchestration blueprint covering transcription, FFmpeg operations, subtitle/text removal, agent coordination, and optional cloud execution. The documents describe the intended design; they do not by themselves establish that all runtime features have been implemented or production-tested.
