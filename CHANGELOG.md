# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.1] - 2026-07-13

### Changed

- **Concierge addon common-expenses OCR now uses the addon's structured template response first** (`sensor.py`, `attribute_extractor.py`, `const.py`, `manifest.json`, `README.md`):

  When the Concierge OCR addon is available, the integration now requests the
  built-in `coe_administraciones` template from the addon before parsing Gastos
  Comunes / Agua Caliente PDFs. This allows the integration to consume the
  template-driven field extraction introduced in addon v0.3.x directly instead
  of always reprocessing raw OCR lines locally.

  **Behaviour:**

  - The integration first calls `POST /ocr/source` with `template_id=coe_administraciones`.
  - If the addon returns structured `sections` data, the integration maps that
    response directly into the existing Concierge attributes.
  - If the addon returns no usable structured values, the integration falls back
    to the previous raw OCR JSON path and, if needed, to the internal PDF
    extractor exactly as before.

## [1.6.0] - 2026-07-13

### Changed

- **Addon status check now uses `GET /status` (addon v0.3.1+) with `GET /health` as fallback** (`sensor.py`, `manifest.json`):

  The Concierge OCR addon v0.3.1 introduced a new `GET /status` endpoint that
  returns `{"status": "ok", "running": true, "version": "<version>"}`.  Starting
  with this integration release, the coordinator queries `GET /status` first on
  every update cycle when checking whether the addon is reachable and healthy.

  **Behaviour:**

  - If `GET /status` responds with HTTP 200 and `status == "ok"` and
    `running == true` → the sensor transitions to / stays in **`running`**, and
    the reported addon version is stored internally.
  - If `GET /status` fails for any reason (connection error, timeout, HTTP
    error, unexpected payload — e.g. addon older than v0.3.1 that does not
    expose the endpoint) → the integration falls back to the existing
    `GET /health` check exactly as before.

  All other status transitions (`unknown`, `unsupported`, `not_installed`,
  `installed`, `starting`) and the full notification logic are completely
  unchanged.

- **Addon version exposed as extra state attribute** (`sensor.py`):

  `sensor.concierge_services_addon_status` now exposes an `addon_version`
  extra state attribute when the addon replies via `GET /status`:

  | Attribute | Value |
  |---|---|
  | `addon_version` | Version string reported by the addon (e.g. `"0.3.1"`), or absent when the `/health` fallback was used or the addon is not running. |

## [1.5.2] - 2026-07-11

### Fixed

- **Hub sensors not visible on integration page** (`sensor.py`):

  `sensor.concierge_services_status` (connection status) and
  `sensor.concierge_services_addon_status` (addon lifecycle) were created as
  standalone entities with no `device_info`, so they did not appear on the
  integration configuration page (`/config/integrations/integration/concierge_ha_integration`)
  and were not shown in the Home Assistant summary.

  Both sensors are now linked to a **hub device** (identifiers:
  `{(DOMAIN, config_entry.entry_id)}`, manufacturer: "Concierge Services", model: "Hub")
  that represents the integration's main connection coordinator.  The hub device
  appears on the integration page alongside the per-service subentry devices,
  making the connection status and addon status sensors fully discoverable and
  visible in the HA summary.

## [1.5.1] - 2026-07-11

### Added

- **Addon status sensor** (`sensor.py`, `const.py`, `manifest.json`):

  A new diagnostic sensor `sensor.concierge_services_addon_status` now exposes
  the Concierge OCR addon lifecycle state on the integration update cycle
  (every 30 minutes by default; first check occurs ~5 minutes after HA boot), giving users a single
  entity they can monitor in dashboards, automations, or the HA history.

  **Possible states:**

  | State | Meaning |
  |---|---|
  | `unknown` | Supervisor data is not yet available (transient after HA boot). |
  | `unsupported` | Home Assistant is not running under Supervisor (plain Docker / venv). |
  | `not_installed` | The Concierge OCR addon is not present in Supervisor. |
  | `installed` | The addon is installed but currently stopped. |
  | `starting` | Supervisor reports the addon is starting up (health endpoint not yet ready). |
  | `running` | The addon is started and its `/health` endpoint reports `{"status": "ok"}`. |

  **Persistence & logging:**
  - State changes are recorded automatically by the HA recorder, providing a
    full history of addon lifecycle transitions.
  - Every state transition is logged at `INFO` level:
    `Concierge Services: addon status changed: 'X' → 'Y'`.

  **Technical details:**
  - Uses `SensorDeviceClass.ENUM` with a fixed `options` list so HA can
    validate and display the state correctly.
  - Entity category: `Diagnostic` (grouped under the integration diagnostics
    panel, not on the main device page).
  - The icon updates dynamically to reflect the current state.
  - Added `ADDON_STATUS_*` constants and `ADDON_STATUS_OPTIONS` list to
    `const.py`.

## [1.5.0] - 2026-07-10

### Fixed

- **False "addon not installed" notification caused by Supervisor full-slug format** (`sensor.py`, `manifest.json`):

  Home Assistant Supervisor identifies third-party repository add-ons with a
  *full slug* that prepends a repository identifier to the add-on's own slug —
  for example `geek_md_concierge_ocr` instead of the bare `concierge_ocr`.

  The addon-presence check was doing an exact comparison against the short slug
  `"concierge_ocr"`, so the add-on was never found in the Supervisor add-on
  list, causing the code to always return `not_installed`.  As a side-effect,
  the `get_addons_info` hostname lookup also failed (same key mismatch), so
  the health-check fell back to `http://localhost:8099`, which is unreachable
  in Home Assistant OS Docker networking without the correct hostname.

  **Changes:**
  - The add-on list search now accepts both an exact slug match
    (`"concierge_ocr"`) and a suffix match (`"*_concierge_ocr"`), covering
    every possible repository-prefix format Supervisor may assign.
  - The actual full slug returned by Supervisor is now stored in a local
    variable (`actual_slug`) and used for the `get_addons_info` lookup, so
    the Docker hostname is retrieved correctly and the health-check URL is
    accurate.

## [1.4.9] - 2026-07-10

### Fixed

- **Addon notification no longer reappears on every recursive check after manual dismissal** (`sensor.py`, `manifest.json`, `README.md`):

  The coordinator now tracks the last addon-notification reason and only creates
  a new persistent addon notice when the underlying addon state actually changes
  (for example, from *not installed* to *stopped* or *startup timeout*).
  This prevents repeated recreation of the same *"Addon de OCR no instalado"*
  notification during periodic recursive checks.

  Additionally, when Supervisor state is `unknown` (data still loading), any
  stale addon notice is now dismissed to avoid keeping outdated warnings on
  screen.

## [1.4.8] - 2026-07-10

### Changed

- **5-minute delay before the first addon check after HA starts** (`sensor.py`, `const.py`):

  After Home Assistant fully starts (or after the integration is loaded/reloaded),
  the addon-presence check is now held back for **5 minutes** before it runs for
  the first time.  This prevents false *"Addon de OCR no instalado"* notifications
  that can appear right after a reboot while Supervisor is still populating its
  internal addon list.

  - Replaced the immediate `async_refresh()` call that was triggered by
    `EVENT_HOMEASSISTANT_STARTED` with an `async_call_later` deferred task that
    fires after `ADDON_CHECK_DELAY_SECONDS` (300 s).
  - Added `_addon_check_not_before: datetime | None` to the coordinator so that
    any update cycle that happens to run within the delay window is also
    suppressed, giving consistent protection regardless of the update interval.
  - Added `ADDON_CHECK_DELAY_SECONDS = 300` to `const.py`.

## [1.4.7] - 2026-07-10

### Fixed

- **False "addon not installed" notification on HA 2026.4+** (`sensor.py`, `manifest.json`):

  Home Assistant 2026.4.0 changed two Supervisor helper functions in a way that
  broke the addon-presence detection:

  1. **`get_supervisor_info` now raises `HassioNotReadyError`** instead of
     returning `None` when its data cache hasn't been populated yet.  The
     integration was calling the function without a `try/except`, so the
     exception propagated uncaught and was silently swallowed by the
     coordinator, leaving whatever notification was previously on screen
     unchanged.

  2. **The `"addons"` key in `get_supervisor_info`'s result is now a
     backwards-compat shim** filled from `DATA_ADDONS_LIST` (a separate addon
     coordinator).  When that coordinator hasn't done its first refresh yet,
     the key is *absent* from the result dict.  The old code used
     `supervisor_info.get("addons", [])` which returned `[]` in this case —
     incorrectly interpreted as "no addons installed" — triggering the
     persistent "not installed" notification even though the addon was present.

  3. **`get_addons_info` also raises `HassioNotReadyError`** in the same
     situation.  The previous `(get_addons_info(self.hass) or {})` pattern did
     not handle exceptions, so this too could propagate.

  Fixes applied to `_get_supervisor_addon_status`:

  - All calls to `get_supervisor_info` and `get_addons_info` are now wrapped in
    `try/except Exception` so that any `HassioNotReadyError` (or other
    unexpected hassio error) returns `("unknown", None)`, which suppresses the
    notification rather than firing a false alarm.
  - `get_addons_list()` (introduced alongside the HA 2026.4 changes) is now
    tried as the *primary* source for the installed-addon list.  When its data
    isn't ready it raises `HassioNotReadyError`, which is caught and mapped to
    `"unknown"`.  This avoids the empty-list false-negative that came from the
    deprecated `supervisor_info["addons"]` shim.
  - Older HA versions that don't expose `get_addons_list` fall back to the
    `supervisor_info.get("addons")` key, but now treat a `None` result (missing
    key) as `"unknown"` rather than as an empty addon list.

## [1.4.6] - 2026-07-10

### Fixed

- **Unnecessary addon health check when HA has no Supervisor** (`sensor.py`):

  When Home Assistant runs without a Supervisor (e.g. Container or Core
  installations), addons cannot be installed, so there is no point checking
  the addon health endpoint or showing any addon-related notification.

  Previously the integration still attempted an HTTP request to
  `http://localhost:8099/health` on every update cycle, logged a failed
  connection, and only suppressed the notification afterwards.

  Now `_async_manage_addon_notification` returns early as soon as
  `supervisor_state == "unsupported"` is detected — before building the
  candidate URL list or running any health checks — and dismisses any
  leftover addon notification that may have been created by a previous
  configuration.

## [1.4.5] - 2026-07-10

### Fixed

- **False "addon not installed" notification during HA restart / startup**
  (`sensor.py`, `manifest.json`):

  The integration was raising the "Concierge OCR addon not installed"
  persistent notification immediately on HA boot, before Home Assistant had
  finished starting.  At that point the Supervisor addon list returned by
  `get_supervisor_info` is still being populated, so the addon appeared absent
  even when it is installed.

  Two root causes were addressed:

  1. **HA startup guard**: The coordinator now tracks a `_ha_started` flag
     (initialised to `True` when HA is already running, e.g. on a config-entry
     reload, or `False` during initial boot).  A one-time listener for
     `EVENT_HOMEASSISTANT_STARTED` sets the flag and immediately schedules an
     `async_refresh()` so the addon check runs as soon as HA is fully up —
     without waiting the full 30-minute polling interval.  All notification
     creation in `_async_manage_addon_notification` is suppressed while the flag
     is `False`.

  2. **`"unknown"` / `"unsupported"` state fallthrough**: When the Supervisor
     data was not yet available (`supervisor_state == "unknown"`) or HA is not
     running under Supervisor (`"unsupported"`), the code was falling through to
     the generic "Addon de OCR no disponible" notification instead of returning
     silently.  An explicit early return was added for both states.

## [1.4.4] - 2026-07-10

### Fixed

- **False "addon not installed" notification when Concierge OCR addon is installed**
  (`sensor.py`, `manifest.json`):

  The addon-presence check now uses `get_supervisor_info(hass)["addons"]` — the
  Supervisor's authoritative list of all installed addons — as its primary
  source instead of `get_addons_info(hass)` (`DATA_ADDONS_INFO`).  The
  previous approach stored per-addon detailed info fetched individually via the
  Supervisor API; when any of those individual fetches failed the entry was
  stored as `None`, which was incorrectly interpreted as "addon not installed"
  and triggered the persistent notification.  The new logic returns `"unknown"`
  (suppressing the notification) when Supervisor data is not yet available, and
  `"not_installed"` only when the addon is genuinely absent from the Supervisor
  list.  The detailed info from `get_addons_info` is still used — as a
  best-effort fallback — to obtain the addon's Docker hostname when constructing
  the API URL.

## [1.4.3] - 2026-07-09

### Fixed

- **Explicit Concierge addon lifecycle handling for install, stopped, starting and timeout states**
  (`sensor.py`, `const.py`, `manifest.json`, `README.md`):

  The integration now distinguishes between the Concierge OCR addon being not
  installed, installed but not running, still starting, and fully ready. The
  old fixed 180-second startup grace period was replaced with Supervisor-aware
  lifecycle handling plus a 5-minute startup timeout: notifications now appear
  immediately when the addon is absent or stopped, stay suppressed while the
  addon is still starting, and switch to a dedicated startup-problem notice if
  `/health` still does not become ready within 5 minutes.

## [1.4.2] - 2026-07-09

### Fixed

- **Startup grace period before showing "addon not installed" notification**
  (`sensor.py`, `const.py`, `manifest.json`):

  The persistent notification that warns about the Concierge OCR addon not
  being installed was being shown immediately on every HA start, even when the
  addon was installed and simply had not finished starting yet. A 180-second
  startup grace period is now observed before the notification is created, so
  that the addon has enough time to initialise before being considered absent.

## [1.4.1] - 2026-07-09

### Fixed

- **Supervisor-aware Concierge addon detection**
  (`sensor.py`, `const.py`, `manifest.json`, `README.md`):

  Addon detection now checks Home Assistant Supervisor metadata first, uses the
  addon's reported hostname when available, and no longer relies only on
  `http://localhost:8099/health` to decide whether the addon is installed and
  running. This prevents false persistent notifications on Supervisor-based
  installations.

## [1.4.0] - 2026-07-08

### Added

- **Optional integration with the Concierge OCR API addon for Gastos Comunes/Agua Caliente PDFs**
  (`sensor.py`, `attribute_extractor.py`, `const.py`, `README.md`):

  When the `concierge_ocr` addon is available at `http://localhost:8099`, the
  integration delegates PDF OCR to the addon endpoint (`/ocr/source`) and
  reuses the existing extraction pipeline by converting the addon response to
  the expected internal format.

### Changed

- **Persistent notification to guide OCR addon installation**
  (`sensor.py`, `const.py`, `README.md`):

  A persistent notification is now created when the addon is not available and
  is automatically dismissed when it is detected as operational, improving the
  diagnostic and configuration flow for Home Assistant users.

## [1.3.12] - 2026-05-28

### Fixed

- **OCR.space API key is no longer treated as a required configuration dependency**
  (`sensor.py`, `config_flow.py`, `README.md`):

  Removed the OCR-space "key not configured" repair warning/notification flow and
  deleted the OCR.space API key field from setup/options forms, so users are no
  longer prompted to configure OCR.space as a requirement.

## [1.3.11] - 2026-05-27

### Fixed

- **Gastos Comunes / Agua Caliente no longer stay `unknown` when Tier 1 is incomplete**
  (`attribute_extractor.py`, `sensor.py`, `config_flow.py`, `README.md`):

  Common-expenses extraction now keeps the Tier-1 (`pdfminer`) path as the
  first choice, but it falls back to OCR.space again when the PDF text layer is
  empty or too incomplete to populate the Gastos Comunes / Agua Caliente
  sensors. The OCR.space API key is also persisted again in the setup/options
  flow and passed into PDF extraction, so image-based building PDFs can recover
  the missing values instead of leaving all those sensors in `unknown`.

## [1.3.10] - 2026-05-27

### Added

- **Task execution entries in Home Assistant Logbook under a dedicated domain**
  (`__init__.py`, `sensor.py`, `const.py`, `task_logbook.py`):

  The integration now emits `logbook_entry` events for key operational tasks so
  troubleshooting does not depend only on developer logs. Entries are grouped
  under the dedicated Logbook domain `concierge_ha_tasks`, including setup,
  discovery scans, automatic polling cycles, force refresh outcomes, recalculate
  actions, and `set_value` manual overrides.

## [1.3.9] - 2026-05-27

### Changed

- **Gastos Comunes + Agua Caliente extraction reverted to Tier 1 only**
  (`attribute_extractor.py`, `sensor.py`, `config_flow.py`):

  Common-expenses parsing now reads only the PDF text layer (`pdfminer`) and no
  longer uses OCR Tier 2 (OCR.space) for these fields. Hot-water values are now
  extracted from Tier 1 patterns and existing fallback derivations.

- **`set_value` keeps manual override behavior without "learning" wording**
  (`__init__.py`, `sensor.py`, `services.yaml`, translations):

  The service remains available as `concierge_ha_integration.set_value`, but
  descriptions and labels now describe it as a manual override in memory.

### Removed

- **OCR Tier 2 runtime dependency for common-expenses pipeline**
  (`manifest.json`):

  Removed `pypdfium2` and `Pillow` from runtime requirements since the v1.3.9
  common-expenses/hot-water path no longer uses OCR Tier 2.

## [1.3.8] - 2026-05-26

### Fixed

- **OCR row grouping no longer collapses all lines into one giant row**
  (`attribute_extractor.py`):

  The row-grouping algorithm in `_build_gc_ocr_pages` previously accumulated
  row height with each merged line ("snowball" effect), causing the logo line
  height (≈113 px) to grow the threshold until all 62 subsequent lines fell
  into one row. The fix uses the last added line's own height as tolerance,
  so each new line is compared to its immediate predecessor only. The 65-line
  OCR result for a typical bill now produces ≈13 logical rows instead of 2.

- **Value-cell selection now uses Y-proximity instead of left-to-right order**
  (`attribute_extractor.py`):

  `_extract_regex_near_gc_anchor` previously iterated same-row candidates in
  X-order (leftmost first), which caused values in the left column to be
  chosen over the correct right-column amounts. Candidates are now sorted by
  absolute Y-distance to the anchor line, so the geometrically closest match
  is always tried first.

- **Gastos Comunes extraction uses "Concepto" as the primary anchor**
  (`attribute_extractor.py`):

  The Detalle section always occupies a single merged row. Using the first
  X-ordered line (PROVISION DE FONDOS) as the anchor pointed to the wrong
  amount. The extractor now retrieves the "Concepto" anchor from
  `anchor_matches` and falls back to the row's first line only if the anchor
  is absent.

- **Provisión de Fondos derived from subtotal when proximity extraction fails**
  (`attribute_extractor.py`):

  `$138.719` (Subtotal) is geometrically closer in Y to PROVISION DE FONDOS
  than `$6.606` is, so direct Y-proximity always extracts the wrong value.
  `_finalize_common_expenses_attrs` now re-derives `fondos_amount` as
  `subtotal_departamento − gastos_comunes_amount` whenever the extracted value
  is missing or inconsistent with the financial identity.

- **Hot-water cost regex no longer matches meter-reading decimal prefixes**
  (`attribute_extractor.py`):

  The pattern `[\d.]+,\d{2}` matched `594,99` inside the reading `594,996000`
  (which occurs earlier in the row text than the real unit cost `6.817,72`).
  A negative-lookahead `(?!\d)` prevents the pattern from matching mid-number,
  so `6.817,72` is now extracted correctly as the cost per m³.

- **"Aqua Caliente" OCR typo handled as accepted spelling**
  (`attribute_extractor.py`):

  OCR.space consistently reads the bill heading as "Aqua Caliente" instead of
  "Agua Caliente". The hot-water row lookup now tries both spellings so the
  section is always found regardless of which variant OCR returns.

## [1.3.7] - 2026-05-26

### Fixed

- **Gastos Comunes and Agua Caliente extraction now uses OCR Tier 2 only**
  (`attribute_extractor.py`):

  The common-expenses PDF pipeline no longer mixes `pdfminer` Tier 1 values
  with OCR-derived values. All Gastos Comunes and Agua Caliente sensor fields
  now come from OCR.space Tier 2 data, preventing stale or mismatched Tier 1
  text-layer values from leaving sensors as `unknown`.

- **OCR JSON snapshots are now stored on every OCR run with explicit logging**
  (`attribute_extractor.py`, `README.md`):

  The OCR.space raw JSON payload is now written for every OCR attempt, including
  partial runs with zero parsed results, and the integration logs where the
  snapshot was stored. This makes OCR troubleshooting reproducible from the
  saved JSON alone.

- **Common-expenses OCR JSON mapping now matches by structure/proximity**
  (`attribute_extractor.py`, `README.md`):

  Instead of depending on the raw OCR JSON line order, the extractor now sorts
  overlay lines by visual position, groups them into rows, and resolves values
  through semantic label matching plus nearby-row proximity. This improves
  extraction stability when OCR returns complete data in a different order than
  the markdown template.

## [1.3.6] - 2026-05-26

### Fixed

- **Gastos Comunes and Agua Caliente sensors no longer show "unknown" when
  partial OCR extraction occurs** (`attribute_extractor.py`):

  The OCR JSON "monto a pagar" positional extraction previously required all 8
  expected amounts to be present; when OCR captured fewer values (due to layout
  drift or garbled `$` signs), no amounts were mapped at all.  Now, partial
  results (≥ 3 amounts) are assigned by position, so at least the first
  available amounts (gastos comunes, fondos, subtotal) populate their sensors.

- **`gc_total` computation uses proper None-checks instead of truthiness**
  (`attribute_extractor.py`, `sensor.py`):

  Previously `gc_total = subtotal_departamento + cargo_fijo` was only computed
  when *both* values were truthy (non-zero).  A legitimate zero `cargo_fijo`
  or a missing component caused the Total sensor to remain "unknown".  Now
  explicit `is not None` checks are used, and multiple fallback tiers ensure
  `gc_total` is always populated when any meaningful data exists:
  1. `subtotal_departamento + cargo_fijo` (both present)
  2. `subtotal_departamento` alone (cargo_fijo missing)
  3. `total_amount − subtotal_consumo` (neither component found)
  4. `total_amount` as last resort

- **`gastos_comunes_amount` derivation fallback added**
  (`attribute_extractor.py`):

  When direct extraction of the GC apartment charge fails (OCR garbling, regex
  miss), it is now derived from `subtotal_departamento − fondos_amount` so the
  "Bill" sensor is no longer stuck as "unknown".

- **`hot_water_amount` derivation relaxed for missing `cargo_fijo`**
  (`attribute_extractor.py`):

  The `subtotal_consumo` (and by extension `hot_water_amount`) back-calculation
  previously required all three components (total, subtotal_depto, cargo_fijo).
  A new relaxed branch handles the case where `cargo_fijo` is unavailable by
  computing `total_amount − subtotal_departamento`, ensuring the Agua Caliente
  sensor shows a value even without full extraction.

## [1.3.5] - 2026-05-26

### Fixed

- **Force Refresh now keeps processing even with malformed/missing email Date**
  (`sensor.py`):

  Matching emails are now fully processed (email attributes + PDF extraction)
  even when the `Date` header cannot be parsed. This prevents force-refresh from
  aborting with `last_updated = None`, which previously left Gastos Comunes and
  Agua Caliente sensors stuck as `unknown` in these cases.

