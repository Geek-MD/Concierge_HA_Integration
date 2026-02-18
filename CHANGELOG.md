# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Translated README.md from Spanish to English
- Added CHANGELOG.md to track project changes

## [0.1.0] - 2026-02-18

### Added
- Initial release of Concierge Services integration
- IMAP email account configuration through Home Assistant UI
- Real-time IMAP credential validation
- Secure credential storage using Home Assistant's storage system
- Multi-language support (English and Spanish)
- HACS compatibility for easy installation
- Configuration flow with user-friendly interface
- Support for major email providers (Gmail, Outlook, Yahoo)
- Basic integration structure and manifest

### Documentation
- Created comprehensive README with installation and configuration instructions
- Added MIT License
- Created HACS configuration file
- Added Spanish translations for the integration UI

### Technical Details
- Integration domain: `concierge_services`
- Configuration flow implementation using Home Assistant's config_flow
- Supports IMAP SSL/TLS connection on port 993
- IoT class: cloud_polling

[Unreleased]: https://github.com/Geek-MD/Concierge_Services/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Geek-MD/Concierge_Services/releases/tag/v0.1.0
