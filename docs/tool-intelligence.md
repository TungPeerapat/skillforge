# Tool / CLI / configuration intelligence (K10)

K10 adds **offline extraction**, not a new CLI command or live tool execution.
Knowledge build/search integration remains K14; lifecycle analysis remains K11.

## Supported inputs

- `CliReferenceExtractor.extract_reference(document, source, target)` reads
  explicit command headings and Usage/Options/Commands/Arguments reference
  blocks. It understands command trees, comma-separated aliases, option value
  names, explicitly documented defaults, wrapped descriptions and argument
  optionality/repetition when the usage syntax states it.
- `ConfigReferenceExtractor.extract_reference(...)` reads explicit headings
  such as `Configuration TOML lint.select`, with `Type:`, JSON-literal
  `Default:`, `Required:` and `Choices:` fields. Ordinary examples are ignored.
- `ConfigReferenceExtractor.extract_schema(schema, source, target, ...)`
  reads bounded JSON Schema properties, types, enums, defaults and required
  keys. Local `#/...` references support JSON pointer escaping and are
  cycle/depth checked. Remote refs are **never fetched**; complex conditional
  branches and unsupported keys produce diagnostics rather than guessed facts.

Configured CLI identities: pytest, Ruff, Next and dotnet. This does not imply
support for every format used by their documentation. In particular HTML
tables, shell completions, dynamic config and complex schema composition are
not fully interpreted. Config schemas must be supplied as supported, proven
inputs; a repository's example config is not a schema.

## Evidence and identity

Tool extraction requires an accepted official EXACT source, matching source
ID/URL, technology, ecosystem and resolved version. URL conventions alone are
not additional proof of content or existence. K10 consumes existing verified
source records; it does not fetch or verify a release page itself.

`KnowledgeSource.ecosystem` is additive. Old source records still load, but
records with no established ecosystem cannot back new K10 metadata until they
are refreshed. Existing source/capability IDs and enum ranks are unchanged.

Every tool record carries `ToolProvenance` with source ID, normalized section
or schema-pointer locator and extractor version. CLI names use a `cli:`
namespace; config keys use `config:` to avoid collisions with API symbols.
Intermediate config sections may represent path namespaces, not independently
configurable objects.

## Metadata and unknown values

Capabilities/drafts gain optional `cli`, `option`, `argument`, `config` and
`tool_provenance` fields. Older records read with these fields absent.

`default_known=false` is different from a known default of `false`, `""`, `0`
or JSON `null`. Requiredness, repeatability, types and choices remain null when
unsupported. Defaults/choices are redacted recursively; descriptions are
redacted before quote truncation. Known contradictory metadata is preserved as
a conflict; later evidence may fill unknown fields without overwriting facts.

Command parents/options/config sections flow through the existing capability
IR and graph. The index accepts short/long option aliases and command aliases.
No exported SKILL.md frontmatter or v0.2 execution behavior is changed.

## Probing is deliberately disabled in production

`ToolProbeService` provides a default-off policy and injectable backend
contract for offline testing. **There is no live backend in K10**, even when
its policy is explicitly enabled. No `--probe-tools` switch is exposed yet.

The contract allows only fixed version/help argv for allowlisted tools and
subcommands, with explicit absolute executable identity outside the repo,
temporary cwd, sanitized environment, byte/time budgets and per-service caches.
Version is checked before any help request. An injected backend is trusted
caller code and is responsible for enforcing subprocess limits/cleanup itself;
the service is not a process sandbox.

Running `--help` can import plugins or execute JavaScript. Live probing needs
separate consent/identity/security review during CLI integration, rather than
reusing `doctor`'s unbounded capture helper or invoking `npx`/installers.

## Verification and limitations

Offline fixtures under `tests/fixtures/tool-intelligence/` are **synthetic
contract fixtures**, not downloaded official docs or evidence of whole-tool
coverage. Tests cover pytest/Ruff/Next/dotnet CLI and config records, version
isolation, provenance, aliases, schema refs, unknown/false/empty/null defaults,
conflicts, redaction, budgets, and store/index/graph round trips. Fake probe
tests never execute those tools. An execution/network canary verifies offline
extraction does not call subprocess or DNS APIs.

Real-version documentation corpus coverage is still a K19/release-review gate.