- **Template OCR JSON mapping now runs even when OCR `ParsedText` is empty**
  (`attribute_extractor.py`):

  For common-expenses PDFs, template-guided extraction from OCR.space overlay
  lines is now executed whenever raw OCR JSON results exist, regardless of
  whether concatenated OCR plain text is empty. This ensures OCR refreshes can
  still populate Gastos Comunes and Agua Caliente sensor fields and keep JSON-
  driven mapping effective on layout variations.

## [1.3.4] - 2026-05-26

### Changed

- **OCR JSON snapshots moved to dedicated `json/` directory**
  (`attribute_extractor.py`, `sensor.py`, `const.py`):

  OCR.space raw JSON responses are now stored under
  `config/concierge_ha_integration/json/` (a sibling directory of `pdfs/`)
  instead of the previous `config/concierge_ha_integration/pdfs/ocrspace_json/`
  subdirectory.  The filename now matches the source PDF stem exactly (e.g.
  `gastos_comunes_2026-04_45313.json`), so each OCR run overwrites the previous
  snapshot for the same billing document.  Up to 5 JSON files are retained; the
  oldest is removed when the limit is exceeded.  A `JSON_SUBDIR` constant is
  added to `const.py` and the `json_dir` parameter is threaded through the
  entire extraction call chain so the coordinator can supply the correct path.

## [1.3.3] - 2026-05-25

### Added

- **Manual GitHub issue guidance for significant OCR/template mismatch**
  (`attribute_extractor.py`, `sensor.py`, `README.md`):

  The common-expenses OCR extractor now evaluates template-anchor coverage and
  flags significant mismatches (missing anchors / anchors without extracted
  values), and also detects unexpected OCR JSON structural lines not represented
  in the markdown template. Known optional OCR-only content stays excluded from
  this trigger (`Paga tu Gasto Común en línea` block and the phone value under
  `Fono`). When detected, the integration shows a persistent Home Assistant
  notification with a direct link to the repository issues page and a ready-to-
  copy markdown report body so users can create the issue manually.

## [1.3.2] - 2026-05-25

### Added

- **OCR.space raw JSON snapshot retention (last 5 files)**
  (`attribute_extractor.py`):

  Every OCR.space analysis now stores a raw JSON snapshot under
  `config/concierge_ha_integration/pdfs/ocrspace_json/`.
  The integration keeps only the 5 most recent JSON files and deletes older
  snapshots automatically.

### Changed

- **PDF cache retention now enforces latest 5 files globally**
  (`pdf_downloader.py`, `sensor.py`, `const.py`):

  In addition to age-based cleanup, the PDF cache now keeps only the 5 newest
  PDF files in `config/concierge_ha_integration/pdfs/` across all services.
  Older PDF files are removed automatically during refresh cycles and after
  each successful/confirmed download.

- **Common-expenses markdown template cleanup**
  (`services_templates/common_expenses/edificio_jose_miguel.md`):

  Removed the optional "Paga tu Gasto Común en línea" / client-code header from
  the template and replaced RUT/phone values with neutral placeholders so that
  non-critical building-specific metadata is not part of the reference sample.

## [1.3.1] - 2026-05-25

### Changed

- **Gastos Comunes markdown template generalized with placeholders**
  (`services_templates/common_expenses/edificio_jose_miguel.md`):

  Replaced building-specific sample values with generic placeholders
  (`dd-mm-aaaa`, `$ 0.000.000`, `0,00000`, `0,00 %`, generic owner/property
  fields) while preserving anchor labels and table structure used by the
  template-guided OCR mapping introduced in v1.3.0.

  This release does not change extraction logic; it only updates the template
  reference content to be reusable across buildings.

## [1.3.0] - 2026-05-24

### Changed

- **Gastos Comunes OCR extraction now uses markdown-template anchors**
  (`attribute_extractor.py`, `services_templates/common_expenses/edificio_jose_miguel.md`):

  The common-expenses extractor now reads OCR.space `ParsedResults` overlay lines
  and maps values using the structure defined in the markdown template for
  `edificio_jose_miguel`. The template is used as a structural reference (field
  anchors), not as a source of fixed values.

  This improves parsing stability for:
  `gastos_comunes_amount`, `fondos_amount`, `subtotal_departamento`,
  `cargo_fijo`, `subtotal_recargos`, `total_amount`,
  `hot_water_reading_prev`, `hot_water_reading_curr`,
  `hot_water_consumption`, `hot_water_cost_per_m3`, `hot_water_amount`,
  and last-payment fields.

  Scope is intentionally limited to **common_expenses** for this release.

## [1.2.8] - 2026-05-07

### Changed

- **`set_value` service — in-memory override only** (`sensor.py`, `__init__.py`):

  The `set_value` service no longer persists overrides to a JSON file.  It
  now simply overwrites the attribute value in the coordinator's in-memory
  data and immediately triggers a recomputation of formula-derived sensors
  (e.g. `subtotal`, `total_amount`).  The override is lost when HA restarts
  or when a *Force Refresh* replaces the coordinator data with fresh PDF
  values.

  Removed: `_load_learning_data`, `_save_learning_data`,
  `_set_learning_override_sync`, `_apply_learning_overrides` methods and the
  `learning.json` file-path constant.

### Fixed

- **Water PDF extractor — column-serialised NO PUNTA / PUNTA table**
  (`attribute_extractor.py`):

  The Aguas Andinas PDF uses a column-serialised table layout (pdfminer reads
  all row labels first, then m³ sub-values, then CLP amounts — each group on
  its own lines).  The previous `_WATER_AA_PDF_WATER_NO_PUNTA_RE` and related
  patterns expected the m³ and CLP values on the *same* line as the row label,
  so they never matched.  As a result the billing breakdown was not extracted
  and Recalculate had no source data to work with.

  A new `_WATER_AA_PDF_BILLING_TABLE_NOPUNTA_RE` pattern handles this format
  and correctly captures:

  | Attribute | Example value |
  |---|---|
  | `water_consumption_non_peak_m3` | 9.69 |
  | `water_consumption_non_peak` | 5803 |
  | `water_consumption_peak_m3` | 1.94 |
  | `water_consumption_peak` | 1150 |
  | `water_consumption` | 6953 (derived: non_peak + peak) |
  | `wastewater_recolection` | 5267 |
  | `wastewater_treatment` | 3564 |
  | `other_charges` | −8 |
  | `subtotal` | 16698 (derived) |
  | `total_amount` | 16690 (derived) |

  The existing line-by-line fallback patterns are retained for alternative PDF
  layouts where label, m³ and CLP appear on the same row.

### Added

- **Four new water billing sensors** replacing the removed combined sensor:

  | Sensor suffix | Attribute | Unit | Description |
  |---|---|---|---|
  | `water_non_peak_m3` | `water_consumption_non_peak_m3` | m³ | No-punta water consumed |
  | `water_non_peak_charge` | `water_consumption_non_peak` | $ | No-punta potable-water charge |
  | `water_peak_m3` | `water_consumption_peak_m3` | m³ | Punta water consumed |
  | `water_peak_charge` | `water_consumption_peak` | $ | Punta potable-water charge |

### Removed

- **Two redundant water sensors eliminated** (migration v1.6):
  - `sensor.concierge_{id}_water_consumption` (`water_consumption_charge`):
    combined potable-water charge ($) superseded by the individual
    `water_non_peak_charge` and `water_peak_charge` sensors.
  - `sensor.concierge_{id}_cost_per_unit` (`water_cost_per_unit`):
    combined cost-per-m³ ($/m³) superseded by the more granular
    `water_cost_per_unit_non_peak` and `water_cost_per_unit_peak` sensors.

  A config-entry migration (v1.6) removes both entities from the entity
  registry on first load.  `config_flow.MINOR_VERSION` is updated to 6.

## [1.2.7] - 2026-05-07

### Fixed

- **Water PDF extractor — new Aguas Andinas line-by-line layout**
  (`attribute_extractor.py`):

  The water billing table now supports a layout where potable-water consumption
  is split into two separate rows:

  - `Consumo Agua Potable No Punta`
  - `Consumo Agua Potable Punta`

  `water_consumption` is now computed as the sum of those two row amounts when
  this layout is detected, and the split-row consumptions are also captured so
  the following cost entities can be derived as `float` values with 2 decimals:

  - `cost_per_unit_non_peak = amount_non_peak / consumption_non_peak`
  - `cost_per_unit_peak = amount_peak / consumption_peak`

  The extractor also supports row-based parsing for:

  - `Recolección Aguas Servidas`
  - `Tratamiento Aguas Servidas`
  - `Descuento Ley Redondeo`

  For this layout, `other_charges` now follows the bill's
  `Descuento Ley Redondeo` value (including `Interés Deuda` when present, for
  backward compatibility with older formats).

- **Water service — updated derived-entity split**
  (`sensor.py`, `attribute_extractor.py`):

  The water recomputation graph now treats the PDF row amounts as the source of
  truth and keeps only cost entities as decimal sensors:

  - Cost entities remain `float` with 2 decimals.
  - All remaining billing amounts (`fixed_charge`, `water_consumption`,
    `wastewater_recolection`, `wastewater_treatment`, `subtotal`,
    `other_charges`, `total_amount`) remain integers.

  `Total Venta` continues to be ignored.

## [1.2.6] - 2026-04-29

### Changed

- **Water service — formula-derived sensors** (`sensor.py`,
  `attribute_extractor.py`):

  Five water sensors are now **computed from other sensor values** instead of
  being extracted directly from the PDF tariff table.  They are calculated at
  extraction time and also recomputed automatically whenever a constituent
  value is updated via the `set_value` service or the *Recalculate* button:

  | Sensor | Formula |
  |---|---|
  | `sensor.concierge_{id}_cost_per_unit` *(new)* | `water_consumption / consumption` (float, 2 decimals) |
  | `sensor.concierge_{id}_cubic_meter_collection` | `wastewater_recolection / consumption` (float, 2 decimals) |
  | `sensor.concierge_{id}_cubic_meter_treatment` | `wastewater_treatment / consumption` (float, 2 decimals) |
  | `sensor.concierge_{id}_subtotal` | `water_consumption + wastewater_recolection + wastewater_treatment + fixed_charge` |
  | `sensor.concierge_{id}_total_amount` | `subtotal + other_charges` |

  The new `_recompute_water_derived_attrs` coordinator method mirrors the
  existing `_recompute_gc_derived_attrs` pattern: attributes explicitly
  overridden by the user (confidence == `CONF_SCORE_OVERRIDE`) are never
  overwritten.  All formula results carry confidence score `CONF_SCORE_DERIVED`
  (60).

  Both `async_set_learning_override` and `async_recompute_derived` are now
  service-type-aware and dispatch to the correct recompute helper.

### Removed

- **Three water sensors eliminated**:
  - `sensor.concierge_{id}_cost_per_unit_peak` (`cubic_meter_peak_water_cost`)
  - `sensor.concierge_{id}_cost_per_unit_non_peak` (`cubic_meter_non_peak_water_cost`)
  - `sensor.concierge_{id}_cubic_meter_overconsumption` (`cubic_meter_overconsumption`)

  These three sensors, along with their associated PDF tariff-rate regex
  patterns and cross-verification logic, have been removed.  The effective
  per-m³ costs are now derived directly from the billed amounts.

## [1.2.5] - 2026-04-28

### Added

- **`Recalculate` button per service device** (`button.py`, `sensor.py`,
  `__init__.py`, `strings.json`, `services.yaml`):

  Each service device now exposes a second **Configuration** button entity —
  `button.concierge_{id}_recalculate` — alongside the existing *Force Refresh*
  button.

  Pressing *Recalculate* calls the new
  `ConciergeServicesCoordinator.async_recompute_derived()` method, which:

  1. Reads the attribute values already stored in the coordinator for that
     service device.
  2. Re-runs all alias syncs and arithmetic formulas (e.g.
     `gc_total = subtotal_departamento + cargo_fijo`).
  3. Logs every changed value at `INFO` level (unchanged recomputation at
     `DEBUG`).
  4. Pushes the updated state to all listening entities via
     `async_set_updated_data`.

  Unlike *Force Refresh*, this button **does not** open an IMAP connection or
  download any PDF — it only recomputes derived values from data already in
  memory.  Typical use case: after a manual `set_value` learning override, press
  *Recalculate* to immediately propagate the corrected input into all formula
  sensors without waiting for the next polling cycle or triggering a full
  refresh.

  The same logic is also exposed as a new HA service
  `concierge_ha_integration.recalculate` (device-picker selector, same
  interface as `force_refresh`).

- **`force_refresh` now delegates recomputation to `async_recompute_derived`**
  (`sensor.py`):

  The inline `_recompute_gc_derived_attrs` call that was embedded in
  `async_refresh_service` has been replaced with
  `await self.async_recompute_derived(subentry_id)` as the **final step**,
  after the fresh email/PDF data has already been pushed to the coordinator.
  This keeps the recomputation logic in one place (the new public method) and
  means *Force Refresh* always ends with an explicit *Recalculate* pass.

## [1.2.4] - 2026-04-28

### Fixed

- **Force-refresh flow — PDF cache busting** (`button.py`, `sensor.py`,
  `pdf_downloader.py`):

  Pressing *Forzar refresco* now **deletes every cached PDF** for the target
  service device before rescanning the mailbox.  Previously the downloader
  would reuse the on-disk file if it already existed for that billing period,
  so a corrupted or stale PDF would never be replaced.  The new sequence is:

  1. Resolve the normalised `service_id` slug for the subentry.
  2. Delete all `{service_id}_*.pdf` files from the PDF cache directory and
     log each deletion at `INFO` level.
  3. Scan the mailbox and download a fresh PDF from the matching email.
  4. Only replace the coordinator data (and therefore the sensor states) when
     the scan actually found a matching email (`last_updated` is not `None`).
     If no email is found the existing sensor values are preserved and a
     `WARNING` is emitted.
  5. **Recompute all formula-derived attributes** (e.g.
     `gc_total = subtotal_departamento + cargo_fijo`) from the freshly
     extracted values.  This is the final step before pushing the new state
     to HA, ensuring that formula sensors always reflect the latest extracted
     and override-applied data.

  All steps emit structured `INFO`-level log entries so the full refresh
  lifecycle can be traced in the HA log.

- **`sensor.concierge_common_expenses_total` showing "Unknown"**
  (`attribute_extractor.py`):

  The `gc_total` formula (`subtotal_departamento + cargo_fijo`) now also
  falls back to the public alias keys (`subtotal`, `fixed_charge`) when the
  canonical extraction keys are absent.  This prevents the *Total* sensor
  from being "Unknown" when the *Subtotal* and *Fixed Charge* breakdown
  sensors already show correct values.

- **Force-refresh does not overwrite existing sensor data on failure**
  (`sensor.py`):

  If the force-refresh IMAP scan fails or finds no matching email, the method
  now returns early without calling `async_set_updated_data`, preserving the
  sensor values that were already visible in HA.  Previously a failed scan
  would silently replace all service attributes with an empty dict, making
  every sensor for that device go "Unknown".

## [1.2.2] - 2026-04-28

### Added

- **Structured email-processing logs** (`sensor.py`):

  Every time the coordinator scans the mailbox, it now emits structured log
  entries so you can trace exactly what happened with each email:

  - **`DEBUG`** — one line per email evaluated, showing `from` and `subject`
    before any matching is attempted:
    ```
    Concierge Services [Gas]: evaluating email — from='...', subject='...'
    ```
  - **`DEBUG`** — when an email does *not* match a service:
    ```
    Concierge Services [Gas]: email did not match — from='...', subject='...'
    ```
  - **`INFO`** — when an email **matches** a service, including which of the
    five detection strategies triggered the match (`sender-domain`,
    `service-name-keywords`, `service-id-pattern`, `sample-subject-keywords`,
    or `service-type-pattern-fallback`) and the email's `from`, `subject`, and
    `date` fields:
    ```
    Concierge Services [Gas]: email matched via strategy 'sender-domain' — from='...', subject='...', date='...'
    ```
  - **`INFO`** — attributes extracted from the email body (key=value pairs):
    ```
    Concierge Services [Gas]: attributes extracted from email body — total_amount=12345, ...
    ```
  - **`INFO`** — PDF attachment found and being processed:
    ```
    Concierge Services [Common Expenses]: PDF found at '/config/...pdf' — extracting additional attributes
    ```
  - **`INFO`** — attributes extracted from the PDF:
    ```
    Concierge Services [Common Expenses]: attributes extracted from PDF — consumption=3.5, ...
    ```
  - **`INFO`** — when `last_updated` is overridden with the PDF's emission date:
    ```
    Concierge Services [Common Expenses]: last_updated overridden with PDF emission date '15-04-2026'
    ```
  - **`DEBUG`** — when a matching email has no PDF attachment.
  - **`WARNING`** — when no matching email is found (pre-existing, unchanged).

  `_matches_service` now returns the matching strategy name (`str`) instead of
  `bool`; `None` is returned when no strategy fires.  This is a purely internal
  refactor — external behaviour is unchanged.

## [1.2.1] - 2026-04-28

### Fixed

- **`_matches_service` fails to recognise forwarded billing emails** (`sensor.py`):

  When an email is forwarded to the monitored mailbox through a generic webmail
  provider such as Gmail (e.g. `From: edison.montes@gmail.com`), the existing
  four matching strategies all fail:

  1. **Sender-domain check** — the domain `gmail` is in `_GENERIC_WEBMAIL_DOMAINS`
     and is therefore deliberately skipped.
  2. **Service-name keywords** — the service name is stored in English
     (e.g. `"Common Expenses"`) while the email body is in Spanish, so words
     like `"common"` or `"expenses"` are never found.
  3. **Service-ID pattern** — the slug `common_expenses` is also English and
     produces no match against Spanish content such as `"Gastos Comunes"`.
  4. **Sample-subject keywords** — this strategy only succeeds when the
     `sample_subject` stored at detection time contains language-specific
     keywords that are also present in the new email.  If the stored sample
     was itself a generic forward, this check also fails.

  **Fix**: a new fifth strategy is added as the final fallback.  When no
  earlier strategy matches, `_matches_service` iterates the canonical
  `SERVICE_PATTERNS` list (already used during initial service detection) and
  tests each pattern whose `service_type` matches the configured service.  If
  any regex matches the combined email text, the email is accepted.  This
  ensures that Spanish keywords such as `"gastos comunes"`, `"aguas andinas"`,
  `"metrogas"`, etc. are recognised regardless of the sender address.

  - `_matches_service` now accepts an optional `service_type` parameter
    (defaults to `SERVICE_TYPE_UNKNOWN` for backward compatibility).
  - `SERVICE_PATTERNS` is now imported into `sensor.py` from `service_detector`.
  - The call site in the coordinator's `_async_update_data` loop passes
    `service_type` to `_matches_service`.

## [1.2.0] - 2026-04-06

### Added

- **Full OCR Space analysis of the Gastos Comunes PDF** (`attribute_extractor.py`):

  Previously, the OCR.space tier-2 pass only extracted the hot-water meter
  table (fields absent from the embedded text layer) plus two override fields
  (`building_name`, `cargo_fijo`).  All remaining fields extracted by pdfminer
  were left at confidence score 70 even when OCR could read them more
  accurately from the JPEG image.

  All tier-1 fields are now re-read from the JPEG via OCR.space and, when a
  match is found, the pdfminer value is overridden and the confidence score is
  raised from 70 (pdfminer) to 85 (OCR):

  - `billing_period_month`, `billing_period_year`, `billing_period_start`,
    `billing_period_end`
  - `emission_date`, `due_date`
  - `building_rut`, `address`, `apartment`, `owner_name`
  - `alicuota`, `building_total_expense`, `fondos_pct`
  - `gastos_comunes_amount`, `fondos_amount`, `subtotal_departamento`
  - `subtotal_recargos`, `total_amount`
  - `last_payment_date`, `last_payment_amount`, `last_payment_folio`

  New OCR-specific regex patterns added:
  - `_GC_OCR_EMISSION_DATE_RE` — matches "Fecha Emisión: DD-MM-YYYY"
  - `_GC_OCR_DUE_DATE_RE` — matches "Pagar Hasta: DD-MM-YYYY"
  - `_GC_OCR_ALICUOTA_RE` — matches "0,XXXXX %" without font garbling
  - `_GC_OCR_GC_AMOUNT_RE` — matches GC apartment amount after alícuota line
  - `_GC_OCR_FONDOS_AMOUNT_RE` — matches fondos amount after "FONDOS N%"
  - `_GC_OCR_SUBTOTAL_DEPTO_RE` — matches "Subtotal Departamento $X"
  - `_GC_OCR_TOTAL_AMOUNT_RE` — matches "Total del mes / Total a pagar $X"
  - `_GC_OCR_LAST_PAYMENT_DATE_RE` — matches "Fecha Último Pago … DD-MM-YYYY"
  - `_GC_OCR_LAST_PAYMENT_AMOUNT_RE` — matches "Monto Último Pago … $X"
  - `_GC_OCR_LAST_PAYMENT_FOLIO_RE` — matches "Folio Último … NNNNN"

## [1.1.0] - 2026-04-05

### Fixed

- **Gastos Comunes PDF: extraction patterns were tuned exclusively to the
  reference building, producing wrong values for any second building**
  (`attribute_extractor.py`):

  The pdfminer-tier regex patterns contained hard-coded assumptions about a
  single building's bill layout.  Any "Nota de Cobro" PDF from a different
  building caused nearly all extracted values to be incorrect.

  - **`_GC_AMOUNT_RE`** — alícuota pattern required the fractional digits to
    start with `9` (i.e. `[O0]\s*9\d{4}\s*%`), restricting matches to
    alícuotas ≥ 0.9 %.  Buildings with lower alícuotas (e.g. 0.325 %) were
    silently skipped.
    **Fix:** changed to `[O0]\s*\d{4,6}\s*%` — accepts any sub-1 % alícuota
    whose decimal part is 4–6 digits.

  - **`_GC_FONDOS_AMOUNT_RE`** — fondos pattern matched only the 5 % case
    (garbled by pdfminer as `500 %` or `5,0 %`).  Buildings that provision
    10 %, 7 %, etc. produced no fondos amount match.
    **Fix:** changed `5[0,]` to `\d{1,2}[0,]` — accepts any 1–2 digit
    integer fondos percentage (5 %, 10 %, 7 %, etc.).

  - **`_GC_BUILDING_TOTAL_RE` fallback** — the fallback pattern required the
    formatted building total to start with `1x` (`1\d[\d.]{6,}`), restricting
    matches to totals ≥ $10,000,000.  Smaller buildings whose total is in the
    low millions were never matched by the fallback.
    **Fix:** changed to `\d[\d.]{8,}` — accepts any formatted CLP amount
    whose string representation is ≥ 9 characters, corresponding to amounts
    ≥ $1,000,000.

  - **`_GC_THREE_AMOUNTS_RE` fallback (positional)** — the three-consecutive-
    amounts fallback (used when individual patterns fail) searched the entire
    document text with no label context.  If any other section of the PDF
    happened to contain three consecutive `$` amounts before the breakdown
    table, those unrelated values were picked up instead of the correct
    GC / fondos / subtotal figures.
    **Fix:** the search is now scoped to a window starting at the earliest
    `alícuota` / `fondos X%` / `Gasto Común` anchor and ending just after the
    first `Cargo Fijo` or `Subtotal Recargos` label, preventing false-positive
    matches from other document sections.

