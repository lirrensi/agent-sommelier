# Importing `.skill` Bundles

A `.skill` file is just a zip of a skill folder.

## Import

```bash
# Drop the bundle
cp path/to/web-scraper.skill ~/.skill-store/skills/

# Rebuild index
skill-store sync
```

`sync` auto-extracts `.skill` files in `~/.skill-store/skills/`.

## Export

```bash
# Zip a skill folder into a .skill file
cd ~/.skill-store/skills/<slug>/
zip -r ../<slug>.skill . -x '*.git*'
```

Or from the store root:

```bash
cd ~/.skill-store/skills
zip -r ../<slug>.skill <slug>/ -x '*.git*'
```

## Collision Handling

- In a terminal: prompted to overwrite / skip / rename
- In non-TTY mode: errors cleanly
