# Changelog

## [Unreleased]
### Added/Changed
- MyProjector v0: board sync, drift detection, checklist, tiered policy
- Mechanical migration to mythings.testing: local SpyEngine replaced by shared ScriptedEngine; _attended_env autouse now wraps the promoted attended_env fixture; the projector-specific FakeGh (state-parsing REST double) and FakeProjects stay local.