## [1.0.3] - 2026-03-30

### Fixed

- **False "OCR key not configured" notification when key is present**
  (`attribute_extractor.py`):

  `_try_ocr_pdf()` previously set `_ocr_available = False` whenever the
  OCR.space call returned empty text — even when an API key *was* configured.
  This happened because the function fell through to the "no key" code path
  any time OCR returned nothing (e.g. a PDF without hot-water content, a
  transient API error, or a rate-limit response).  As a result, HA raised a
  persistent Repair issue and notification claiming *"No OCR.space API key is
  configured"* while the key was clearly visible in the CONFIGURE dialog.

  The flag now correctly reflects whether the key is **configured**, not
  whether OCR extracted text from a specific PDF:

  - `_ocr_available = False` — no key provided (genuinely unconfigured).
  - `_ocr_available = True`  — key is present; extraction result for any
    individual PDF does not affect the flag.

### Added

- **Long-term statistics for all numeric billing sensors** (`sensor.py`):

  Added `state_class = SensorStateClass.MEASUREMENT` to all numeric sensor
  classes:
  - `ConciergeServiceConsumptionSensor`
  - `ConciergeServiceCostPerUnitSensor`
  - `ConciergeServiceTotalAmountSensor`
  - `ConciergeServiceBillingBreakdownSensor`

  Without `state_class`, Home Assistant's recorder only kept the short-term
  state history (last few days).  With `MEASUREMENT` set, every bill value is
  also recorded in the long-term statistics tables, enabling historical charts
  spanning months or years from the HA Statistics / History cards.

## [1.0.2] - 2026-03-27

### Added

