**Before doing anything, run `date` to get the current date and time.**

# Beets Documentation Lookup

Read beets reference documentation from the local nix store.

## Instructions

The beets source docs (RST format) are available in the nix store. First, resolve the store path:

```bash
nix build nixpkgs#beets.src --no-link --print-out-paths
```

This returns a path like `/nix/store/<hash>-source`. The docs live at `${BEETS_SRC}/docs/`.

### Doc Tree

| Doc | Path | Lines | Purpose |
|-----|------|-------|---------|
| Config reference | `docs/reference/config.rst` | ~1181 | All config.yaml options |
| CLI commands | `docs/reference/cli.rst` | ~538 | CLI commands reference |
| Path templates | `docs/reference/pathformat.rst` | ~292 | Path format templates |
| Query syntax | `docs/reference/query.rst` | ~443 | Query syntax |
| Plugin overview | `docs/plugins/index.rst` | ~706 | Plugin overview & list |
| Plugin docs | `docs/plugins/<name>.rst` | varies | One file per plugin |
| Autotagger guide | `docs/guides/tagger.rst` | varies | How the autotagger works |
| Advanced guide | `docs/guides/advanced.rst` | varies | Advanced usage |
| FAQ | `docs/faq.rst` | varies | Common questions |

### How to Use

1. **Resolve the path** using the nix build command above
2. **Read docs** with the Read tool: `Read file_path="${BEETS_SRC}/docs/reference/config.rst"`
3. **Search docs** with Grep: `Grep pattern="import" path="${BEETS_SRC}/docs/reference" output_mode="content"`
4. **Find plugin docs**: `Read file_path="${BEETS_SRC}/docs/plugins/chroma.rst"`

### Quick Lookups

- **Config option**: search `docs/reference/config.rst` for the option name
- **Plugin config**: read `docs/plugins/<plugin-name>.rst`
- **Path template variables**: read `docs/reference/pathformat.rst`
- **Import behaviour**: search config.rst for `import`
- **Matching/autotagger**: read `docs/guides/tagger.rst` and search config.rst for `match`

### Current Beets Nix Module

The beets config is managed by the Home Manager module at:
`/home/abl030/nixosconfig/modules/home-manager/services/beets.nix`

The module header (~230 lines) contains the full plugin catalogue and Home Manager options reference. Read that first for available options and plugins before diving into the RST docs.

After changes, rebuild with:
```bash
cd /home/abl030/nixosconfig && nix fmt && sudo nixos-rebuild switch --flake .#proxmox-vm
```