- **OCR.space cloud API as the OCR engine for Agua Caliente (Hot Water) extraction**
  (`attribute_extractor.py`, `sensor.py`, `config_flow.py`, `const.py`,
  `strings.json`, `translations/en.json`, `translations/es.json`, `README.md`):

  The [OCR.space](https://ocr.space/OCRAPI) free REST API is now the sole OCR
  engine used to extract hot-water meter data from the Gastos Comunes "Nota de
  Cobro" PDF.  Users must obtain a free API key at <https://ocr.space/OCRAPI>
  before installation (or add it later via **CONFIGURE**).

  OCR pipeline (two passes per bill):
  1. Full-page render at 3× zoom (Spanish, OCR Engine 2).
  2. Agua Caliente table crop (30–55 % from top, upscaled 2×, OCR Engine 2).

  The key is stored in Home Assistant configuration and exposed as the
  **OCR.space API Key** field in both the initial **Finalize Configuration**
  step and the **CONFIGURE** (options) reconfiguration form.

### Removed

- **RapidOCR / onnxruntime / PyMuPDF engine** — removed entirely.
  `onnxruntime` has no pre-built wheel for Home Assistant OS / Alpine / musl
  libc, making it impossible to install in the standard HA environment.
  Functions removed: `_try_ocr_pdf_rapidocr`, `_ocr_boxes_to_text`,
  `_validate_ocr_against_pdfminer`, `_save_pdf_with_ocr_text_layer`.

- **Concierge Add-on REST API fallback** — removed entirely.
  Functions removed: `_try_ocr_pdf_via_api`.
  Configuration field `ocr_api_url` / `CONF_CONCIERGE_ADDON_URL` removed from
  `const.py`, `config_flow.py`, `sensor.py`, and all translation files.

## [1.0.1] - 2026-03-26

### Fixed

- **Remove literal example URLs from translation strings to pass HA validation**
  (`strings.json`, `translations/en.json`):

  Home Assistant's translation validator rejects string values that contain
  literal URLs.  Removed the inline example `http://homeassistant.local:8099`
  from two strings:

  - The `ocr_api_url` field description in the options flow.
  - The `ocr_unavailable` Repair issue description.

  The strings still explain *what* to configure; the example URL is simply no
  longer embedded in the translated text.

## [1.0.0] - 2026-03-26

### Added

- **Concierge Add-on REST API as OCR fallback**
  (`attribute_extractor.py`, `sensor.py`, `config_flow.py`, `const.py`,
  `strings.json`, `translations/en.json`, `translations/es.json`, `README.md`):

  A new optional **Concierge Add-on URL** (`ocr_api_url`) setting is available
  in the integration options.  When RapidOCR is unavailable (e.g. on
  Home Assistant OS / Alpine / musl libc where `onnxruntime` has no pre-built
  wheel), the integration now falls back to the
  [Concierge Add-on](https://github.com/Geek-MD/Concierge_Addon) REST API
  (`POST /ocr/file`, default port **8099**) to perform OCR on the
  "Nota de Cobro" PDF.

  **OCR priority order:**
  1. **RapidOCR** (primary) — used when `rapidocr`, `onnxruntime` and `PyMuPDF`
     are installed.
  2. **Concierge Add-on REST API** (fallback) — used when RapidOCR is
     unavailable and `ocr_api_url` is configured.
  3. **None** — a warning is logged, a persistent notification is raised, and
     a Repair issue appears recommending the Concierge Add-on installation.

- **Persistent notification and Repair issue when OCR is unavailable**
  (`sensor.py`, `strings.json`, `translations/en.json`, `translations/es.json`):

  When neither RapidOCR nor the Concierge Add-on is available, Home Assistant
  displays a persistent notification and a Repair issue (key `ocr_unavailable`)
  directing the user to install the Concierge Add-on.  Both are automatically
  dismissed on the first successful OCR run.

### Changed

- **`rapidocr`, `onnxruntime` and `PyMuPDF` are now optional dependencies**
  (`manifest.json`):

  These packages are no longer listed as hard requirements in `manifest.json`.
  `onnxruntime` has no pre-built wheel for Home Assistant OS / Alpine / musl
  libc, causing installation failures.  The libraries are still used when
  available; when absent, the Concierge Add-on REST API is used as fallback.

### Removed

- **Tesseract OCR add-on support removed**
  (`attribute_extractor.py`, `sensor.py`, `config_flow.py`, `const.py`,
  `strings.json`, `translations/en.json`, `translations/es.json`, `README.md`):

  The Tesseract HTTP API path (`CONF_TESSERACT_API_URL` / `tesseract_api_url`)
  has been removed entirely.  The integration never relied on Tesseract as a
  designed solution — it was an accidental historical artefact.  All OCR is
  now handled by **RapidOCR** (primary) or the **Concierge Add-on REST API**
  (fallback).

  - `CONF_TESSERACT_API_URL` constant removed from `const.py`.
  - `tesseract_api_url` parameter removed from `extract_attributes_from_pdf`,
    `_extract_pdf_type_specific_attributes`, `_extract_common_expenses_pdf_attributes`,
    and `_try_ocr_pdf`.
  - `tesseract_api_url` field removed from the integration options flow.
  - `is_tesseract_available()` renamed to `is_ocr_available()`.
  - Repair issue key renamed from `tesseract_not_found` to `ocr_unavailable`.
  - Internal method `_manage_tesseract_repair_issue()` renamed to
    `_manage_ocr_repair_issue()`.



### Fixed

- **Migrate from `rapidocr-onnxruntime` to `rapidocr` + `onnxruntime`**
  (`manifest.json`, `attribute_extractor.py`, `sensor.py`,
  `strings.json`, `translations/en.json`, `translations/es.json`, `README.md`):

  `rapidocr-onnxruntime` declares `Requires-Python: <3.13`, which causes Home
  Assistant to reject the package on Python 3.13+ environments (and in strict
  package-manager configurations such as `uv`).  The package is also no longer
  actively maintained.

  The fix replaces it with the actively maintained **`rapidocr` ≥ 3.0.0**
  (supports Python 3.6–3.x, no upper-version cap) and a separate
  **`onnxruntime` ≥ 1.7.0** dependency.  `rapidocr` v3 uses `onnxruntime` as
  its default inference backend (configured via its bundled `config.yaml`) and
  returns a `RapidOCROutput` dataclass instead of the legacy `(list, elapsed)`
  tuple.

  **`manifest.json`:** replaced `rapidocr-onnxruntime>=1.4.0` with
  `rapidocr>=3.0.0` and added `onnxruntime>=1.7.0`.

  **`attribute_extractor.py`:** updated `_try_ocr_pdf_rapidocr` to import
  `from rapidocr import RapidOCR` and to convert the `RapidOCROutput` result
  (`result.boxes`, `result.txts`, `result.scores`) back to the
  `[bbox, text, score]` list format expected by `_ocr_boxes_to_text` and
  `_save_pdf_with_ocr_text_layer`.  All docstrings updated accordingly.

## [0.9.14] - 2026-03-24

### Added

- **RapidOCR built-in engine — eliminates Tesseract dependency**
  (`attribute_extractor.py`, `manifest.json`, `sensor.py`,
  `strings.json`, `translations/en.json`, `translations/es.json`, `README.md`):

  The integration now uses **`rapidocr-onnxruntime`** as its primary OCR engine
  for extracting Agua Caliente (hot water) sensor data from the Gastos Comunes
  PDF.  Both `rapidocr-onnxruntime` and `PyMuPDF` are installed automatically by
  Home Assistant as Python package dependencies — no system binary installation,
  no HAOS add-on, and no manual configuration is required.

  **What RapidOCR provides over Tesseract:**
  - Pure-Python implementation using ONNX Runtime; no system binary.
  - Works on all HA installation types (HAOS, Docker, Supervised, Core) without
    any extra steps.
  - Superior accuracy on the "Nota de Cobro" layout: correctly reads
    `cargo_fijo` ($9.638 vs. pdfminer's font-garbled $9.838) and the building
    name ("Jose Miguel" vs. "Jon Miguel").
  - On first use, PP-OCRv4 ONNX models (~20 MB total) are downloaded to the
    system cache automatically.

  **New functions in `attribute_extractor.py`:**
  - `_ocr_boxes_to_text(results)` — reconstructs text from RapidOCR bounding
    boxes by grouping items into rows (±20 px Y-tolerance) and sorting each row
    left-to-right.
  - `_try_ocr_pdf_rapidocr(pdf_path)` — renders the PDF with `PyMuPDF`,
    runs RapidOCR, and returns `(text, raw_results)`.
  - `_validate_ocr_against_pdfminer(ocr_text, pdfminer_text)` — cross-validates
    the OCR output against the embedded text layer by checking how many
    reference tokens (amounts, dates from pdfminer) appear in the OCR text.
    Returns a score in [0, 1].
  - `_save_pdf_with_ocr_text_layer(pdf_path, ocr_results, zoom)` — embeds OCR
    results as an invisible text overlay (PDF render-mode 3) into a copy of the
    PDF saved as `*_searchable.pdf`.  Future pdfminer reads of this copy find
    the complete text without needing OCR.

  **Updated `_try_ocr_pdf`** now returns `(ocr_text, raw_results)` and uses
  the following priority order:
  1. Tesseract HTTP API (if `tesseract_api_url` is configured — backward compat).
  2. RapidOCR (default for all new and existing installations).

  **Regex pattern fixes** for RapidOCR output format:
  - `_GC_OCR_CARGO_FIJO_RE`: `\s+` → `\s*` between "Cargo" and "Fijo"
    (RapidOCR concatenates them as "CargoFijo").
  - `_GC_OCR_BUILDING_NAME_RE`: accepts "Pagar" as a separator in addition to
    "Fecha" (RapidOCR places "Edificio Jose Miguel Pagar Hasta:" on one row).

  **`manifest.json`:** removed `pytesseract>=0.3.13`; added
  `rapidocr-onnxruntime>=1.4.0` and `PyMuPDF>=1.23.0`.

  **Repair issue** (`tesseract_not_found`): translation text updated to refer
  to the OCR engine generically rather than Tesseract specifically.  Any
  previously active `tesseract_not_found` issue is automatically resolved on
  the first successful RapidOCR run.

## [0.9.13] - 2026-03-24

### Added

- **Tesseract OCR add-on support for Home Assistant OS (HAOS)**
  (`attribute_extractor.py`, `sensor.py`, `config_flow.py`, `const.py`,
  `strings.json`, `translations/en.json`, `translations/es.json`, `README.md`):

  On HAOS the Home Assistant container image is read-only, which means the
  `tesseract-ocr` system binary cannot be installed persistently — it is wiped
  on every HA Core update.  Starting with this version the integration supports
  running OCR via the **Tesseract OCR API** HTTP add-on from the
  [Kosztyk add-on repository](https://github.com/Kosztyk/homeassistant-addons),
  which exposes Tesseract as a persistent service at `http://<host>:8000`.

  **How it works:**
  - `attribute_extractor.py` — new `_try_ocr_pdf_via_api(pdf_path, api_url)`
    function renders PDF pages to PNG images with `pypdfium2`/`Pillow` and
    POSTs them to the add-on's `/ocr/file` endpoint (three passes: PSM 1, 6, 4)
    using `urllib.request` (no new dependency).  The existing
    `_try_ocr_pdf(pdf_path, tesseract_api_url="")` function now delegates to
    this helper when an API URL is configured, or falls back to the local
    `pytesseract` binary path when the URL is empty.  The
    `_extract_common_expenses_pdf_attributes`, `_extract_pdf_type_specific_attributes`,
    and `extract_attributes_from_pdf` functions accept the new
    `tesseract_api_url` parameter and propagate it through the call chain.
  - `const.py` — added `CONF_TESSERACT_API_URL = "tesseract_api_url"`.
  - `config_flow.py` — added an optional **Tesseract OCR Add-on URL** field to
    the `OptionsFlowHandler` (CONFIGURE screen).
  - `sensor.py` — reads `CONF_TESSERACT_API_URL` from the effective config and
    passes it to `extract_attributes_from_pdf`.
  - `strings.json`, `translations/en.json`, `translations/es.json` — added the
    new field label / description to the options step and updated the
    `tesseract_not_found` Repair issue description to guide users towards the
    add-on approach on HAOS.

  **README** — replaced the HAOS section (which instructed users to run
  `apt-get` inside the container — not persistent) with step-by-step
  instructions to install the Tesseract OCR API add-on and configure the
  integration's **Tesseract OCR Add-on URL** setting.  Docker / Supervised
  instructions are retained (binary path, URL left empty).

## [0.9.12] - 2026-03-24

### Added

- **HA Repair notification when `tesseract-ocr` is not installed**
  (`sensor.py`, `attribute_extractor.py`, `strings.json`,
  `translations/en.json`, `translations/es.json`):

  When the first Gastos Comunes bill is processed and the `tesseract-ocr`
  system binary is absent, the integration now raises a **Repair issue**
  visible in **Settings → Repairs**.  The issue card explains which five
  Agua Caliente sensors are affected and links directly to the new
  Prerequisites section of the README for step-by-step installation
  instructions.  The issue is dismissed automatically the next time a bill
  is processed with Tesseract working correctly.

  Implementation details:
  - `attribute_extractor.py` — added module-level `_tesseract_available`
    flag (updated by `_try_ocr_pdf` on success/failure) and a public
    `is_tesseract_available()` accessor.
  - `sensor.py` — new `_manage_tesseract_repair_issue()` helper called after
    every regular poll (`_async_update_data`) and every force-refresh
    (`async_refresh_service`); uses `homeassistant.helpers.issue_registry`
    (`ir.async_create_issue` / `ir.async_delete_issue`).
  - `strings.json`, `translations/en.json`, `translations/es.json` — added
    `issues.tesseract_not_found` with bilingual title and description.

- **README — Prerequisites section** (`README.md`):

  Added a new `## 📋 Prerequisites` section immediately before `## 📦 Installation`
  with step-by-step instructions for installing `tesseract-ocr` on the three
  most common HA deployment types:
  - **Home Assistant OS (HAOS)** — via the *Advanced SSH & Web Terminal*
    add-on and `docker exec`.
  - **Docker / Docker Compose** — custom `Dockerfile` extending the official
    HA image for a persistent installation.
  - **Supervised HA on Debian / Ubuntu** — same `docker exec` approach.

  Each method includes a caveat about non-persistence across HA Core updates
  (for HAOS and Supervised).

### Fixed

- **README**: corrected an outdated reference to `pymupdf` (replaced by
  `pypdfium2` since v0.9.10) in the Hot Water sensor description.

- **`manifest.json`**: version bumped to `0.9.12`.

## [0.9.11] - 2026-03-24

### Fixed

- **Spurious `WARNING` log when `tesseract-ocr` is not installed**
  (`attribute_extractor.py`):

  Every time a Gastos Comunes PDF was processed, `_try_ocr_pdf` logged a
  `WARNING` if the `tesseract-ocr` system binary was absent:

  > `Tesseract OCR not found for '…/common_expenses_2026-03.pdf':
  > tesseract is not installed or it's not in your PATH.
  > Install tesseract-ocr to enable Agua Caliente extraction.`

  Because `tesseract-ocr` is an optional system package that many Home
  Assistant container environments cannot install, this warning fired on every
  PDF poll cycle, flooding the HA log without providing actionable information
  to the user.

  The function docstring already stated *"Failures are logged at DEBUG level
  only"* — the `TesseractNotFoundError` handler was inconsistent with that
  contract.

  **Change**: downgraded the `TesseractNotFoundError` log call from
  `_LOGGER.warning` to `_LOGGER.debug`.  Users who want to diagnose missing
  Tesseract support can still find the message by enabling debug logging for
  the integration; it will no longer appear in the default HA log.

- **`manifest.json`**: version bumped to `0.9.11`.

## [0.9.10] - 2026-03-23

### Fixed

- **`PyMuPDF` fails to install — OCR unavailable** (`manifest.json`,
  `attribute_extractor.py`):

  `PyMuPDF>=1.24.0` (added in v0.9.9) fails to install on Home Assistant
  because it requires compilation from source (`setup.py:216`) and the HA
  container image does not ship the MuPDF development headers needed to build
  it.  This caused the integration to fail at startup with:

  > `Setup failed for custom integration 'concierge_ha_integration':
  > Requirements for concierge_ha_integration not found: ['PyMuPDF>=1.24.0']`

  **Changes**:

  1. `manifest.json` — replaced `PyMuPDF>=1.24.0` with `pypdfium2>=4.30.0`.
     `pypdfium2` ships pre-built binary wheels for all common platforms
     (Linux x86-64, ARM, macOS, Windows) so no compilation is required and
     `pip install` succeeds out of the box in any HA environment.

  2. `attribute_extractor.py` — `_try_ocr_pdf` updated to use the
     `pypdfium2` API instead of `fitz` (PyMuPDF):
     - `fitz.open(pdf_path)` → `pdfium.PdfDocument(pdf_path)`
     - `page.rect.height` → `page.get_height()`
     - `fitz.Matrix` + `page.get_pixmap()` + `Image.frombytes()` →
       `page.render(scale=...)` + `bitmap.to_pil()`
     - All `pix.width` references replaced with `img_full.width`
     - Warning messages and comments updated to reference `pypdfium2`.

- **`manifest.json`**: version bumped to `0.9.10`.

## [0.9.9] - 2026-03-23

### Fixed

- **Missing OCR requirements cause hot water sensors to always have no data**
  (`manifest.json`, `attribute_extractor.py`):

  The five `sensor.concierge_common_expenses_hot_water_*` sensors
  (`consumption`, `cost_per_unit`, `amount`, `curr_reading`, `prev_reading`)
  always reported no data because their values are extracted via Tesseract OCR
  on the JPEG-backed "Nota de Cobro" PDF — a step that requires three Python
  libraries (`PyMuPDF`, `Pillow`, `pytesseract`) that were never listed in
  `manifest.json`.

  Home Assistant installs only the packages listed in `requirements`; without
  them, `_try_ocr_pdf` raised an `ImportError` on every call and returned an
  empty string, so the entire OCR extraction block was silently skipped and all
  five hot-water attributes remained unset.  Because the failure was logged only
  at `DEBUG` level it was invisible in the HA log, making the root cause
  impossible to diagnose.

  **Changes**:

  1. `manifest.json` — added the three missing pip requirements:
     - `PyMuPDF>=1.24.0` (PDF-to-image rendering)
     - `Pillow>=10.2.0` (image processing)
     - `pytesseract>=0.3.13` (Python wrapper for Tesseract OCR)

     With these packages installed HA will run OCR on the "Nota de Cobro" PDF
     and populate all five hot-water sensors.

     > **Note**: the `tesseract-ocr` system binary must also be present on the
     > host (e.g. `apt-get install tesseract-ocr tesseract-ocr-spa` on Debian /
     > Ubuntu).  If only the Python packages are installed the code logs a
     > `WARNING` and the sensors remain empty until the binary is added.

  2. `attribute_extractor.py` — the `ImportError` log in `_try_ocr_pdf` was
     upgraded from `DEBUG` to `WARNING` so that any future missing-library
     scenario is visible in the HA log without needing to enable debug logging.

- **`manifest.json`**: version bumped to `0.9.9`.

## [0.9.8] - 2026-03-23

### Fixed

- **Hot Water OCR: missing consumo column now handled** (`attribute_extractor.py`):

  The Tesseract OCR pass on the "Nota de Cobro" PDF (PSM 6, middle-section crop)
  sometimes fails to extract the *Consumo* column of the Agua Caliente table row,
  producing a line like:

  ```
  Agua Caliente | 585,396000| 588,379000 7.034,70
  ```

  instead of the expected four-value row:

  ```
  Agua Caliente  585,396000  588,379000  2,983000  7.034,70  $20.985
  ```

  Previously `_GC_OCR_HOT_WATER_ROW_RE` required the consumo group, so the
  entire `hw_m` match failed — leaving `hot_water_reading_prev`,
  `hot_water_reading_curr`, `hot_water_consumption`, and `hot_water_cost_per_m3`
  all unset.  The sensors
  `sensor.concierge_common_expenses_hot_water_consumption`,
  `sensor.concierge_common_expenses_hot_water_cost_per_unit`,
  `sensor.concierge_common_expenses_hot_water_curr_reading`, and
  `sensor.concierge_common_expenses_hot_water_prev_reading` therefore reported
  no data.

  **Changes**:

  1. `_GC_OCR_HOT_WATER_ROW_RE` — the *consumo* capture group (group 3) is now
     wrapped in `(?:...)?` making it optional.  The remaining groups (4 = valor
     total, 5 = optional monto) retain the same numbers.

  2. Extraction code — when group 3 is `None` (consumo not captured by OCR),
     `hot_water_consumption` is derived arithmetically:
     `consumo = lectura_actual − lectura_anterior` (rounded to 6 decimal places).

- **`manifest.json`**: version bumped to `0.9.8`.

## [0.9.7] - 2026-03-23

### Changed

- **All entity and PDF filenames now use English generic service names**
  (`service_detector.py`, `sensor.py`, `binary_sensor.py`, `button.py`,
  `__init__.py`):

  Service display names in `SERVICE_PATTERNS` have been translated to English,
  and `normalize_service_id()` is now applied at runtime so that any subentry
  whose stored `service_id` still uses a Spanish or company-specific slug is
  transparently mapped to the canonical English ID when generating entity names
  and PDF filenames.

  | Old service ID (Spanish / company) | New canonical ID (English) |
  |---|---|
  | `aguas_andinas` | `water` |
  | `agua` | `water` |
  | `agua_caliente` | `hot_water` |
  | `electricidad` | `electricity` |
  | `telecomunicaciones` | `telecom` |
  | `internet_tv` | `telecom` |
  | `gastos_comunes` | `common_expenses` |

  Examples of renamed entity IDs:

  | Old entity ID | New entity ID |
  |---|---|
  | `sensor.concierge_aguas_andinas_fixed_charge` | `sensor.concierge_water_fixed_charge` |
  | `sensor.concierge_gastos_comunes_hot_water_consumption` | `sensor.concierge_common_expenses_hot_water_consumption` |
  | `sensor.concierge_electricidad_bill` | `sensor.concierge_electricity_bill` |
  | `sensor.concierge_telecomunicaciones_total` | `sensor.concierge_telecom_total` |

  PDF filenames stored in the integration's working directory are also renamed:
  e.g. `gastos_comunes_2026-01.pdf` → `common_expenses_2026-01.pdf`.

  > **Migration note**: a v1.5 config-entry migration automatically renames all
  > existing entity registry entries on the next HA restart, preserving entity
  > history.  No manual changes are needed.

- **`manifest.json`**: version bumped to `0.9.7`.

- **`__init__.py`**: config-entry minor version bumped to `1.5` with a new
  migration (`_migrate_1_4_to_1_5`) that renames entity IDs in the entity
  registry from Spanish/legacy slugs to English generic names.

## [0.9.6] - 2026-03-23

### Changed

- **Hot Water sensor entity IDs now use English names** (`sensor.py`, `README.md`):

  The five Hot Water sensor entities on the Gastos Comunes device previously used
  "Agua Caliente" in their display names, which produced Spanish entity IDs like
  `sensor.concierge_{id}_agua_caliente_amount`.  All five name suffixes have been
  updated to English so the generated entity IDs are consistent with the rest of
  the integration:

  | Old entity suffix | New entity suffix | Attribute key | Unit |
  |---|---|---|---|
  | `agua_caliente_consumption` | `hot_water_consumption` | `hot_water_consumption` | m³ |
  | `agua_caliente_cost_per_unit` | `hot_water_cost_per_unit` | `hot_water_cost_per_m3` | $/m³ |
  | `agua_caliente_amount` | `hot_water_amount` | `hot_water_amount` | $ |
  | `agua_caliente_prev_reading` | `hot_water_prev_reading` | `hot_water_reading_prev` | m³ |
  | `agua_caliente_curr_reading` | `hot_water_curr_reading` | `hot_water_reading_curr` | m³ |

  > **Migration note**: existing installations will need to remove and re-add the
  > integration (or manually rename the entity IDs in the HA entity registry) to
  > pick up the new English entity IDs.

- **Hot Water OCR regex made more robust** (`attribute_extractor.py`):

  The `_GC_OCR_HOT_WATER_ROW_RE` pattern that extracts the Agua Caliente table
  row from the PDF OCR output has been tightened in two ways:

  1. **Period accepted as decimal separator** — the meter-reading capture groups
     now use `[\d,.]` instead of `[\d,]`, so readings like `585.396000` (period
     as decimal) produced by some Tesseract configurations are captured correctly.
  2. **Pipe column separators tolerated** — the inter-column separator changed
     from `\s+` to `[\s|]+`, allowing OCR output that includes `|` characters
     between table columns (common when Tesseract renders table borders).
  3. **Wider gap window** — the non-greedy bridge between "Agua Caliente" and the
     first reading was widened from `{0,30}` to `{0,60}` characters so multi-line
     OCR output is handled without truncation.

  As a result, `sensor.concierge_{id}_hot_water_prev_reading`,
  `sensor.concierge_{id}_hot_water_curr_reading`,
  `sensor.concierge_{id}_hot_water_consumption`, and
  `sensor.concierge_{id}_hot_water_cost_per_unit` now receive values from the
  OCR tier whenever `pymupdf`, `pytesseract`, and `tesseract-ocr` are available.

- **`manifest.json`**: version bumped to `0.9.6`.

## [0.9.5] - 2026-03-22

### Added

- **Agua Caliente sensors on the Gastos Comunes device** (`sensor.py`,
  `binary_sensor.py`):

  The "Nota de Cobro" PDF for Gastos Comunes already includes a hot-water
  (Agua Caliente) consumption table that is extracted via OCR (Tier 2).
  However, no dedicated sensor entities existed for those values on the
  Gastos Comunes device — and a standalone "Agua Caliente" subentry would
  never work because there is no separate email for it; the data lives
  exclusively inside the Gastos Comunes email/PDF.

  **Fix**: Five new `ConciergeServiceBillingBreakdownSensor` entities are now
  created automatically for every `SERVICE_TYPE_COMMON_EXPENSES` subentry,
  grouped on the same Gastos Comunes device:

  | Entity suffix | Attribute key | Unit | Unique-ID suffix |
  |---|---|---|---|
  | Agua Caliente Consumption | `hot_water_consumption` | m³ | `gc_hw_consumption` |
  | Agua Caliente Cost Per Unit | `hot_water_cost_per_m3` | $/m³ | `gc_hw_cost_per_m3` |
  | Agua Caliente Amount | `hot_water_amount` | $ | `gc_hw_amount` |
  | Agua Caliente Prev Reading | `hot_water_reading_prev` | m³ | `gc_hw_prev_reading` |
  | Agua Caliente Curr Reading | `hot_water_reading_curr` | m³ | `gc_hw_curr_reading` |

  Values are populated automatically whenever the OCR Tier-2 pass on the PDF
  succeeds (requires `pymupdf`, `pytesseract`, and `tesseract-ocr`).  When
  OCR is unavailable, the sensors exist but report `None` until a manual
  override is applied via the `set_value` service.

  The Gastos Comunes status binary sensor (`binary_sensor.py`) now also
  includes `previous_measure` and `actual_measure` (meter readings) in its
  diagnostic attributes, consistent with the new sensor entities.

- **`manifest.json`**: version bumped to `0.9.5`.

## [0.9.4] - 2026-03-22

### Changed

- **`set_value` service — single-entity enforcement** (`__init__.py`,
  `services.yaml`, `strings.json`, `translations/en.json`,
  `translations/es.json`):

  The `set_value` action now enforces that **exactly one entity** is selected
  as the target.  If more than one entity is passed (e.g. via YAML automation
  that lists multiple entity IDs), the service raises a clear error:

  > "The set_value service only supports one entity at a time. Please select
  > exactly one entity (got N)."

  The service description in the UI and all translation files has been updated
  to document this restriction.

### Fixed

- **Formula-derived sensors auto-recalculate on override** (`sensor.py`):

  Sensors whose values are computed from other attributes (e.g.
  `sensor.concierge_gastos_comunes_total`, which equals
  `subtotal_departamento + cargo_fijo`) were not updated when one of their
  inputs was overridden via the `set_value` service.  The entity showed the
  old calculated value until the next email/PDF scan.

  **Fix**: After applying a learning override in-memory, the coordinator now
  calls a new `_recompute_gc_derived_attrs` method that re-runs all
  common-expenses alias syncs and arithmetic derivations:

  | Formula / alias | Recomputed when… |
  |---|---|
  | `fixed_charge` ↔ `cargo_fijo` | Either end is overridden |
  | `subtotal` ↔ `subtotal_departamento` | Either end is overridden |
  | `funds_provision` ↔ `fondos_amount` | Either end is overridden |
  | `gc_total = subtotal_departamento + cargo_fijo` | Any input changes |

  Attributes that were themselves overridden by the user
  (`extraction_confidence = 100`) are never overwritten by the recomputation.

## [0.9.3] - 2026-03-22

### Fixed

- **`set_value` service — UI fields and value not applied** (`__init__.py`,
  `services.yaml`, `strings.json`, `translations/en.json`,
  `translations/es.json`):

  Two related bugs were fixed:

  1. **UI fell back to YAML instead of showing form fields.**  In HA's service
     framework, a `fields` entry named `entity_id` is treated as a special
     *target* entity selector by the frontend.  This caused the `attribute` and
     `value` fields to not render as proper text-input widgets, forcing users
     to edit raw YAML.  The fix moves entity selection to HA's standard
     `target` mechanism in `services.yaml` and removes `entity_id` from
     `fields`.  The entity picker now renders as the action's target (shown
     above the fields), and `attribute` / `value` render as plain text inputs.

  2. **Override value was never applied to the sensor.**  Because the UI
     showed YAML, users had to type the data manually.  The YAML in the
     screenshot shows `attribute: sensor.concierge_gastos_comunes_fixed_charge`
     — the entity_id was entered in the attribute field instead of the real
     attribute key (`fixed_charge`).  As a result, the learning store saved the
     override under the wrong key and the sensor never picked it up.  With the
     form fields rendering correctly, users select the entity via the picker and
     type only the numeric value, so the attribute key is always inferred
     correctly from the entity's unique_id when left empty.

  Updated service signature:

  | Field | Type | Description |
  |-------|------|-------------|
  | *(target)* | entity selector | Any entity from the Concierge HA Integration |
  | `attribute` | text (optional) | Internal attribute key — inferred from entity when omitted |
  | `value` | text | The correct value (e.g. `9638`) |

  The handler in `__init__.py` now reads `entity_id` from
  `service_call.target` instead of `service_call.data`.

- **`manifest.json`**: version bumped to `0.9.3`.

## [0.9.2] - 2026-03-21

### Fixed

- **OCR log level** (`attribute_extractor.py`):

  The `ImportError` raised when `pymupdf`, `pytesseract`, or `Pillow` are not
  installed was logged at `WARNING` level, producing a noisy log entry on every
  PDF scan for users who do not need OCR.  Downgraded to `DEBUG`, consistent
  with the function docstring ("Failures are logged at DEBUG level only").

- **`set_value` service — `device_id` rejection** (`__init__.py`):

  When the service was invoked from the **Developer Tools → Actions** panel or
  any other HA UI picker, Home Assistant automatically injected `device_id`
  into the service-call data.  The voluptuous schema had no `device_id` key,
  so validation raised:

  > `extra keys not allowed @ data['device_id']. Got None`

  Fixed by adding `extra=vol.REMOVE_EXTRA` to `_SERVICE_SET_VALUE_SCHEMA` so
  the automatically-injected key is silently discarded before validation.

### Changed

- **`set_value` service — `value` field UI** (`services.yaml`):

  The `value` field selector was changed from `number` (spinner widget) to
  `text`, giving a plain text input box consistent with the `attribute` field.
  Users now see two intuitive text boxes side-by-side in the visual editor
  instead of a numeric spinner.

  Updated service fields:

  | Field | Type | Description |
  |-------|------|-------------|
  | `entity_id` | entity selector | Any entity from the Concierge HA Integration |
  | `attribute` | text (optional) | Internal attribute key — inferred from entity when omitted |
  | `value` | **text** | The correct value (e.g. `9638`) |

- **`manifest.json`**: version bumped to `0.9.2`.

## [0.9.1] - 2026-03-21

### Changed

- **`set_value` service — entity selector** (`__init__.py`, `services.yaml`,
  `strings.json`, `translations/en.json`, `translations/es.json`):

  The `set_value` action now targets a **Concierge HA Integration entity**
  (e.g. `sensor.concierge_gastos_comunes_fixed_charge`) instead of a device.
  This makes selection more precise and avoids ambiguity when a device exposes
  multiple sensors.

  The `attribute` field is now **optional**: when omitted, the attribute key is
  inferred automatically from the selected entity's unique_id, so callers that
  pick a dedicated sensor entity (e.g. the *Fixed Charge* sensor) do not need
  to know the internal key name.

  Updated service fields:

  | Field | Type | Description |
  |-------|------|-------------|
  | `entity_id` | entity selector | Any entity from the Concierge HA Integration |
  | `attribute` | text (optional) | Internal attribute key — inferred from the entity when omitted |
  | `value` | number | The correct value (e.g. `9638`) |

### Fixed

- **Duplicate `total_amount` sensor for Common Expenses** (`sensor.py`,
  `__init__.py`):

  `sensor.concierge_{id}_total_amount` was reading the same `gc_total`
  extracted attribute as `sensor.concierge_{id}_total` (the *Total*
  billing-breakdown sensor).  The `total_amount` sensor is no longer created
  for `common_expenses` devices.

  Existing installations are migrated automatically (config-entry minor version
  1.3 → 1.4): the stale `total_amount` entity is removed from the HA entity
  registry on the first restart after the upgrade.

- **`manifest.json`**: version bumped to `0.9.1`.

## [0.9.0] - 2026-03-21

### Added

- **Extraction confidence score on PDF-sourced sensors** (`attribute_extractor.py`,
  `sensor.py`):

  Every sensor whose value is derived from a PDF bill now exposes an
  `extraction_confidence` attribute (0–100) in its state attributes.  The
  score reflects how reliable the extraction method is:

  | Score | Source |
  |-------|--------|
  | 70    | pdfminer text layer (may have font-encoding errors) |
  | 85    | Tesseract OCR (more accurate for image-backed PDFs) |
  | 60    | Derived / calculated from other extracted values |
  | 100   | User-supplied correction via `set_value` service |

  This is implemented by the new `_confidence` metadata dict returned by
  `_extract_common_expenses_pdf_attributes()` (and added as a default
  70 % fallback by `extract_attributes_from_pdf()` for all other service
  types: water, gas, electricity).

  The new constants `CONF_SCORE_PDFMINER`, `CONF_SCORE_OCR`,
  `CONF_SCORE_DERIVED`, and `CONF_SCORE_OVERRIDE` are exported from
  `attribute_extractor.py`.

  Sensors that now carry `extraction_confidence`:
  - `sensor.*_consumption` (ConciergeServiceConsumptionSensor)
  - `sensor.*_cost_per_unit` (ConciergeServiceCostPerUnitSensor)
  - `sensor.*_total_amount` (ConciergeServiceTotalAmountSensor)
  - `sensor.*_bill`, `sensor.*_fixed_charge`, `sensor.*_subtotal`,
    `sensor.*_funds_provision`, `sensor.*_total`, and all other
    billing-breakdown sensors (ConciergeServiceBillingBreakdownSensor)

- **`set_value` service — learning override** (`__init__.py`,
  `sensor.py`, `services.yaml`, `strings.json`,
  `translations/en.json`):

  A new Home Assistant action `concierge_ha_integration.set_value` that
  allows forcing a correct value for any named attribute of a Concierge
  service device.  The correction is:

  1. **Persisted** to `<ha_config>/concierge_ha_integration/learning.json`
     so it survives HA restarts.
  2. **Applied immediately** — the sensor updates without waiting for the
     next 30-minute polling cycle.
  3. **Re-applied automatically** after every future email/PDF analysis,
     overriding any value extracted by pdfminer or OCR.
  4. **Flagged with `extraction_confidence = 100`** so users can see that
     the value comes from a user correction rather than automatic
     extraction.

  Service fields:

  | Field | Type | Description |
  |-------|------|-------------|
  | `device_id` | device selector | The Concierge service device |
  | `attribute` | text | Internal attribute key (e.g. `fixed_charge`) |
  | `value` | number | The correct value (e.g. `9638`) |

  **Use case**: `sensor.concierge_gastos_comunes_fixed_charge` reads
  `9838` because neither pdfminer nor OCR can decode the PDF glyph
  correctly.  Calling `set_value` with `attribute: fixed_charge`,
  `value: 9638` stores the correction and all dependent sensors
  (`gc_total`, `subtotal_consumo`, etc.) are also re-derived from the
  correct base value on the next refresh.

- **`manifest.json`**: version bumped to `0.9.0`.

## [0.8.5] - 2026-03-21

### Fixed

- **`sensor.concierge_gastos_comunes_last_update` timezone and time** (`sensor.py`):

  The emission date extracted from the PDF was stored as midnight UTC
  (`2026-02-18T00:00:00+00:00`), which rolled back to the previous day in
  negative-offset timezones (e.g. `America/Santiago`, UTC−3).

  **Fix**: The datetime is now created at **noon (12:00)** in the HA-configured
  local timezone by using `dt_util.DEFAULT_TIME_ZONE` instead of `timezone.utc`.
  The displayed value becomes e.g. `2026-02-18T12:00:00-03:00`.

- **`address` attribute on `binary_sensor.concierge_gastos_comunes_status`**
  (`attribute_extractor.py`):

  pdfminer's font-encoding misread "Jose" as "Jon" (e.g. "Edificio Jon Miguel"
  instead of "Edificio Jose Miguel").  This happened because the building-name
  glyphs are embedded in a non-standard font that pdfminer cannot decode
  accurately.

  **Fix**: A `difflib.SequenceMatcher` heuristic compares the pdfminer-extracted
  building name with the known-correct reference
  (`_GC_KNOWN_BUILDING_NAME = "Edificio Jose Miguel"`).  When the similarity
  ratio is ≥ 0.75 (a minor garbling), the known-correct name is used.  Only a
  substantially different extracted name (ratio < 0.75) is accepted as a genuine
  building change.  The OCR tier (Tier 2) continues to override with the
  accurately-read value when the optional libraries are installed.

- **`gross_common_expenses_percentage` precision**
  (`attribute_extractor.py`):

  The alícuota percentage was rounded to 6 decimal places; the user-visible value
  is now rounded to **4 decimal places** (e.g. `0.9511` instead of `0.951100`).

- **`sensor.concierge_gastos_comunes_fixed_charge` preferred extraction process**
  (`attribute_extractor.py`):

  pdfminer misreads the "Cargo Fijo" digit glyphs (e.g. `$9.638` → `$9.838`).
  The OCR tier (Tier 2, `_GC_OCR_CARGO_FIJO_RE`) was already present and
  correctly extracts `$9.638`; it is now explicitly documented as the
  **preferred** process and overrides the pdfminer-derived value when the
  optional OCR libraries are installed.

- **Agua Caliente (Subtotal Consumo) not extracted from Gastos Comunes PDF**
  (`attribute_extractor.py`, `binary_sensor.py`):

  The "Nota de Cobro" PDF contains an Agua Caliente section whose meter-reading
  table lives in the JPEG background and is therefore invisible to pdfminer.
  The OCR tier already extracted individual meter readings when available.
  However, the hot-water subtotal (`subtotal_consumo`) and amount
  (`hot_water_amount`) were missing when OCR was unavailable.

  **Fix**: After all OCR and pdfminer extraction, a derivation fallback computes:
  `subtotal_consumo = total_amount − subtotal_departamento − cargo_fijo`.
  When `cargo_fijo` comes from OCR the result is exact; without OCR the value
  is approximate (off by the pdfminer digit-garbling error, ~$200).

  The new attributes `hot_water_amount`, `subtotal_consumo`, and
  `funds_provision_percentage` are now also exposed as attributes of
  `binary_sensor.concierge_gastos_comunes_status`.

### Changed

- **`sensor.concierge_gastos_comunes_funds_provision_percentage` → attribute**
  (`sensor.py`, `binary_sensor.py`):

  The dedicated sensor entity `sensor.concierge_gastos_comunes_funds_provision_pct`
  has been removed.  The `funds_provision_percentage` value is now exposed as an
  attribute of `binary_sensor.concierge_gastos_comunes_status`, alongside the
  other building-level fields.

- **`manifest.json`**: version bumped to `0.8.5`.

## [0.8.4] - 2026-03-21

### Added

- **`force_refresh` service** (`__init__.py`, `services.yaml`):

  A new Home Assistant action `concierge_ha_integration.force_refresh` that
  forces an immediate email scan and PDF analysis for a single service device,
  bypassing the regular 30-minute polling interval.

  - **Device selector filtered to this integration** — the UI field only
    shows devices that belong to `concierge_ha_integration`; devices from
    other integrations or domains are never listed.
  - Accepts one required field: `device_id` (the HA device registry ID of
    the target Concierge service device).
  - Can be called from **Developer Tools → Actions**, automations, scripts,
    or the Lovelace button card.

- **Per-device *Force Refresh* button entity** (`button.py`):

  A `ButtonEntity` (entity category: `CONFIG`) is created for every service
  device subentry.  Pressing the button from the device detail page in
  **Settings → Devices & Services** triggers the same targeted refresh as
  the service above — only that one device's emails are re-scanned and its
  PDF re-analysed, without waiting for the next polling cycle.

  Entity ID pattern: `button.concierge_{service_id}_force_refresh`

- **`async_refresh_service` / `_fetch_single_service_data`** (`sensor.py`):

  Two new methods on `ConciergeServicesCoordinator`:
  - `async_refresh_service(subentry_id)` — async entry point; opens a
    dedicated IMAP connection for the targeted service, merges the fresh
    result into coordinator state, and notifies all listeners.
  - `_fetch_single_service_data(subentry_id)` — blocking helper (runs in
    an executor thread) that connects to IMAP, fetches and processes the
    latest email for a single subentry, and returns the result dict.

- **`"button"` platform** added to `PLATFORMS` in `__init__.py`.

- **Translations** (`strings.json`, `translations/en.json`,
  `translations/es.json`): new `services.force_refresh` section with
  English and Spanish labels/descriptions for the service and its
  `device_id` field.

- **`manifest.json`**: version bumped to `0.8.4`.

## [0.8.3] - 2026-03-21

### Fixed
- **Gas service incorrectly matching "Gastos Comunes" emails** (`sensor.py`):

  The service-ID pattern used in `_matches_service` was a plain substring
  search (`re.search("gas", combined_text, re.IGNORECASE)`).  Because "gas"
  is a prefix of "Gastos", any email whose subject or body contained the
  word "Gastos Comunes" was matched by the gas service account.  This caused
  the gas service to download the Gastos Comunes PDF (attached to the
  Gastos Comunes email) and rename it `gas_*.pdf`, producing incorrect
  sensor values.

  **Fix**: The service-ID pattern now uses whole-word (`\b`) boundaries:
  `\bgas\b` no longer matches "Gastos" while continuing to match standalone
  occurrences of the word "Gas" in email text.

- **Gas bill (and all services) now analyse only the most-recent matching
  email** (`sensor.py`):

  The email loop already iterates from newest to oldest.  The redundant
  `if latest_date is None or email_date > latest_date:` guard has been
  removed and the `break` is now unconditional once a matching email is
  found.  This makes the "use only the most-recent email" contract
  explicit: as soon as the first (newest) matching email is processed,
  the loop exits immediately — no further emails are examined regardless
  of their content.

- **`manifest.json`**: version bumped to `0.8.3`.

## [0.8.2] - 2026-03-20

### Fixed
- **"Gastos Comunes" email not detected during service scan** (`service_detector.py`):

  When a building-management email carries no generic billing keywords (e.g.
  *factura*, *boleta*, *cuenta*, *pago* …) — its subject is simply
  `"Gastos Comunes Ene 2026"` and its body is `"Enviado desde mi iPhone"` —
  the `_is_billing_email` guard returned `False` and the email was silently
  skipped.  `_extract_service_names` was never called, so the
  *Gastos Comunes* service never appeared in the **Add Service Device** flow.

  **Fix**: `_is_billing_email` now also iterates `SERVICE_PATTERNS`.  Any email
  that matches a recognised service pattern is treated as a billing email by
  definition, regardless of whether it contains generic billing vocabulary.
  This covers:
  - Emails with only a service-specific subject / attachment name and a sparse
    body (forwarded from iPhone, short administrative messages, …).
  - Any future service pattern whose emails might not include standard billing
    keywords.

- **`manifest.json`**: version bumped to `0.8.2`.

## [0.8.1] - 2026-03-20

### Fixed
- **Multi-service detection from a single combined bill email** (`service_detector.py`):

  When a building-management company sends one email (and one PDF) that covers
  both *Gastos Comunes* and *Agua Caliente*, the previous service-detector
  returned only the **first** matching service pattern and silently discarded
  all others.  As a result, neither service appeared in the "Add Service Device"
  flow and the user could not add them.

  Three related fixes are included:

  1. **`_extract_service_names` (renamed from `_extract_service_name`)** — now
     iterates *all* `SERVICE_PATTERNS` and collects **every** matching service
     from the combined email text (from-address + subject + body + attachment
     filenames), returning a list instead of a single tuple.  `detect_services_
     from_imap` is updated to loop over the list and register each service
     independently.

  2. **"Agua Caliente" pattern added to `SERVICE_PATTERNS`** — a new entry with
     regex `agua\s+caliente|agua\s+caliente\s+sanitaria|calefacci[oó]n\s+central`
     and type `SERVICE_TYPE_HOT_WATER` is inserted *before* the generic water
     patterns.  This prevents "Agua Caliente" text from being mis-classified as
     the regular cold-water ("Agua") service and allows both services to be
     detected from the same email.

  3. **Attachment filenames included in detection context** — a new helper
     `_get_attachment_filenames` extracts decoded filenames of all email
     attachments (e.g. `"Gastos Comunes Enero 2026.pdf"`) and appends them to
     the text that is matched against `SERVICE_PATTERNS`.  This fixes detection
     for building-management emails whose subject is generic (e.g. "Cobro Enero
     2026") but whose PDF attachment name makes the service unambiguous.

- **Already-configured services reappearing in "Add Service Device"**
  (`service_detector.py`, `config_flow.py`, `__init__.py`):

  The v0.7.16 release renamed the "Aguas Andinas" display name to "Agua",
  changing the derived `service_id` from `"aguas_andinas"` to `"agua"`.
  Users who had configured the water service *before* v0.7.16 kept the old
  `service_id` in their subentry data; the new detection code returned `"agua"`,
  which did not match `"aguas_andinas"` in the already-configured set, so the
  water service reappeared as available to add.

  **Fix**: a new `normalize_service_id` function (and `_LEGACY_SERVICE_IDS`
  mapping) resolves legacy IDs to their current canonical equivalents before
  the "already-configured" check is performed in both `config_flow.py` and
  `__init__.py`.

- **`manifest.json`**: version bumped to `0.8.1`.

## [0.8.0] - 2026-03-20

### Added
- **Common Expenses billing-breakdown sensors** (`sensor.py`, `attribute_extractor.py`):

  Five new dedicated sensor entities are now created for every `common_expenses`
  device, providing a full breakdown of the monthly GC (Gastos Comunes) bill:

  | Entity suffix | Value | Unit | Source / formula |
  |---|---|---|---|
  | `_funds_provision_percentage` | e.g. `5` | `%` | "PROVISIÓN DE FONDOS 5% DEL GASTO MENSUAL" label |
  | `_funds_provision` | e.g. `$6.697` | `$` | Bill × Funds % / 100 |
  | `_subtotal` | e.g. `$140.643` | `$` | Bill + Funds Provision ("Subtotal Departamento") |
  | `_fixed_charge` | e.g. `$9.638` | `$` | "Cargo Fijo" line |
  | `_total` (via `_total_amount`) | e.g. `$150.281` | `$` | Subtotal + Fixed Charge |

  The `_total_amount` sensor (already present in all devices) now uses
  `gc_total` as its attribute key for `common_expenses` devices, replacing
  the previous `subtotal_departamento`.

  **Extraction changes** (`attribute_extractor.py`):
  - New regex `_GC_FONDOS_PCT_RE` — extracts the integer provision percentage
    from "FONDOS 5% DEL GASTO MENSUAL".
  - New regex `_GC_CARGO_FIJO_RE` — best-effort pdfminer extraction of the
    fixed-charge amount from "Cargo Fijo $X".
  - New regex `_GC_OCR_CARGO_FIJO_RE` — OCR-accurate fixed-charge extraction
    (PSM-4 output); overrides the pdfminer value when present because pdfminer
    can misread font-encoded digits (e.g. `$9.838` instead of `$9.638`).
  - New derived alias keys written at the end of the extractor:
    `funds_provision_percentage`, `funds_provision`, `subtotal`,
    `fixed_charge`, `gc_total`.

- **`manifest.json`**: version bumped to `0.8.0`.
- **`README.md`**: added Common Expenses entity table (8 entities).

## [0.7.16] - 2026-03-19

### Fixed
- **Default device name for Aguas Andinas water accounts** (`service_detector.py`):

  The `aguas?\s+andinas?` service pattern used `"Aguas Andinas"` as its
  display name instead of the generic service-type label `"Agua"` that all
  other water-utility patterns already use.  On first configuration this
  produced a device named "Aguas Andinas" rather than the expected "Agua".

  **Fix**: changed the display name in `SERVICE_PATTERNS` from
  `"Aguas Andinas"` to `"Agua"` so all water accounts — regardless of
  provider — receive the same consistent default device name.

- **`manifest.json`**: version bumped to `0.7.16`.

## [0.7.15] - 2026-03-19

### Fixed
- **acepta.com Custodium PdfView "no plugin" download page** (`pdf_downloader.py`):

  When the acepta.com Custodium viewer is accessed without a PDF browser
  plugin, the `PdfView?url=ENCODED` endpoint returns an HTML page instead of
  the PDF.  This page contains a fallback download `<a>` link:

  ```html
  <a href="PdfView?url=http%3A%2F%2Fmetrogas2601.acepta.com%2Fv01%2F…&menuTitle=Boleta%20horizontal&xsl.full=false">
      Haga click aquí para descargar el archivo PDF.
  </a>
  ```

  Three bugs prevented the PDF URL from being extracted and the download from
  succeeding:

  1. **`_VIEWER_URL_PARAM_RE` truncated `viewer_path`** — the regex stopped
     capturing `viewer_path` at the first `&` after the encoded document URL,
     so extra rendering parameters like `&menuTitle=Boleta%20horizontal&
     xsl.full=false` were silently dropped.  `urllib.parse.urljoin` then
     produced the same plain `PdfView?url=ENCODED` URL that was already in
     the `seen` set from the previous hop, and the candidate was skipped.
     **Fix**: appended `[^"\'<>\s]*` to the regex after `encoded_url` so that
     `viewer_path` captures the full quoted string including all extra
     parameters.

  2. **Bare percent-encoded `href`** — some servers set the `href` attribute
     directly to the percent-encoded document URL (e.g.
     `href="http%3A%2F%2Fmetrogas2601.acepta.com%2Fv01%2F…"`).  Because
     `urllib.request.urlopen` cannot handle `http%3A` as a URL scheme, any
     such URL silently failed to download.
     **Fix**: added a `re.match(r'https?%3A', href)` check at the top of
     `_LinkExtractor.handle_starttag`; when it matches,
     `urllib.parse.unquote(href)` is applied immediately so all downstream
     code sees a proper `http://…` URL.

  3. **Download link text not recognised** — "Haga click aquí para descargar
     el archivo PDF" did not match any pattern in `_PDF_LINK_KEYWORDS` because
     the word `el` between `descargar` and `archivo` broke the adjacency
     requirement of the existing pattern
     `descarg…\s+(?:pdf|documento|archivo)`.
     **Fix**: added two new patterns:
     - `descarg(?:ar?|ue[ns]?)\s+(?:el\s+|este\s+|un\s+|su\s+|tu\s+)?(?:pdf|documento|archivo)` — covers "descargar el archivo", "descargue el documento", etc.
     - `haga\s+clic(?:k)?\s+aqu[ií]\s+para\s+descargar` — matches the
       exact acepta.com "Haga click aquí para descargar" button label.
     - `click\s+here\s+to\s+download` — English equivalent.

  Additionally, `_MAX_HTML_DEPTH` was raised from `2` to `3` to accommodate
  the full acepta.com download chain (outer wrapper page → Custodium JS
  page → PdfView HTML page → PDF bytes), ensuring that each HTML hop can
  still recurse to the next level without exhausting the budget.

- **`manifest.json`**: version bumped to `0.7.15`.

## [0.7.14] - 2026-03-19

### Fixed
- **acepta.com Custodium viewer PDF download** (`pdf_downloader.py`):

  When a fidelizador.com tracking link redirects to an acepta.com billing
  viewer, the download chain passes through **two** pages that contain no
  absolute HTTP URLs:

  1. **Outer wrapper page** — contains an `<iframe>` with a *root-relative*
     `src` attribute:
     ```
     <iframe src="/ca4webv3/index.jsp?url=http%3A%2F%2Fmetrogas2601.acepta.com%2Fv01%2F<HASH>%3Fk%3D<TOKEN>">
     ```
     The underlying document URL is percent-encoded inside the `url=` query
     parameter.

  2. **Custodium JavaScript page** ("Custodium Plugin - Desplegar Documento")
     — loaded by the iframe above; contains JavaScript variables that carry
     the PDF render path as a relative string:
     ```
     var PDFView = "PdfView?url=http%3A%2F%2Fmetrogas2601.acepta.com%2Fv01%2F<HASH>%3Fk%3D<TOKEN>";
     ```

  Because all previously existing URL-extraction strategies required
  absolute `http://` URLs, both pages produced zero candidates and the
  download silently failed.

  The fix introduces the following additions to `pdf_downloader.py`:

  1. **`_VIEWER_URL_PARAM_RE`** — a module-level compiled regex that matches
     any quoted path string (relative, root-relative, or absolute) whose
     query string contains `url=http%3A%2F%2F…` (a percent-encoded HTTP
     URL).  This generalised pattern covers both the `PdfView?url=…`
     JavaScript variable and the `/ca4webv3/index.jsp?url=…` iframe src.

  2. **`_find_viewer_url_params_in_html(html, base_url)`** — a new helper
     that scans the page for every `_VIEWER_URL_PARAM_RE` match and returns
     two candidate URLs per unique match:
     - **Resolved viewer URL** — the viewer path resolved against `base_url`
       via `urllib.parse.urljoin`.  For `PdfView?url=…` this is the absolute
       PDF render endpoint; for `/ca4webv3/index.jsp?url=…` it is the
       Custodium viewer page (one recursion hop away from the PDF).
     - **Decoded document URL** — `urllib.parse.unquote(encoded_url)`; the
       raw DTE document URL tried as a direct fallback.

  3. **`_try_html_redirect_download` — priority tier 4** — after the three
     existing tiers, `_find_viewer_url_params_in_html` is called with
     `original_url` as the base so that the resolved viewer URL and decoded
     document URL are added to the candidate list.

  Additionally, two improvements for other viewer-page scenarios are included:

  - **`_LinkExtractor.get_embedded_urls()`** — new method that returns all
    URLs from `<iframe>`, `<frame>`, `<embed>`, `<object>`, and `<form>`
    tags without billing-keyword filtering.

  - **`_find_embedded_urls_in_html(html)`** — new helper that uses
    `get_embedded_urls()` to return every absolute embedded-resource URL
    regardless of URL content.  Used as priority tier 3 in
    `_try_html_redirect_download` to catch PDFs served from generic CDN
    or opaque token URLs that contain no billing-related terms.

  - **`urllib.parse` import** added.

- **`manifest.json`**: version bumped to `0.7.14`.

## [0.7.13] - 2026-03-19

### Fixed
- **Metrogas/fidelizador.com bill URL — raw-line reconstruction replaces
  decoded-text search** (`pdf_downloader.py`):

  The previous strategy (`_find_fidelizador_href_in_plain_text` v0.7.12)
  decoded the plain-text MIME part first (via `get_payload(decode=True)` +
  `_decode_qp_if_needed`) and then searched the decoded string for the URL
  after the `[image: Ver boleta]` marker.  In practice the URL reconstruction
  was unreliable for some email configurations, causing the wrong
  `trackercl1.fidelizador.com` tracking URL (e.g. an unsubscribe link) to be
  returned instead of the bill-download URL.

  The fix replaces the decoded-text search with a **raw-line algorithm** that
  operates directly on the undecoded payload (`get_payload(decode=False)`),
  preserving the Quoted-Printable soft line-break (`=\n`) that splits the URL
  across two consecutive lines:

  ```
  [image: Ver boleta]
  <https://trackercl1.fidelizador.com/…PART1=
  …PART2>
  ```

  Reconstruction:
  1. Locate the line containing `[image: Ver boleta]`.
  2. Take the **two lines immediately following** it.
  3. Remove `<`, `>`, and `=` from each line and strip whitespace.
  4. Concatenate the two fragments — the result is the full URL.

  This produces the correct URL regardless of whether the email has a
  `Content-Transfer-Encoding: quoted-printable` header or not, and without
  any dependency on `quopri.decodestring` or BeautifulSoup.

  Additionally, the BeautifulSoup fallback (`_find_fidelizador_href_via_bs4`)
  is removed entirely, along with the `beautifulsoup4` dependency from
  `manifest.json`.  The `_VER_BOLETA_RE` regex constant (used only by the
  removed function) is also removed.

  Summary of changes:
  - `_find_fidelizador_href_in_plain_text`: rewritten to use raw-line
    algorithm on `get_payload(decode=False)`.
  - `_find_fidelizador_href_via_bs4`: removed.
  - `_VER_BOLETA_RE`: removed.
  - `BeautifulSoup` import removed.
  - `download_pdf_from_email`: BeautifulSoup fallback block removed; if
    plain-text extraction fails the code falls through directly to the
    existing HTML keyword-extraction path (Attempt 2b).

- **`manifest.json`**: `beautifulsoup4` removed from `requirements`; version
  bumped to `0.7.13`.

## [0.7.12] - 2026-03-19

### Changed
- **Metrogas/fidelizador.com bill URL — primary strategy switched to plain-text
  `[image: Ver boleta]` marker** (`pdf_downloader.py`):

  The previous primary strategy (`_find_fidelizador_href_via_bs4`) searched
  the QP-decoded HTML body for `<img alt="Ver boleta">` using BeautifulSoup.
  While structurally sound, this approach was unreliable in practice because
  the URL reconstruction from the HTML body was incorrect.

  The new primary strategy (`_find_fidelizador_href_in_plain_text`) searches
  the QP-decoded **plain-text** body for the `[image: Ver boleta]` marker.
  Email clients such as Gmail render image-only HTML anchors as plain-text
  `[image: alt_text] <URL>` sequences, making this marker a reliable
  anchor-free indicator for the bill-download button.  The function locates
  this marker via `_IMAGE_VER_BOLETA_PLAIN_RE` and extracts the
  `https://trackercl1.fidelizador.com/` URL that follows it within a
  500-character window.

  The BeautifulSoup HTML approach (`_find_fidelizador_href_via_bs4`) is
  retained as a fallback when the plain-text body does not contain the
  expected marker.

  The `download_pdf_from_email` Strategy 2 download block was also
  refactored so that a `fidelizador_href_url` found via the plain-text path
  is downloaded even when the email has no HTML body.

  Summary of changes:
  - `_IMAGE_VER_BOLETA_PLAIN_RE` module-level compiled regex added.
  - `_find_fidelizador_href_in_plain_text` added.
  - `download_pdf_from_email`: plain-text search runs first; BeautifulSoup
    is demoted to fallback.
  - Strategy 2 download block decoupled from `if html_body:` guard.

- **`manifest.json`**: Version bumped to `0.7.12`.

## [0.7.11] - 2026-03-19

### Changed
- **Metrogas/fidelizador.com bill URL — raw QP-byte regex replaced with BeautifulSoup**
  (`pdf_downloader.py`):

  The Metrogas billing email contains multiple `trackercl1.fidelizador.com`
  click-tracking URLs (social-media icons, account-management buttons, the
  bill-download button, and footer/unsubscribe links).  The previous approach
  (`_find_fidelizador_href_in_html_qp`) selected the bill URL by searching a
  200-byte context window in **raw QP bytes** for billing keywords, relying on
  `_FIDELIZADOR_BILLING_CONTEXT_RE` to account for soft line-breaks (`=\r\n`)
  and QP-encoded characters such as `=3D`.

  The bill-download button is structurally unambiguous: it is always the
  anchor that wraps `<img alt="Ver boleta">`.  The new
  `_find_fidelizador_href_via_bs4` function uses **BeautifulSoup** on the
  already QP-decoded HTML (produced by `_get_html_body` /
  `_decode_qp_if_needed`) to locate this exact element — walking every
  `<a href>` tag and returning the first whose child `<img>` has `alt`
  matching `"Ver boleta"` (case-insensitive via `_VER_BOLETA_RE`) and whose
  `href` starts with `https://trackercl1.fidelizador.com/`.

  Because QP decoding runs before BeautifulSoup, the soft line-break
  (`=\r\n`) that splits long URLs in raw bytes is already removed, and
  `=3D` has been turned back into `=` — BeautifulSoup sees the complete,
  clean `href` with no regex required.

  Summary of changes:
  - `_find_fidelizador_href_in_html_qp` removed.
  - `_find_fidelizador_href_via_bs4` added.
  - `_VER_BOLETA_RE` module-level compiled regex added.

- **`manifest.json`**: `beautifulsoup4>=4.12.3` added to `requirements`;
  version bumped to `0.7.11`.

## [0.7.10] - 2026-03-19

### Changed
- **PDF URL storage redesign — `pdf_url` attribute populated before
  cached-file early return** (`pdf_downloader.py`):

  Previously, when the target PDF was already present on disk,
  `download_pdf_from_email` returned early after reading the download URL
  from a companion `.url` file that had been written alongside the PDF on
  the first download.  This meant `attributes["pdf_url"]` was unavailable
  whenever the companion file was missing or had been deleted.

  URL extraction (fidelizador/BeautifulSoup path and regular HTML link scan)
  is now performed **before** the cached-file check, so
  `attributes["pdf_url"]` is always populated directly from the current
  email body regardless of whether the PDF is already on disk.  The
  companion `.url` file mechanism (`_save_url_companion`) has been removed
  entirely.

  Affected call sites:
  - `download_pdf_from_email`: URL extraction and `attributes["pdf_url"]`
    assignment moved before `if os.path.exists(dest_path)`.
  - `_download_first_valid_pdf`: `_save_url_companion` call removed.
  - `purge_old_pdfs`: no longer removes `*.url` companion files.

- **`manifest.json`**: Version bumped to `0.7.10`.

## [0.7.9] - 2026-03-19

### Fixed
- **Gas service PDF download URL — image-only "Ver boleta" button not matched**
  (`pdf_downloader.py`):

  Metrogas billing emails deliver the "Ver boleta" button as an **image-only
  anchor** — the visible label is a PNG image (`boleta.png`) rather than
  plain text.  The billing-context pattern (`_FIDELIZADOR_BILLING_CONTEXT_RE`)
  previously contained only Spanish/English text keywords (e.g. "ver boleta",
  "descargar boleta"), so the button was never matched and the code fell back
  to the **last** fidelizador.com URL in the HTML, which is a
  footer/unsubscribe link pointing to a different resource.

  The fix adds `boleta(?:=\r?\n)?\.png` to the billing-context regex.  This
  pattern matches the `<img src="…/boleta.png">` tag that is nested inside
  the billing anchor — the image tag is within the context window (from the
  href to the closing `</a>`), so the correct "Ver boleta" URL is now
  selected instead of the footer fallback.  The optional `=\r?\n` allows for
  a QP soft line-break between "boleta" and ".png" in the raw QP-encoded
  email body.

- **`manifest.json`**: Version bumped to `0.7.9`.

## [0.7.8] - 2026-03-18

### Changed
- **Stale-data Problem detection in `binary_sensor.concierge_{service}_status`**
  (`binary_sensor.py`):

  The status binary sensor now reports **Problem** (`is_on = True`) in an
  additional scenario: when the most recently processed bill
  (`sensor.concierge_{service}_last_update`) is **older than one calendar
  month**.  The previous behaviour only flagged a Problem when no bill data
  had ever been found.

  | Condition | State |
  |---|---|
  | No coordinator data | Problem |
  | No bill data for service | Problem |
  | `last_update` is `None` | Problem |
  | `last_update` older than 1 calendar month | **Problem** *(new)* |
  | `last_update` within the last month | OK |

  Calendar-month arithmetic uses `dateutil.relativedelta` (a core HA
  dependency) to handle month-boundary edge cases correctly.  Timezone-naive
  datetimes are safely normalised to UTC before comparison.

- **`requirements.txt`**: `types-python-dateutil` added as a dev dependency
  so that `mypy` can type-check the new `dateutil.relativedelta` import
  without raising `[import-untyped]`.

- **`manifest.json`**: Version bumped to `0.7.8`.

## [0.7.7] - 2026-03-18

### Changed
- **Electricity service — billing charge fields promoted to dedicated sensor entities**
  (`sensor.py`, `binary_sensor.py`):

  The following fields, previously bundled as attributes on
  `binary_sensor.concierge_{service}_status`, are now individual sensor entities:

  | New entity | Attribute replaced | Unit |
  |---|---|---|
  | `sensor.concierge_{service}_service_administration` | `service_administration` | `$` |
  | `sensor.concierge_{service}_electricity_transport` | `electricity_transport` | `$` |
  | `sensor.concierge_{service}_stabilization_fund` | `stabilization_fund` | `$` |
  | `sensor.concierge_{service}_electricity_consumption` | `electricity_consumption` | `$` |

  All four are backed by the now-generic `ConciergeServiceBillingBreakdownSensor`
  class (renamed from `ConciergeWaterSpecificSensor`, which was already fully
  parameterised).

- **`ConciergeWaterSpecificSensor` → `ConciergeServiceBillingBreakdownSensor`**
  (`sensor.py`): class renamed to reflect its use for both water and electricity
  billing breakdowns.

- **`manifest.json`**: Version bumped to `0.7.7`.

## [0.7.6] - 2026-03-18

### Changed
- **Water service — water billing fields promoted to dedicated sensor entities**
  (`sensor.py`, `binary_sensor.py`):

  The 11 water-specific billing fields that were previously bundled as
  attributes on `binary_sensor.concierge_{service}_status` are now exposed as
  individual sensor entities:

  | New entity | Attribute replaced | Unit |
  |---|---|---|
  | `sensor.concierge_{service}_fixed_charge` | `fixed_charge` | `$` |
  | `sensor.concierge_{service}_cost_per_unit_peak` | `cubic_meter_peak_water_cost` | `$/m³` |
  | `sensor.concierge_{service}_cost_per_unit_non_peak` | `cubic_meter_non_peak_water_cost` | `$/m³` |
  | `sensor.concierge_{service}_cubic_meter_overconsumption` | `cubic_meter_overconsumption` | `$/m³` |
  | `sensor.concierge_{service}_cubic_meter_collection` | `cubic_meter_collection` | `$/m³` |
  | `sensor.concierge_{service}_cubic_meter_treatment` | `cubic_meter_treatment` | `$/m³` |
  | `sensor.concierge_{service}_water_consumption` | `water_consumption` | `$` |
  | `sensor.concierge_{service}_wastewater_recolection` | `wastewater_recolection` | `$` |
  | `sensor.concierge_{service}_wastewater_treatment` | `wastewater_treatment` | `$` |
  | `sensor.concierge_{service}_subtotal` | `subtotal` | `$` |
  | `sensor.concierge_{service}_other_charges` | `other_charges` | `$` |

  The generic `sensor.concierge_{service}_cost_per_unit` sensor is **not**
  created for water service subentries; the new `cost_per_unit_peak` and
  `cost_per_unit_non_peak` sensors replace it entirely.

- **`sensor.concierge_{service}_last_update` — relative-time display** (`sensor.py`):

  The sensor now carries `device_class: timestamp` and returns a native
  `datetime` object instead of an ISO-format string.  The Home Assistant
  frontend automatically renders this as a locale-aware relative time string
  ("hace 2 días", "hace 1 semana", "2 days ago", etc.) in the user's
  configured language.

- **`manifest.json`**: Version bumped to `0.7.6`.



### Fixed
- **mypy type error in `_find_fidelizador_href_in_html_qp`** (`pdf_downloader.py`):

  `get_payload(decode=True)` returns `bytes | str | None`.  Mypy therefore
  typed the `payload` variable as `bytes | str | None`, which caused a type
  error on the `_FIDELIZADOR_BILLING_CONTEXT_RE.search(context_window)` call
  because `Pattern[bytes].search()` requires a `Buffer` (bytes-like object),
  not `str`.

  **Fix:** an `isinstance(payload, bytes)` guard is added immediately after
  the `if not payload` early-exit.  This narrows the type to `bytes` for the
  rest of the loop body, eliminating the error and removing the three
  `# type: ignore` suppressions that were masking the same underlying issue
  on the `finditer`, `find`, `min`, and slice calls.

- **`manifest.json`**: Version bumped to `0.7.5`.

## [0.7.4] - 2026-03-18

### Fixed
- **Gas bill PDF download — wrong fidelizador.com URL selected** (`pdf_downloader.py`):

  `_find_fidelizador_href_in_html_qp` was choosing the **last**
  `trackercl1.fidelizador.com` URL in the raw QP HTML body, based on the
  incorrect assumption that the "Ver boleta" download button always appears
  last.  In practice Metrogas emails contain footer/unsubscribe tracking links
  **after** the bill-download button, so the last URL is always a footer link,
  not the billing button.

  **Fix:** The function now identifies the correct URL by searching for billing
  keywords (`ver boleta`, `descargar boleta`, `ver factura`, etc.) within a
  bounded context window in the raw HTML bytes around each candidate URL
  (200 bytes before the `href=3D"` + the link text up to the closing `</a>`).
  The **first URL whose context contains a billing keyword** is returned as
  the preferred bill-download URL.  Bounding the window at the closing `</a>`
  prevents the window from bleeding into the link text of the *next* anchor
  and falsely matching social-media or footer links.  When no billing-context
  match is found the previous last-URL logic is kept as a fallback.

  New module-level constant `_FIDELIZADOR_BILLING_CONTEXT_RE` (bytes pattern)
  encodes the same keyword set as `_PDF_LINK_KEYWORDS`, adapted for matching
  against raw QP-encoded HTML bytes.

- **`manifest.json`**: Version bumped to `0.7.4`.

## [0.7.3] - 2026-03-18

### Fixed
- **Spurious warnings eliminated from PDF downloader** (`pdf_downloader.py`):
  Five intermediate-probe log messages that were emitted at `WARNING` level
  (and therefore appeared in the Home Assistant issue/error panel) have been
  downgraded to `DEBUG`.  These messages represent **expected** failures while
  the downloader heuristically probes multiple candidate URLs:

  - `"URL … returned HTML but no redirect target or billing URL found"` —
    emitted when a click-tracker page (e.g. fidelizador.com) does not contain
    a recognisable redirect or billing link.
  - `"URL error fetching …"` — emitted when a candidate URL inside an HTML
    redirect page returns a network error (e.g. HTTP 404/400).
  - `"Candidate … did not return a PDF"` — emitted when a URL fetched from an
    HTML page returns a non-PDF content type (e.g. a CDN JS file or a video).
  - `"URL … did not return a PDF"` — same as above, in the top-level candidate
    loop.
  - `"URL error downloading …"` — emitted when a top-level candidate URL
    returns an HTTP error (404, 400, etc.).

  Real disk-write failures (`OSError`) continue to be logged at `WARNING`
  level since they represent genuine operational problems.

- **`manifest.json`**: Version bumped to `0.7.3`.

## [0.7.2] - 2026-03-18

### Fixed
- **`sensor.concierge_*_last_update` entity category changed from `CONFIG` to
  `DIAGNOSTIC`** (`sensor.py`): Home Assistant does not allow sensor entities to
  use `EntityCategory.CONFIG`; attempting to register them raised a
  `HomeAssistantError` and the sensors were never added.  The category has been
  corrected to `EntityCategory.DIAGNOSTIC`, which is the appropriate category for
  informational read-only sensors.

- **`manifest.json`**: Version bumped to `0.7.2`.

## [0.7.1] - 2026-03-18

### Changed
- **`sensor.concierge_*_last_update` now holds the full datetime** (`sensor.py`):
  `ConciergeServiceLastUpdateSensor.native_value` now returns the full
  ISO 8601 datetime string (``last_updated.isoformat()``) instead of only the
  date portion (``last_updated.date().isoformat()``).  This is the information
  that was previously exposed as the ``last_updated_datetime`` attribute on the
  companion binary sensor.

- **`last_updated_datetime` attribute removed from status binary sensor**
  (`binary_sensor.py`): The ``last_updated_datetime`` attribute has been dropped
  from ``binary_sensor.concierge_*_status``.  The equivalent information is now
  available directly as the state value of ``sensor.concierge_*_last_update``.

- **`manifest.json`**: Version bumped to `0.7.1`.

### Fixed
- **`pdf_url` attribute now populated after PDF is cached** (`pdf_downloader.py`):
  When the bill PDF was already present on disk from a previous coordinator
  cycle, ``download_pdf_from_email()`` returned early without setting
  ``attributes["pdf_url"]``, causing the attribute to be empty on every run
  after the first download.

  The fix introduces a companion ``.url`` file (``{pdf_path}.url``) that is
  written alongside the PDF whenever a successful URL-based download completes.
  On subsequent cycles the early-return path reads the companion file and
  restores ``attributes["pdf_url"]`` so the sensor always exposes the correct
  download URL.  ``purge_old_pdfs()`` is updated to also delete companion
  ``.url`` files when the associated PDF is purged.

## [0.7.0] - 2026-03-18

### Added
- **`binary_sensor.py`** (new file): `ConciergeServiceStatusBinarySensor` — a
  `BinarySensorDeviceClass.PROBLEM` entity with `EntityCategory.DIAGNOSTIC`.
  One status binary sensor is created per service sub-entry and appears in the
  HA device Diagnostic panel.  `is_on = True` means no bill data was found for
  that service; `is_on = False` means data was retrieved successfully.  All
  attributes that were not promoted to dedicated sensors (folio, billing period,
  address, due date, service-type-specific fields, ``pdf_path``) are retained
  here as `extra_state_attributes`.

- **New per-service sensor entities** (`sensor.py`): The single monolithic
  service sensor has been replaced by four focused sensors per sub-entry,
  aligned with Home Assistant device conventions:

  | Entity ID | Category | Value |
  |---|---|---|
  | `sensor.concierge_{service_id}_last_update` | Diagnostic | Last bill date (ISO 8601) |
  | `sensor.concierge_{service_id}_consumption` | — | m³ (gas/water) or kWh (electricity) |
  | `sensor.concierge_{service_id}_cost_per_unit` | — | $/m³ (gas) or $/kWh (electricity); `None` for water/unknown |
  | `sensor.concierge_{service_id}_total_amount` | — | Total bill amount (`$`) |

  These are implemented as a `_ConciergeServiceBaseSensor` base class plus
  four subclasses: `ConciergeServiceLastUpdateSensor`,
  `ConciergeServiceConsumptionSensor`, `ConciergeServiceCostPerUnitSensor`,
  and `ConciergeServiceTotalAmountSensor`.

### Changed
- **`__init__.py`**: The shared `ConciergeServicesCoordinator` is now
  initialised (including its first refresh) inside `async_setup_entry`,
  *before* `async_forward_entry_setups` is called.  This eliminates the race
  condition that existed when both the `sensor` and `binary_sensor` platforms
  tried to create the coordinator independently.  Both platforms now read the
  coordinator from `hass.data[DOMAIN][entry_id]["coordinator"]`.

- **`__init__.py`**: `PLATFORMS` list extended from `["sensor"]` to
  `["sensor", "binary_sensor"]`.

- **`sensor.py`**: `async_setup_entry` now reads the pre-initialised
  coordinator from `hass.data` instead of creating its own instance.
  `ConciergeServiceSensor` (the old monolithic sensor) has been removed.

- **`config_flow.py`** / **`manifest.json`**: `MINOR_VERSION` bumped to `3`,
  integration version updated to `0.7.0`.

### Removed
- **Legacy single-service sensor** (`sensor.py`): `ConciergeServiceSensor`
  and its single-entity-per-service approach have been removed.  Existing
  installations are migrated automatically via `_migrate_1_2_to_1_3`, which
  removes all stale `sensor.concierge_services_*` entities from the entity
  registry on upgrade (schema version 1.2 → 1.3).

## [0.6.16] - 2026-03-18

### Fixed
- **``pdf_url`` attribute empty in both sensors** (`pdf_downloader.py`):
  Previously the ``pdf_url`` sensor attribute was only populated when the
  fidelizador.com bill download URL was found in the raw Quoted-Printable HTML
  (strategy 2a, Metrogas emails).  For all other download paths — HTML link
  extraction (strategy 2b) and plain-text URL scan (strategy 3) — the
  attribute was never set and always remained ``""``.

  ``_download_first_valid_pdf()`` now accepts an optional *attributes* dict
  parameter.  Whenever a URL in the candidate list yields a valid PDF, the
  function stores that URL in ``attributes["pdf_url"]``.  All three call sites
  inside ``download_pdf_from_email()`` pass the shared *attributes* dict, so
  ``pdf_url`` is populated regardless of which strategy produces the download.

- **Wrong PDF downloaded for gas service** (`pdf_downloader.py`):
  The previous ``_HTTP_USER_AGENT`` value identified the client as a Home
  Assistant custom integration.  Click-tracking servers such as
  *fidelizador.com* inspect the User-Agent header and may return a different
  document (or an error page) when they detect a non-browser client, causing
  the wrong PDF to be saved.

  ``_HTTP_USER_AGENT`` is now set to a standard Mozilla/Chrome browser
  string::

      Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
      (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36

  This causes the fidelizador.com tracker to follow its normal browser
  redirect chain and serve the correct bill PDF.

## [0.6.15] - 2026-03-18

### Added
- **``pdf_url`` attribute for electricity sensor** (`sensor.py`):
  The ``pdf_url`` attribute (introduced for the gas sensor in v0.6.14) is now
  also available on the electricity-service sensor.  It defaults to ``""`` and
  is overridden with the bill download URL whenever a matching email is
  processed and a fidelizador.com URL is reconstructed.

## [0.6.14] - 2026-03-18

### Fixed
- **Gas bill PDF download — definitive fix for incorrect URL selection**
  (`pdf_downloader.py`, `sensor.py`):
  The v0.6.13 strategy used the *first*
  ``trackercl1.fidelizador.com`` URL found in the raw QP bytes, which turned
  out to be the "Ver en el navegador" view-in-browser link from the
  ``text/plain`` part — not the actual bill download button URL.  Following
  that URL yielded the wrong document.

  The definitive fix introduces a new helper
  ``_find_fidelizador_href_in_html_qp()`` that targets exclusively the
  ``text/html`` part and searches the raw QP bytes for
  ``href=3D"https://trackercl1.fidelizador.com/…"`` attributes (the
  Quoted-Printable encoding of ``href="…"``).  Among all matching ``<a href>``
  tags the function returns the **last** one in document order: social-media
  icon links and account-management buttons appear earlier in the HTML body,
  while the bill download button's ``<a>`` element is the last billing CTA.

  URL reconstruction follows the approach required by the problem specification:

  1. Locate the div containing the bill download ``<a>`` element.
  2. Extract the raw bytes of the ``href`` value from ``href=3D"…"``, spanning
     any ``=\\r?\\n`` soft line-breaks.
  3. Apply ``quopri.decodestring()`` to remove soft line-breaks and decode
     ``=XX`` hex codes, yielding the clean URL.

  This is now the **sole** authoritative method for reconstructing the gas bill
  PDF URL from Metrogas / fidelizador.com emails; all previous approaches
  (plain-text part scan, general HTML link extraction) have been shown to
  return incorrect URLs and are no longer used for this purpose.

  Additionally:

  - ``download_pdf_from_email()`` stores the reconstructed URL in
    ``attributes["pdf_url"]`` before attempting the download, making the URL
    available even if the download fails.
  - A new **``pdf_url``** attribute is added to the gas-service sensor
    (``_GAS_ATTR_DEFAULTS``).  It defaults to ``""`` and is overridden with the
    actual reconstructed URL whenever a Metrogas / fidelizador.com email is
    processed.

## [0.6.13] - 2026-03-17

### Fixed
- **Gas bill PDF download — wrong PDF selected when multiple fidelizador.com
  links present** (`pdf_downloader.py`):
  Metrogas billing emails delivered by *fidelizador.com* are Quoted-Printable
  encoded **without** a ``Content-Transfer-Encoding`` header.  They contain
  **multiple** ``trackercl1.fidelizador.com`` click-tracking URLs: one per
  interactive element (social-media icons, account-management buttons, and the
  bill download button).  Because all share the same domain, the
  ``_PDF_HREF_KEYWORDS`` matcher could not distinguish them, and the code was
  following whichever URL appeared first in the decoded HTML — often a
  social-media or account link — and downloading whatever PDF that redirect
  chain produced (which was *not* the gas bill).

  The tracker URL can be found in **two places** in the raw email bytes:

  1. **Plain-text part** — as an RFC 2396 angle-bracket link at the very top
     of the body (the "Ver en el navegador" view-in-browser reference)::

         Ver en el navegador
         <https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807CB3HF4=
         E1ADBBCEJA9FFC4CD0B3A58A829KF1C34750AD1337D0DF097F8513787E299F30>

     This is the *first* occurrence and the most reliable one: the plain-text
     part has only one ``trackercl1.fidelizador.com`` URL at the top, before
     any social-media or account links.

  2. **HTML part** — as a ``href=3D"URL"`` attribute on the image-only bill
     download button ``<a>`` element, also potentially split by a soft
     line-break.  This occurrence can serve as confirmation that the URL
     is correctly reconstructed.

  In both cases the URL may be split across two QP lines by a soft line-break
  (``=\r?\n``) that must be removed to reconstruct the full URL.

  The fix adds:

  1. **`_FIDELIZADOR_URL_RE` (new regex constant)** — matches
     ``https://trackercl1.fidelizador.com/`` followed by the opaque
     alphanumeric token, spanning QP soft line-breaks (``=\r?\n``) and
     optional QP hex codes (``=XX``).  ``=`` is excluded from the ordinary-
     character class so the engine always uses a QP alternative when it
     encounters ``=``, ensuring no truncation at soft line-breaks.

  2. **`_find_fidelizador_links_in_raw_qp_parts()` (new helper)** — walks
     all MIME parts in document order (``text/plain`` before ``text/html``
     in a standard ``multipart/alternative`` message), skips any part whose
     ``Content-Transfer-Encoding`` header is ``quoted-printable`` (Python
     has already decoded those), checks for QP soft line-breaks as the
     indicator that raw QP processing is needed, and applies
     ``_FIDELIZADOR_URL_RE`` + ``quopri.decodestring()`` to extract and
     reconstruct the full URL.  Because the plain-text part is walked first,
     the first URL returned is the view-in-browser link from the plain-text
     body — the correct one to follow.

  3. **`download_pdf_from_email()` — new attempt 2a** — before the existing
     keyword / ``.pdf`` / billing-term HTML link extraction (now *attempt
     2b*), the function calls ``_find_fidelizador_links_in_raw_qp_parts()``
     and, if any URL is returned, tries to download the PDF from those URLs
     first.  The existing HTML parsing falls through as a fallback if no
     fidelizador.com URL is found in the raw QP bytes or the download fails.

## [0.6.12] - 2026-03-17

### Fixed
- **Gas bill PDF download — Quoted-Printable email body without CTE header**
  (`pdf_downloader.py`): Metrogas billing emails delivered by *fidelizador.com*
  are Quoted-Printable encoded.  In a standards-compliant email the MIME part
  carries a ``Content-Transfer-Encoding: quoted-printable`` header, which
  causes Python's ``email`` library to decode the QP bytes automatically when
  ``get_payload(decode=True)`` is called.  However, some fidelizador.com
  messages omit this header even though the body is fully QP-encoded.  Without
  the header, ``get_payload(decode=True)`` returns the raw bytes unchanged,
  so the HTML body contains:

  * ``=3D`` instead of ``=`` in HTML attribute assignments — for example the
    anchor tag appears as ``<a href=3D"https://trackercl1.fidelizador.com/…">``.
    ``HTMLParser`` treats the ``=`` immediately after ``href`` as the
    attribute-assignment operator and parses ``3D"https://…"`` as an
    *unquoted* attribute value, yielding a ``href`` value of
    ``3D"https://trackercl1.fidelizador.com/IF1C347GA9EF1E79E1807=``
    instead of the correct URL — which never starts with ``http`` and is
    therefore silently discarded.
  * ``=\n`` (QP soft line-break) splitting the tracking URL across two lines
    — the second fragment (``CB3HF4E1ADBBCE…``) is parsed as a separate,
    meaningless attribute and the first fragment is truncated at ``=``.

  A new private helper ``_decode_qp_if_needed()`` is called immediately after
  ``get_payload(decode=True)`` in both ``_get_html_body()`` and
  ``_get_plain_text_body()``.  It checks whether the raw payload contains
  ``=\n`` or ``=\r\n`` (QP soft line-breaks) — an unambiguous indicator of
  QP-encoded content — and, if so, applies ``quopri.decodestring()`` to strip
  the encoding before the bytes are decoded as a character set and passed to
  the HTML/text parsers.  The helper is a no-op when the ``Content-Transfer-
  Encoding`` header is already ``quoted-printable`` (Python has decoded the
  bytes) or when the payload contains no soft line-breaks.

## [0.6.11] - 2026-03-17

### Fixed
- **Gas bill PDF download — acepta.com depot URL extraction** (`pdf_downloader.py`):
  The acepta.com document depot URL
  (`http://metrogas{YYMM}.acepta.com/depot/{hash}?k={token}`) that the
  Metrogas bill viewer opens is reachable from the email via two paths that
  the previous code did not cover:

  1. **`<iframe src>` / `<embed src>` / `<object data>` / `<form action>`
     extraction in `_LinkExtractor`** — the acepta.com viewer URL often
     appears as an inline-frame source or embedded-object ``data`` attribute
     in the email HTML or in the HTML page served by the fidelizador.com
     click-tracker.  `_LinkExtractor` previously only inspected ``<a href>``
     tags; it now also records HTTP(S) URLs from these four element types
     with an empty text label, so they are classified purely by URL content
     (tier 3 — matches ``acepta\.com`` in `_PDF_HREF_KEYWORDS`).

  2. **`_try_html_redirect_download` — full billing-URL scan as fallback** —
     when the fidelizador.com tracker page uses an *indirect* JavaScript
     redirect (``var u = "https://…"; window.location.href = u;``) the
     ``_extract_url_from_html_redirect`` helper returns ``None`` because it
     only matches *direct string literals*.  The acepta.com URL is, however,
     present in the ``<script>`` block as a string and is already found by
     ``_find_urls_in_script_tags`` → ``_find_pdf_links_in_html``.
     ``_try_html_redirect_download`` now builds a combined candidate list
     from **both** ``_extract_url_from_html_redirect`` (priority 1) **and**
     ``_find_pdf_links_in_html`` (priority 2 — covers ``<a href>``,
     ``<iframe src>``, ``<script>`` variables, etc.), and tries each in
     order.

  3. **Depth-limited recursive HTML following** — the two-hop chain
     *fidelizador.com → acepta.com viewer → PDF download link* is now
     resolved automatically.  ``_try_html_redirect_download`` accepts a
     ``_depth`` parameter and recurses up to two levels deep, so if the
     acepta.com viewer itself returns HTML with a *"Descargar PDF"* link or
     an embedded iframe pointing at the actual PDF bytes, that final hop is
     also followed.  The depth cap (``_MAX_HTML_DEPTH = 2``) prevents
     infinite loops.

## [0.6.10] - 2026-03-17

### Fixed
- **Gas bill PDF download — HTML click-tracking redirects** (`pdf_downloader.py`):
  Metrogas emails delivered via the *fidelizador.com* platform embed billing
  links as click-tracking URLs (e.g.
  ``https://trackercl1.fidelizador.com/…``).  When the code fetched such a
  URL it received an HTML page (``Content-Type: text/html``) instead of a
  PDF, because the tracking server records the click and then redirects the
  browser client-side rather than via an HTTP 301/302 response that
  ``urllib`` would have followed automatically.

  Two complementary changes address this:

  1. **HTML redirect follower in `_download_first_valid_pdf()`** — when a
     fetched URL returns an HTML page, the code now inspects the HTML for
     common client-side redirect mechanisms and, if one is found, fetches
     the redirect target and validates it as a PDF before writing it to
     disk.  Two redirect patterns are recognised:

     - ``<meta http-equiv="refresh" content="N; url=…">``
     - JavaScript ``window.location.href = '…'`` /
       ``location.replace('…')`` / ``location.assign('…')`` assignments
       inside ``<script>`` blocks.

     A single redirect hop is followed per candidate URL to prevent loops.

  2. **`fidelizador.com` added to `_PDF_HREF_KEYWORDS`** — ensures that
     fidelizador.com tracking URLs discovered in email bodies (``<a href>``
     or plain-text) are always classified as billing-related candidates and
     included in the download attempt list, even when the URL path itself
     carries no recognisable billing keyword.

## [0.6.9] - 2026-03-17

### Fixed
- **Gas bill PDF download — JavaScript URL injection** (`pdf_downloader.py`):
  Metrogas emails (sent via the acepta.com platform) embed the real document
  URL inside a JavaScript variable in the email ``<head>`` and set the
  visible ``<a href>`` dynamically at browser render time.  Because the
  Python email parser does not execute JavaScript, the ``href`` attribute
  remained a non-navigable placeholder (``#`` or ``javascript:…``) and the
  real URL was never discovered.

  Three complementary fixes address this:

  1. **`_find_urls_in_script_tags()` (new helper)** — walks every
     ``<script>…</script>`` block in the HTML body, extracts all
     ``http(s)://`` URLs, and classifies them into the same three-tier
     priority scheme used for ``<a href>`` links (keyword context match →
     ``.pdf`` suffix → billing-term / domain match).  The 200-character
     text surrounding each URL in the script is used as the context for
     keyword matching, so variable names such as ``boletaUrl`` or comments
     like ``// URL de la boleta`` promote the URL to the highest tier.

  2. **`acepta.com` added to `_PDF_HREF_KEYWORDS`** — the acepta.com domain
     is Chile's official electronic-document management portal; any URL on
     that domain is a billing document link.  This ensures acepta.com URLs
     found in ``<script>`` blocks (and in ``<a href>`` tags) are always
     picked up as tier-3 candidates even when the surrounding context
     carries no explicit billing keywords.

  3. **`_LinkExtractor` — `data-*` / `onclick` fallback** — when an ``<a>``
     tag has a non-HTTP ``href`` (``#``, ``javascript:…``), the extractor
     now also inspects ``data-url``, ``data-href``, ``data-link``,
     ``data-target``, ``data-action``, and ``onclick`` attributes for a
     real HTTP URL.  This covers email platforms that store the document URL
     in data attributes instead of (or in addition to) a script variable.

## [0.6.8] - 2026-03-17

### Added
- **PDF download logging** (`pdf_downloader.py`, `sensor.py`): Improved
  observability for the PDF download pipeline by elevating key log messages
  from DEBUG to INFO or WARNING level.  These messages are now visible in the
  standard Home Assistant log without enabling debug logging:

  - `INFO`  — PDF download started for each service, number of candidate URLs
    found in the HTML body and plain-text body, each HTTP fetch attempt URL,
    and successful downloads.
  - `INFO`  — "No PDF links found in HTML/plain-text body" when the email
    carries no recognisable billing links.
  - `WARNING` — Failed HTTP requests (network or URL errors), responses that
    are not a valid PDF (unexpected Content-Type / missing magic bytes, with
    first 16 bytes logged for diagnosis), and the final "No PDF could be
    obtained" outcome.
  - `WARNING` — PDF download exceptions that were previously swallowed at
    DEBUG level in the coordinator (`sensor.py`).

### Fixed
- **IMAP connection timeout** (`sensor.py`, `service_detector.py`): Added an
  explicit ``timeout=30`` (seconds) to all ``imaplib.IMAP4_SSL`` constructor
  calls.  Previously, if the IMAP server was unreachable or slow to respond
  the socket would block indefinitely, causing the integration setup to hang
  and triggering the Home Assistant bootstrap warning
  *"Waiting for integrations to complete setup"*.

## [0.6.7] - 2026-03-17

### Fixed
- **Gas bill PDF download** (`pdf_downloader.py`): The Metrogas email uses
  image-only ``<a href="…"><img alt="" …></a>`` buttons where the visible
  label ("Ver boleta") is a sibling text node placed **outside** the
  ``<a>`` tag rather than inside it.  The previous `_LinkExtractor`
  only collected text *inside* ``<a>`` elements, so these buttons were
  returned with an empty label and silently skipped by the keyword filter.

  `_LinkExtractor` now implements an *adjacent-text fallback*:

  - Text that appears **before** an ``<a>`` tag within the same container
    element is kept as the *preceding context*.
  - When a link closes with an empty label, the preceding context is used
    first; if that is also empty the link is marked *pending* and text that
    appears **after** the ``</a>`` (still within the same container) is
    accumulated as its label.
  - Context is reset at every block/container tag boundary
    (``<td>``, ``<tr>``, ``<div>``, ``<p>``, ``<table>``, etc.) so that
    text from unrelated table cells or sections is never incorrectly
    associated with a link.

  With this fix, both occurrences of "Ver boleta" in the Metrogas email are
  correctly matched against `_PDF_LINK_KEYWORDS` and the tracking URL is
  promoted to the top-priority candidate list, enabling the bill PDF to be
  downloaded successfully.


## [0.6.6] - 2026-03-17

### Added
- **5 new water-billing attributes** extracted from the Aguas Andinas PDF
  billing breakdown table (`attribute_extractor.py`):

  pdfminer serialises the billing table column-by-column (all row labels
  first, then three per-row consumption sub-values, then CLP amounts in
  row order). `_WATER_AA_PDF_BILLING_TABLE_RE` anchors on the label block
  to recover all eight amounts at once.

  | Attribute | Source row | Value | Type |
  |---|---|---|---|
  | `water_consumption` | `CONSUMO AGUA POTABLE … 6.426` | `6426` | int |
  | `wastewater_recolection` | `RECOLECCION AGUAS SERVIDAS … 4.902` | `4902` | int |
  | `wastewater_treatment` | `TRATAMIENTO AGUAS SERVIDAS … 3.360` | `3360` | int |
  | `subtotal` | `SUBTOTAL SERVICIO … 15.602` | `15602` | int |
  | `other_charges` | `INTERÉS DEUDA (99) + DESCUENTO LEY REDONDEO (−1)` | `98` | int |

  Cross-verifications (tolerance ±10 CLP; warnings logged on mismatch):
  - `water_consumption ≈ round(consumption × cubic_meter_non_peak_water_cost)`
  - `wastewater_recolection ≈ round(consumption × cubic_meter_collection)`
  - `wastewater_treatment ≈ round(consumption × cubic_meter_treatment)`
  - `subtotal ≈ fixed_charge + water_consumption + wastewater_recolection + wastewater_treatment`

### Changed
- **`_WATER_ATTR_DEFAULTS`** (`sensor.py`): Added zero defaults for
  `water_consumption`, `wastewater_recolection`, `wastewater_treatment`,
  `subtotal`, `other_charges` so the water sensor exposes these fields
  from startup.


### Added
- **Water PDF extractor — Aguas Andinas** (`attribute_extractor.py`):
  New function `_extract_water_pdf_attributes` and its companion regex
  constants, registered in `_extract_pdf_type_specific_attributes` for
  `SERVICE_TYPE_WATER`.

  Key observations (reference PDF: February 2026):
  - pdfminer reads two-column table sections column-by-column (labels first,
    then values), so label-based lookups with short windows fail; each
    pattern is anchored to a landmark visible in the pdfminer output.
  - Address spans two lines after `SEÑOR RESIDENTE` in the header.
  - Account number follows the `Nro de cuenta` label (with a blank line)
    at the bottom of the bill.
  - Due date follows the `VENCIMIENTO` label in the header.
  - CONSUMO TOTAL value is the last m³ entry in the readings block before
    `MODALIDAD DE PRORRATEO`.
  - Tariff rates appear in a published-rates block as `Label = $ value`.

  | Attribute | Source phrase | Value | Type |
  |---|---|---|---|
  | `address` | `SEÑOR RESIDENTE\nGENERAL JOFRE  385-515\nSANTIAGO` | `"GENERAL JOFRE 385-515 SANTIAGO"` | str |
  | `customer_number` | `Nro de cuenta\n\n1630935-4` | `"1630935-4"` | str |
  | `due_date` | `VENCIMIENTO\n\n21-MAR-2026` | `"21-MAR-2026"` | str |
  | `consumption` | `CONSUMO TOTAL … 10,98 m3` | `10.98` | float |
  | `consumption_unit` | `CONSUMO TOTAL … 10,98 m3` | `"m3"` | str |
  | `total_amount` | `TOTAL A PAGAR\n\n$ 15.700` | `15700` | int |
  | `fixed_charge` | `Cargo fijo = $ 914` | `914` | int |
  | `cubic_meter_peak_water_cost` | `Metro cúbico agua potable punta = $ 585,32` | `585.32` | float |
  | `cubic_meter_non_peak_water_cost` | `Metro cúbico agua potable no punta = $ 585,28` | `585.28` | float |
  | `cubic_meter_overconsumption` | `Metro cúbico sobreconsumo = $ 1.679,38` | `1679.38` | float |
  | `cubic_meter_collection` | `Metro cúbico recolección = $ 446,45` | `446.45` | float |
  | `cubic_meter_treatment` | `Metro cúbico tratamiento = $ 306,45` | `306.45` | float |

  New regex constants: `_WATER_AA_PDF_ADDRESS_RE`, `_WATER_AA_PDF_ACCOUNT_RE`,
  `_WATER_AA_PDF_DUE_DATE_RE`, `_WATER_AA_PDF_CONSUMO_LABEL_RE`,
  `_WATER_AA_TARIFF_AMT`, `_WATER_AA_PDF_FIXED_CHARGE_RE`,
  `_WATER_AA_PDF_PEAK_COST_RE`, `_WATER_AA_PDF_NON_PEAK_COST_RE`,
  `_WATER_AA_PDF_OVERCONSUMPTION_RE`, `_WATER_AA_PDF_COLLECTION_RE`,
  `_WATER_AA_PDF_TREATMENT_RE`.


## [0.6.4] - 2026-03-17

### Added
- **5 new electricity-specific PDF attributes** (`attribute_extractor.py`):
  All extracted from the Enel PDF header block that appears at the very
  beginning of the extracted text.

  | Attribute | Source phrase | Value |
  |---|---|---|
  | `tariff_code` | `Tipo de tarifa contratada: BT1-T2` | `"BT1-T2"` |
  | `connected_power` | `Potencia conectada: 2,500 kW` | `2500` (int) |
  | `connected_power_unit` | `Potencia conectada: 2,500 kW` | `"kW"` |
  | `area` | `Área Típica: AREA 1 S Caso 3 (a)` | `"AREA 1 S Caso 3 (a)"` |
  | `substation` | `Subestación: SAN CRISTOBAL` | `"SAN CRISTOBAL"` |

  New regexes: `_ELEC_PDF_TARIFF_CODE_RE`, `_ELEC_PDF_CONNECTED_POWER_RE`,
  `_ELEC_PDF_AREA_RE`, `_ELEC_PDF_SUBSTATION_RE`.

  `connected_power` is parsed by `_parse_amount_to_int` which correctly
  handles the Chilean format ``2,500`` → ``2500``.

### Changed
- **`_ELECTRICITY_ATTR_DEFAULTS`** (`sensor.py`): Added `tariff_code` (default
  `0`), `connected_power` (default `0`), `connected_power_unit` (default `0`),
  `area` (default `0`), `substation` (default `0`) to the electricity sensor's
  attribute set.
- **`manifest.json`**: Version bumped to `0.6.4`.

## [0.6.3] - 2026-03-17

### Changed
- **Service-type-specific attribute sets** (`sensor.py`): Each service sensor
  now exposes only the attributes relevant to its service type, instead of
  showing every attribute (gas + electricity) regardless of type.

  - `_STANDARD_ATTRS` replaced by:
    - `_COMMON_ATTRS` — universal attributes present for every service type
      (folio, billing dates, customer number, address, due date, total amount,
      consumption, consumption unit).
    - `_GAS_ATTR_DEFAULTS` — gas-only defaults: `cost_per_m3s`.
    - `_ELECTRICITY_ATTR_DEFAULTS` — electricity-only defaults:
      `service_administration`, `electricity_transport`, `stabilization_fund`,
      `electricity_consumption`, `cost_per_kwh`.
    - `_SERVICE_TYPE_ATTR_DEFAULTS` — mapping `service_type → defaults dict`,
      making it straightforward to add water-specific attributes later.
  - `extra_state_attributes` initialises universal defaults then adds
    service-type-specific defaults only for the matching type.  Extracted
    values are applied to both sets independently.
  - **Gas sensor** exposes 16 attributes (15 universal + `cost_per_m3s`).
  - **Electricity sensor** exposes 20 attributes (15 universal + 5 electricity).
  - **Water / unknown sensors** expose only the 15 universal attributes.
- **`manifest.json`**: Version bumped to `0.6.3`.



### Added
- **Electricity PDF extractor for Enel Distribución Chile** (`attribute_extractor.py`):
  A new dedicated `_extract_electricity_pdf_attributes(text)` extracts attributes
  that are only present in the Enel PDF bill, not in the notification email.

  Key implementation detail: pdfminer reads the billing breakdown table as three
  separate column blocks (labels / `$` signs / amounts), so the extractor uses a
  single multi-line regex (`_ELEC_PDF_TABLE_RE`) that matches the full block in
  column order rather than expecting label+amount on the same line.

  New attributes extracted from the PDF:
  - `billing_period_start` / `billing_period_end` – billing period dates
    (DD-MM-YYYY), parsed from the Spanish-month format
    "30 Dic 2025 - 29 Ene 2026" (primary) or the DD/MM/YYYY
    "Período de lectura: 30/12/2025 - 29/01/2026" (fallback).
  - `consumption` / `consumption_unit` – energy consumed confirmed from
    the PDF (e.g. `505.0` / `"kWh"`).
  - `service_administration` – administration fee in integer CLP.
  - `electricity_consumption` – cost of consumed electricity in integer CLP.
  - `electricity_transport` – electricity transport charge in integer CLP.
  - `stabilization_fund` – stabilisation fund charge in integer CLP.
  - `cost_per_kwh` – `electricity_consumption / consumption` (float, 2 dp).

- **`_ELEC_PDF_BILLING_PERIOD_RE`** – regex for Spanish-month date range.
- **`_ELEC_PDF_PERIOD_LECTURA_RE`** – fallback regex for DD/MM/YYYY date range.
- **`_ELEC_PDF_TABLE_RE`** – column-aware regex for the Enel billing table.
- **`_SPANISH_MONTH_MAP`** / **`_parse_spanish_date`** – helpers for
  converting Spanish abbreviated month names to DD-MM-YYYY strings.

### Changed
- **`_extract_pdf_type_specific_attributes`** (`attribute_extractor.py`):
  Routes `SERVICE_TYPE_ELECTRICITY` to `_extract_electricity_pdf_attributes`.
- **PDF text cap** (`extract_attributes_from_pdf`): Increased from 15 000 to
  50 000 characters.  The Enel PDF has key data at ~29 000–31 000 chars; the
  old cap silently discarded all of it.
- **`_STANDARD_ATTRS`** / **`extra_state_attributes`** (`sensor.py`): Added
  `service_administration` (default `0`), `electricity_transport` (default `0`),
  `stabilization_fund` (default `0`), `electricity_consumption` (default `0`),
  and `cost_per_kwh` (default `0.0`) to the standard sensor attributes.
- **`manifest.json`**: Version bumped to `0.6.2`.


## [0.6.1] - 2026-03-17

### Changed
- **Dedicated PDF extractor per service type** (`attribute_extractor.py`):
  The PDF extraction pipeline now mirrors the email extraction pipeline with
  **separate extractor functions per service type**, instead of reusing the
  email extractors for PDF text.

  - **`_GAS_PDF_CONSUMPTION_LABELS`** *(new)*: regex for the Metrogas PDF
    consumption label (``gas\s+consumido`` with ``[:\s\(]+`` separator),
    kept separate from the email-body label patterns in
    ``_GAS_CONSUMPTION_LABELS``.
  - **`_extract_gas_pdf_attributes(text)`** *(new)*: dedicated PDF extractor
    for Metrogas gas bills.  Contains only the patterns that appear in the
    Metrogas PDF (``Gas consumido ( 5,95 m3s )``, ``$``-prefixed total
    amount) and calculates ``cost_per_m3s``.
  - **`_extract_pdf_type_specific_attributes(text, service_type)`** *(new)*:
    routing helper for PDF extractors, parallel to the existing
    ``_extract_type_specific_attributes`` (email router).  Service types
    without a PDF extractor return an empty dict.
  - **`_extract_gas_attributes(text)`** *(email-only)*: reverted to handle
    email bodies only — ``gas\s+consumido`` (PDF-only label) and the ``(``
    separator extension have been moved to the new PDF extractor.
  - **`extract_attributes_from_pdf`**: updated to call
    ``_extract_pdf_type_specific_attributes`` instead of the email router.
- **`manifest.json`**: Version bumped to `0.6.1`.

## [0.6.0] - 2026-03-17

### Added
- **PDF data extraction for gas service** (`attribute_extractor.py`):
  A new `extract_attributes_from_pdf()` function uses `pdfminer.six` to
  convert downloaded bill PDFs to plain text and applies the
  service-type-specific PDF extractor.  For Metrogas, the PDF is the
  authoritative source for gas consumption data not present in the
  notification email.

- **Gas consumption from Metrogas PDF** (`attribute_extractor.py`):
  The gas PDF extractor recognises the PDF label
  ``Gas consumido ( 5,95 m3s )`` and extracts the numeric value and unit.

- **`consumption_unit` correctly set to `"m3s"`** (`attribute_extractor.py`):
  The unit of measurement is extracted directly from the matched text
  (group 2 of `_GAS_CONSUMPTION_RE`) instead of being hard-coded, so
  ``"m3s"`` (Metrogas standardised cubic metres) is returned for Metrogas
  PDFs while ``"m3"`` or ``"m³"`` is still returned for other formats.

- **`cost_per_m3s` attribute** (`attribute_extractor.py`, `sensor.py`):
  A new derived attribute ``cost_per_m3s`` (cost per standardised cubic metre)
  is calculated as ``round(total_amount / consumption, 2)`` whenever both
  values are available and consumption is positive.  It is exposed as a
  standard sensor attribute with a default value of ``0.0``.

- **`pdfminer.six` dependency** (`manifest.json`):
  Added ``pdfminer.six>=20221105`` to the integration's pip requirements so
  that Home Assistant installs the library automatically on first load.

### Changed
- **`_GAS_CONSUMPTION_RE`** (`attribute_extractor.py`): Extended to capture
  the unit string (group 2: ``m3s`` / ``m3`` / ``m³``) alongside the
  numeric value (group 1), enabling precise unit reporting.
- **`sensor.py`**: After a bill PDF is downloaded, `extract_attributes_from_pdf`
  is called automatically and its results are merged into the service
  attributes (PDF values take precedence over email-derived values for the
  same keys).  `cost_per_m3s` added to `_STANDARD_ATTRS` with default `0.0`.
- **`manifest.json`**: Version bumped to `0.6.0`.


### Added
- **Richer PDF link detection in email bodies** (`pdf_downloader.py`):
  When no PDF attachment is found, the integration now searches for billing
  PDF links using an improved three-tier heuristic:

  1. **Keyword match on visible text or `<img alt>`** — links whose human-
     readable label (including the `alt` text of button images) matches
     expanded Spanish/English billing vocabulary (*"ver boleta"*, *"revisar
     tu factura"*, *"descargar comprobante"*, *"visualizar documento"*,
     *"view invoice"*, etc.).
  2. **`.pdf` href suffix** — links whose URL ends in `.pdf` (with optional
     query string).
  3. **Billing-term href match** — links whose URL contains billing-related
     path segments or query parameters (*pdf*, *boleta*, *factura*,
     *invoice*, *bill*, *comprobante*, *descargar*, *download*, etc.)
     anywhere in the URL.

- **Plain-text body search** (`pdf_downloader.py`): A new **Strategy 3**
  scans `text/plain` MIME parts (or plain-text-only emails) for bare
  HTTP/HTTPS URLs that reference a PDF or contain billing-related terms.
  This covers services that send only a text email with a link rather than
  styled HTML.

- **Image-link support** (`_LinkExtractor`): The HTML parser now captures
  the `alt` attribute of `<img>` tags nested inside `<a>` elements, so
  image-only buttons (e.g.
  `<a href="…"><img alt="Ver boleta" …></a>`) are treated the same as
  text links.

- **Expanded billing keywords** (`_PDF_LINK_KEYWORDS`): Added *"revisar
  boleta/factura"*, *"ver comprobante"*, *"descargar comprobante"*,
  *"bajar / obtener pdf"*, *"imprimir boleta/factura"*, *"visualizar
  documento"*, *"ver cobro"*, and broader English patterns
  (*"view invoice/bill/statement/receipt"*, *"get invoice/bill/pdf"*).

### Changed
- **`manifest.json`**: Version bumped to `0.5.6`.
- **`_HTTP_USER_AGENT`**: Updated to reflect the new version string
  (`ConciergeHAIntegration/0.5.6`).
- **`_download_first_valid_pdf` helper extracted**: The fetch-and-validate
  loop shared by HTML and plain-text strategies is now a private helper
  function (`_download_first_valid_pdf`) to eliminate code duplication.

## [0.5.5] - 2026-03-08

### Added
- **Local brand images for HA 2026.3+** (`brand/`): Starting with Home Assistant
  2026.3, custom integrations can ship their own brand images directly inside the
  integration directory.  The `brand/` folder now contains both `icon.png` and
  `icon@2x.png` so that the integration icon is served through the new local
  brands-proxy API (`/api/brands/integration/{domain}/{image}`) without requiring
  a separate submission to the `home-assistant/brands` repository.  No changes to
  `manifest.json` or any Python file are needed — placing the images in `brand/`
  is sufficient.

### Changed
- **`manifest.json`**: Version bumped to `0.5.5`.

## [0.5.4] - 2026-03-08

### Fixed
- **Integration UI buttons not translated** (`manifest.json`): `integration_type` was
  set to `"service"`, which caused Home Assistant to fall back to the English
  `strings.json` for all integration-specific UI strings (subentry labels, button text,
  device-type badges). The value has been restored to `"hub"` — as it was first set in
  v0.4.2 — so that HA renders the proper CONFIGURE + ADD DEVICE card and applies the
  integration's own translations (including `es.json`) for all subentry labels.
  Concretely:
  - **"+ Add Service Device"** button now shows as **"+ Agregar Dispositivo de Servicio"**
    in Spanish (and in any other supported locale).
  - **"Service Device"** type badge shown under each subentry group now reads
    **"Dispositivo de Servicio"** in Spanish.
- **"Agregar servicio" button hidden when already configured**: With `integration_type:
  "hub"` and `single_config_entry: true`, Home Assistant no longer shows the "Add new
  instance" button on the integration card once an email account is already configured.
  The `async_set_unique_id(DOMAIN)` + `_abort_if_unique_id_configured()` guard provides
  an additional code-level safeguard.
- **`AttributeError: 'ConciergeServicesCoordinator' object has no attribute 'get'`**
  (`sensor.py`): `async_setup_entry` in the sensor platform was assigning the
  coordinator object directly to `hass.data[DOMAIN][entry_id]`, overwriting the
  plain dict (containing the `pending_discoveries` set) that
  `__init__.py`'s `async_setup_entry` had just initialised at the same key.
  When the background discovery task subsequently called
  `.get(_PENDING_DISCOVERIES_KEY, set())` on what it expected to be a dict, it
  received a `ConciergeServicesCoordinator` instance instead and crashed.

  Fix: the coordinator is now stored under a `"coordinator"` sub-key
  (`hass.data[DOMAIN][entry_id]["coordinator"]`) so the pending-discoveries dict
  is preserved and the discovery task runs without errors.

## [0.5.2] - 2026-03-07

### Added
- **IMAP-based service discovery** (`__init__.py`, `config_flow.py`): Service
  accounts are now detected automatically via "discovery" and surface in
  **Configuration → Integrations** as devices available to be added — similar
  to how MQTT component discovery works.

  *How it works*:
  1. After the integration is set up, a background task scans the IMAP inbox
     immediately and then repeats every **hour**.
  2. For each detected service that is not already configured as a subentry, the
     integration initiates a subentry discovery flow
     (`hass.config_entries.subentries.async_init`).  Requires **HA 2025.4 or
     newer** (the subentry discovery API was added in that release); on older
     versions the background scan runs silently without triggering flows.
  3. The user sees the discovered device in the integration card and can confirm
     or dismiss it with a single click via the new **"Discovered: {service_name}"**
     confirmation step (`async_step_discovery_confirm`).
  4. Confirming adds the subentry directly using `async_add_subentry` (the
     HA-recommended path for discovery-initiated subentries) and reloads the
     entry so the sensor appears immediately.

- **`async_step_discovery` / `async_step_discovery_confirm`** steps added to
  `ServiceSubentryFlowHandler` (`config_flow.py`): handle the discovery source
  context, check for duplicates (already-configured and already-pending
  discoveries are silently aborted), and confirm the addition with a
  description that includes the service name and e-mail count.

- New string keys (`strings.json`, `translations/en.json`,
  `translations/es.json`):
  - `config_subentries.service.initiate_flow.discovery` — label for the
    discovery-triggered flow card.
  - `config_subentries.service.step.discovery_confirm` — confirmation form.
  - `config_subentries.service.abort.already_configured` — shown when the
    service is already set up.
  - `config_subentries.service.abort.subentry_added` — shown after a
    successful discovery confirmation.

## [0.5.1] - 2026-03-07

### Fixed
- **"Dispositivos que no pertenecen a una subentrada" grouping** (`sensor.py`):
  The connection/status sensor no longer creates a hub device associated with
  the main config entry.  Removing `device_info` from
  `ConciergeServicesConnectionSensor` means no device is registered for the
  main entry, so the "Devices that don't belong to a sub-entry" section is
  no longer shown in the integration page.  The status sensor remains fully
  functional as a standalone entity.
- **Removed `via_device` from service sensors** (`sensor.py`): Each service
  sensor device now stands independently under its own subentry without
  being linked to a (now-removed) hub device.

### Added
- **Automatic migration from v0.4.x** (`__init__.py`, `config_flow.py`):
  Upgrading from v0.4.x no longer requires deleting and re-adding the
  integration.  A `async_migrate_entry` migration (config entry minor
  version 1.1 → 1.2) runs automatically on first startup after the upgrade
  and performs three steps:
  1. Assigns the correct `config_subentry_id` to each service entity already
     in the entity registry.
  2. Removes the legacy hub device (previously associated with the main
     config entry).
  3. Moves each service device's registry association from the main config
     entry to its own subentry, so devices appear under the correct subentry
     group in the HA UI without any manual reconfiguration.

## [0.5.0] - 2026-03-07

### Added
- **Standard attributes with default values** (`sensor.py`): Every service
  sensor now exposes the full set of standard attributes on every update.
  If a value cannot be extracted from the email the attribute defaults to
  `0`, so automations and dashboards never see a missing key:
  `service_id`, `service_name`, `service_type`, `last_updated_datetime`,
  `folio`, `billing_period_start`, `billing_period_end`, `customer_number`,
  `address`, `due_date`, `icon`, `friendly_name`, `total_amount`,
  `consumption`, `consumption_unit`.
- **`icon` and `friendly_name` attributes** (`sensor.py`): Both are now
  included as extra-state attributes on every service sensor.
  `icon` is always `mdi:file-document-outline`; `friendly_name` mirrors
  the configured service name.

### Changed
- **`total_amount` is now an integer** (`attribute_extractor.py`): The
  extracted amount is converted to a plain integer, removing thousands
  separators regardless of locale format (e.g. Chilean `122.060` → `122060`,
  `12.013` → `12013`, `1.234,56` → `1234`).  A dot or comma followed by
  exactly 3 digits is treated as a thousands separator; followed by 1–2
  digits it is treated as a decimal separator (decimal part discarded).
- **`consumption` is now a float** (`attribute_extractor.py`): The extracted
  consumption value is converted to a Python `float` instead of being stored
  as a raw string.  A new `_parse_consumption_to_float()` helper handles both
  Chilean/Spanish format (dot = thousands separator, comma = decimal, e.g.
  `"1.500"` → `1500.0`, `"12,5"` → `12.5`) and English format (`"12.5"` →
  `12.5`).  The default value in the sensor attributes is `0.0`.
- **Unified consumption attributes** (`attribute_extractor.py`): Service-type
  specific fields (`consumption_m3`, `consumption_kwh`) have been replaced
  by the standard attributes `consumption` and `consumption_unit` (`"m3"` or
  `"kWh"` depending on the service type).
- **Removed non-standard attributes** (`attribute_extractor.py`): The
  following service-specific fields are no longer extracted or exposed as
  they will be re-introduced as typed specific attributes in a future
  version: `next_billing_period_start`, `next_billing_period_end`,
  `consumption_type`, `boleta_date`, `metropuntos`, `contracted_power_kw`,
  `rut_from_subject`.
- **Fixed duplicate device registration** (`sensor.py`): Service sensor
  entities are now registered with their respective `config_subentry_id` via
  `async_add_entities(..., config_subentry_id=subentry_id)`.  This causes
  each service device to appear correctly nested under its subentry in the
  Home Assistant device registry (matching the MQTT integration layout
  shown in screenshot-02) and eliminates the "Dispositivos que no
  pertenecen a una subentrada" grouping.

### Removed
- `attributes_extracted_count` debug attribute from service sensors.

## [0.4.10] - 2026-03-06

### Added
- **Heuristic PDF downloader** (`pdf_downloader.py`): New module that locates
  and saves the billing PDF for each matched email using a two-strategy
  heuristic:
  1. **PDF attachment** — walks MIME parts looking for `application/pdf`
     content-type or a filename ending in `.pdf`; saves the raw bytes.
  2. **Link in HTML body** — if no attachment is found, parses `text/html`
     parts with a lightweight `HTMLParser` subclass and collects `<a href>`
     candidates. Priority is given to links whose visible text matches
     Spanish/English billing keywords (*"ver boleta"*, *"descargue su
     boleta"*, *"ver factura"*, *"descargar pdf"*, etc.). A secondary pass
     collects links whose `href` ends in `.pdf`. Each candidate URL is
     fetched; the response is validated by magic-byte check (`%PDF`) and/or
     `Content-Type` header before the file is written.
- **Intelligent filename scheme**: Downloaded PDFs follow a deterministic
  naming convention so that re-running on the same bill produces the same
  filename and the download is skipped if the file already exists:
  ```
  {service_id}_{YYYY}-{MM}_{folio}.pdf   # folio available
  {service_id}_{YYYY}-{MM}.pdf           # folio not extracted
  ```
  `YYYY-MM` comes from `billing_period_start` (extracted by the attribute
  extractor) with a fallback to the email's `Date` header.
- **Automatic PDF purge**: `purge_old_pdfs()` removes files older than
  `PDF_MAX_AGE_DAYS` (365 days) from the download directory. It is called
  automatically once per coordinator update cycle (every 30 minutes).
- **PDF storage constants** (`const.py`): `PDF_SUBDIR` and `PDF_MAX_AGE_DAYS`
  centralise configuration. PDFs are stored under
  `{ha_config_dir}/concierge_ha_integration/pdfs/`.
- **`pdf_path` sensor attribute** (`sensor.py`): When a PDF is successfully
  downloaded its absolute path is exposed as the `pdf_path` extra-state
  attribute on the service sensor.

### Changed
- **`manifest.json`**: Version bumped to `0.4.10`.

## [0.4.8] - 2026-03-03

### Fixed
- **Enel and Metrogas not detected during service discovery**
  (`service_detector.py`, `sensor.py`): The integration required emails to
  have file attachments before evaluating them as billing emails.  Both Enel
  and Metrogas send HTML notification emails *without* PDF attachments (the
  bill is downloaded via a link); only Aguas Andinas attaches the PDF
  directly.  Removed the `_has_attachments` gate from the service detector
  and from the sensor's email-matching loop so that attachment-free billing
  emails are evaluated correctly.
- **Enel and Metrogas emails not matched by sensor after detection**
  (`sensor.py` `_matches_service`): Both emails arrive as Gmail-forwarded
  messages (`From: user@gmail.com`), so the sender-domain check was
  matching the generic domain `"gmail"` against *every* forwarded email,
  causing cross-service matches and missing matches.  Three targeted fixes:
  1. Domain matching now skips known generic webmail providers (gmail,
     hotmail, yahoo, outlook, live, icloud, protonmail).
  2. Service-name matching now correctly handles short names like `"Gas"`
     (previously the `0 >= 0` condition always returned `True`, matching
     every email regardless of content).
  3. A new subject-keyword check extracts company-specific words from the
     stored `sample_subject` (e.g. `"enel"` from
     `"Fwd: Cuenta Enel de este mes …"` or `"metrogas"` from
     `"Fwd: Boleta Metrogas Nro. …"`) and requires at least one of them to
     appear in the candidate email, reliably distinguishing Enel from
     Metrogas even when both are forwarded from the same Gmail account.

### Changed
- **`manifest.json`**: Version bumped to `0.4.8`.

## [0.4.7] - 2026-03-03

### Fixed
- **Duplicate sensors per service account**: `manifest.json` declared
  `"integration_type": "hub"`, which caused Home Assistant to register
  two sensor entities for every service account subentry.  Changed to
  `"integration_type": "service"` (matching the MQTT integration pattern
  described in the architecture).
- **"Invalid handler specified" error on config flow**: `const.py` still
  contained the old domain `"concierge_services"` after the integration was
  renamed to `"concierge_ha_integration"`.  Home Assistant looks up the config
  flow handler by `DOMAIN`, so the mismatch between `const.py` and
  `manifest.json` / the component folder caused the config flow to fail to
  load.  Updated `DOMAIN` in `const.py` to `"concierge_ha_integration"`.

### Changed
- **`manifest.json`**: Version bumped to `0.4.7`.

## [0.4.6] - 2026-03-01

### Changed
- **`manifest.json`**: Version bumped to `0.4.6`.
- Integration name revamped to **Concierge HA Integration**

## [0.4.5] - 2026-02-26

### Fixed
- **`TypeError` when reconfiguring a service subentry** (`config_flow.py`):
  `ServiceSubentryFlowHandler.async_step_reconfigure` was calling
  `self.async_update_and_abort()` without the two required positional arguments
  `entry` and `subentry`.  Added `self._get_entry()` and the already-retrieved
  `subentry` object as the first two arguments so the call matches the
  `ConfigSubentryFlow.async_update_and_abort(entry, subentry, ...)` signature.

### Changed
- **`manifest.json`**: Version bumped to `0.4.5`.

## [0.4.4] - 2026-02-25

### Fixed
- **`ValueError` when reconfiguring a service account** (`config_flow.py`):
  `ServiceSubentryFlowHandler.async_step_reconfigure` was calling `self.async_create_entry()`
  which is only valid when `source == user`.  For reconfigure flows the correct method is
  `self.async_update_and_abort()`, which updates the subentry data in-place and closes the
  flow without creating a duplicate entry.
- **Service entity shows no data after being added** (`sensor.py`):
  `_find_latest_email_for_service` was only scanning the last 50 emails in the inbox,
  while `service_detector.py` scans the last 100.  When the most recent billing email for
  a service was beyond the first 50, the sensor would not find it and the entity state
  would remain empty.  The limit has been raised to 100 to match the detector.
  A `WARNING`-level log message is now emitted when no matching email is found for a
  service, making future diagnosis visible in the HA log without requiring debug logging.

### Changed
- **`manifest.json`**: Version bumped to `0.4.4`.



### Fixed
- **`AttributeError` when adding a service device** (`config_flow.py`): `ServiceSubentryFlowHandler`
  was accessing `self._config_entry`, a private attribute that does not exist on Home Assistant's
  `ConfigSubentryFlow` base class. This caused a 500 error every time the **ADD DEVICE** button
  was clicked.
  - `async_step_user`: replaced `self._config_entry` with `self._get_entry()`.
  - `async_step_reconfigure`: replaced `self._config_entry.subentries[self._subentry_id]` with
    `self._get_reconfigure_subentry()`.

### Changed
- **`manifest.json`**: Version bumped to `0.4.3`.

## [0.4.2] - 2026-02-23

### Added
- **`integration_type: "hub"`** (`manifest.json`): Marks the integration as a hub so Home
  Assistant displays it like the MQTT integration — with a **CONFIGURE** button and an
  **ADD DEVICE** button on the integration card.
- **`single_config_entry: true`** (`manifest.json`): Only one Concierge Services instance
  (one monitored email account) is allowed at a time.
- **Options Flow** (`config_flow.py` → `OptionsFlowHandler`): The **CONFIGURE** button
  opens a pre-filled form to reconfigure the IMAP credentials and friendly name without
  deleting and re-adding the integration.
- **Subentry Flow** (`config_flow.py` → `ServiceSubentryFlowHandler`): The **ADD DEVICE**
  button scans the inbox, filters out services already added, and lets the user select one
  new service account to add as a device. Each device also supports a **reconfigure** step
  so the service name can be updated via the UI.
- **New constants** (`const.py`): `CONF_SERVICE_ID`, `CONF_SERVICE_NAME`,
  `CONF_SERVICE_TYPE`, `CONF_SAMPLE_FROM`, `CONF_SAMPLE_SUBJECT` — used as keys in
  subentry data instead of the old flat `services_metadata` dict.
- **Subentry strings** (`strings.json`, `translations/en.json`, `translations/es.json`):
  New `config_subentries.service` section with step, error, and abort messages for the
  add-device and reconfigure flows.

### Changed
- **Initial config flow** (`config_flow.py`): Simplified to two steps only — IMAP
  credentials (`user`) and friendly name (`finalize`). Service detection during initial
  setup has been removed; services are added individually after setup via ADD DEVICE.
- **`sensor.py`**: `async_setup_entry` now iterates over `config_entry.subentries` to
  create service sensors, instead of reading from the old `services` / `services_metadata`
  keys in `config_entry.data`. The coordinator reads effective credentials from
  `{**entry.data, **entry.options}` so options-flow changes are respected without
  needing to reconfigure from scratch.
- **`__init__.py`**: Removed explicit device-registry calls (devices are created via
  `DeviceInfo` in the sensor entities). Added an `add_update_listener` so the entry
  reloads automatically when options or subentries change.
- **`manifest.json`**: Version bumped to `0.4.2`.

### Removed
- `CONF_SERVICES` constant from `const.py` (replaced by individual subentries).
- `area_id` support from the initial config flow (areas can still be assigned from the
  device card after setup).
- `detect_services` and `select_services` steps from the initial config flow.

### Migration note
Existing config entries from v0.4.0/v0.4.1 will continue to load (the connection sensor
works without subentries). Previously configured service sensors will not appear
automatically — use the **ADD DEVICE** button to re-add them as subentries.


### Added
- **Service-Type Constants** (`const.py`): Added `SERVICE_TYPE_WATER`, `SERVICE_TYPE_GAS`,
  `SERVICE_TYPE_ELECTRICITY`, `SERVICE_TYPE_TELECOM`, and `SERVICE_TYPE_UNKNOWN` constants
  to classify detected services.

- **Modular Type-Specific Extractors** (`attribute_extractor.py`): Extraction tools are
  now organised by service type so each utility category can use patterns tuned to its
  own email format:

  | Service type  | Extra attributes extracted |
  |---|---|
  | `water`       | `address` + `customer_number` (packed-values), `consumption_m3`, `meter_reading`, `meter_number` |
  | `gas`         | `total_amount` (plain-number override), `metropuntos`, `consumption_m3` (label-based) |
  | `electricity` | `folio`, `boleta_date`, `address` (from `ubicado en`), `consumption_kwh`, `consumption_type`, `next_billing_period_start/end` |

- **`due_date` extraction** (`attribute_extractor.py`): New generic extractor
  (`_extract_due_date`) searches for `Fecha de vencimiento` label — confirmed in
  both Metrogas and Enel emails.

- **`_extract_type_specific_attributes` routing helper** (`attribute_extractor.py`):
  Dispatches to the correct type-specific extractor based on `service_type`.

- **`classify_service_type` utility** (`service_detector.py`): Public function that
  infers the service type from the email `From` address and `Subject` line.

### Changed
- **`_strip_html`** (`attribute_extractor.py`): Now applies `html.unescape()` a second
  time after the HTML parser so that double-encoded entities (`&amp;oacute;` →
  `&oacute;` → `ó`) found in Aguas Andinas emails are fully decoded.

- **`_CUSTOMER_LABELS`** (`attribute_extractor.py`): Made `de` optional so both
  `Número de Cliente:` and `Número Cliente:` (Metrogas) are matched.

- **`_extract_from_subject` folio patterns** (`attribute_extractor.py`): Added
  `r"nro\.?\s+([0-9]{6,})"` for the Metrogas `Boleta Metrogas Nro. NNNNNN` format.

- **`SERVICE_PATTERNS`** (`service_detector.py`): 3-tuples with service type added.

- **`DetectedService` dataclass** (`service_detector.py`): Added `service_type` field.

- **`ConciergeServiceSensor.extra_state_attributes`** (`sensor.py`): Exposes
  `service_type`; also filters `None` attribute values so fields cleared by
  type-specific extractors are omitted from the HA UI rather than shown as `null`.

- **Config flow** (`config_flow.py`): Stores `service_type` in `services_metadata`.

### Water extractor — Aguas Andinas (reference email: February 2026)
The Aguas Andinas HTML-only email uses a two-column table layout: labels
(`Dirección:`, `Número de Cuenta:`, `Período de Facturación:`) are in the left `<td>`;
all values are packed in the right `<td>` as a single paragraph:
`ADDRESS    ACCOUNT_NUM    DATE al DATE`.

| Root cause | Fix |
|---|---|
| `&amp;oacute;` double-encoded HTML entities | `_strip_html` applies `html.unescape()` twice |
| Labels and values in separate `<td>` — generic label extractor gives wrong results | `_WATER_AA_PACKED_RE` detects the ALL-CAPS address + `\d{5,}-\d` account pattern; results override generic values via `update()` |

Fields now correctly extracted: `billing_period_start/end` ✓ `total_amount` ✓
`address` ✓ (was `'Número de Cuenta:'`) `customer_number` ✓ (was street number `'385-515'`)

### Gas extractor — Metrogas (reference email: January 2026)
- Folio from subject `Nro.` pattern, plain-number total, `metropuntos` loyalty points.
- Gas consumption (m³) is not in the email body — only in the PDF attachment.

### Electricity extractor — Enel Distribución Chile (reference email: February 2026)
The Enel email has both `text/plain` and `text/html` parts; extractor uses plain text.

| What we learned | How it's handled |
|---|---|
| Invoice number in body: `N° Boleta 361692435 del 02-02-2026` | `_ELEC_ENEL_FOLIO_RE` + `_ELEC_ENEL_BOLETA_DATE_RE` → `folio` + `boleta_date` |
| Address follows `ubicado en` (not `Dirección:`) | `_ELEC_ENEL_ADDRESS_RE` → `address` |
| No current billing period in email; first two dates are boleta date + due date (WRONG) | Electricity extractor sets `billing_period_start/end = None`; sensor filters `None` values |
| `Próximo periodo de facturación` = NEXT billing period | `_ELEC_ENEL_NEXT_PERIOD_RE` → `next_billing_period_start/end` |
| `Consumo real` / `Consumo estimado` quality flag | `_ELEC_ENEL_CONSUMPTION_TYPE_RE` → `consumption_type` |
| `505 kWh` in email body | bare-kWh fallback → `consumption_kwh` |

## [0.3.2] - 2026-02-22

### Changed
- **Targeted Attribute Extraction**: Replaced broad heuristic email parsing with a
  focused extractor that produces exactly the fields needed before PDF analysis:

  | Attribute | Description |
  |---|---|
  | `service_name` | Utility company name (from sensor metadata) |
  | `folio` | Invoice/folio number (extracted from subject; confirmed later by PDF) |
  | `billing_period_start` | Start date of the billing period |
  | `billing_period_end` | End date of the billing period |
  | `total_amount` | Total amount due |
  | `customer_number` | Customer / account number |
  | `address` | Service address |
  | `last_updated_datetime` | Date the company sent the email (from `Date` header) |

- **HTML Body Handling**: Email body extractor now prefers `text/plain` parts;
  falls back to `text/html` only after stripping tags via stdlib `html.parser`.

### Removed
- Generic heuristic extractors (`_extract_key_value_pairs`, `_extract_currency_amounts`,
  `_extract_ids`, `FIELD_INDICATORS`, `KEY_VALUE_PATTERNS`, etc.) — replaced by
  targeted extractors (`_extract_total_amount`, `_extract_customer_number`, `_extract_address`).
- Redundant `empresa` attribute (covered by `service_name`).

### Fixed
- `mypy` errors: added `assert config_entry is not None` guards in
  `_fetch_service_data` and `ConciergeServicesConnectionSensor.extra_state_attributes`.

## [0.3.0] - 2026-02-21

### Added
- **Service Detection Flow**: Integration now automatically detects service accounts from inbox during setup
- **Service Selection**: Users can now select which detected services to configure as devices
- **MQTT-Style Architecture**: Following Home Assistant's MQTT integration pattern:
  - Email account acts as the "service" (hub)
  - Service accounts act as "devices" linked to the hub
- **Multi-Step Configuration**: Enhanced setup flow with service detection and selection
- **Service Metadata Storage**: Detected services are stored with metadata for future updates

### Changed
- **Configuration Flow**: Added two new steps after email setup:
  1. Service detection (automatic scan of inbox)
  2. Service selection (choose which services to configure)
- **Device Creation**: Devices are now created for all selected services during initial setup
- **Sensor Platform**: Updated to properly handle configured services from config entry

### Fixed
- **Service Detection Issue**: Previously detected services were not being converted into devices
- **Device Creation**: Service devices are now properly created during integration setup

## [0.2.0] - 2026-02-18

### Added
- **Device Architecture**: Each service is now represented as a separate device in Home Assistant
- **Friendly Name Configuration**: Users can set a custom friendly name for the integration
- **Area Assignment**: Integration can be associated with a specific area during setup
- **Automatic Service Detection**: Services are detected automatically from email inbox
- **Heuristic Attribute Extraction**: Automatically extracts billing attributes from email content
  - Account numbers, invoice numbers (folio)
  - Total amounts, due dates, billing periods
  - Consumption data, addresses, RUT
  - Company names from email subject
  - Any structured data in email body
- **Device-per-Service**: Each detected service appears as its own device with sensors
- **Status Sensor**: Renamed to "Concierge Services - Status" for consistency
- **Two-Step Configuration Flow**:
  1. IMAP credentials (server, port, email, password)
  2. Finalize (friendly name and area selection)

### Changed
- **Config Flow Simplified**: Service selection removed from initial setup
- **Device Names**: Uses friendly name instead of email address
- **Sensor Naming**: Connection sensor now called "Concierge Services - Status"
- **Device Info**: All sensors now include proper device_info for grouping
- **Architecture**: Prepared for automatic service discovery and notifications

### Technical
- Added `DeviceInfo` to all sensor entities
- Device hierarchy with `via_device` linking service devices to main device
- Area assignment using Home Assistant's area registry
- Services metadata stored in config entry for future use
- Heuristic pattern matching for attribute extraction (40+ field indicators)
- Multi-language support for attribute detection (Spanish and English)

## [0.1.5] - 2026-02-18

### Added
- Mail server connection status sensor that displays "OK" or "Problem"
- Sensor checks IMAP connection every 30 minutes and reports status
- Sensor includes email, server, and port as attributes
- Enhanced configuration form with helpful placeholder text and examples
- Added data_description fields in strings.json and translations for better UX
- Suggested values for IMAP server (e.g., "imap.gmail.com") and email (e.g., "user@gmail.com")

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
- Created CHANGELOG.md to track project changes
- Added MIT License
- Created HACS configuration file
- Added Spanish translations for the integration UI

### Technical Details
- Integration domain: `concierge_services`
- Configuration flow implementation using Home Assistant's config_flow
- Supports IMAP SSL/TLS connection on port 993
- IoT class: cloud_polling
