<img src="assets/icon.svg" width="64" height="64" alt="">

# CSP Story Tool

Created in [T3 Code](https://t3.codes).

Round-trip the text in a Clip Studio Paint comic through any external editor or
spellchecker **without losing bubble positions, fonts, or styling**.

Clip Studio's Story Editor rebuilds text layers when you paste across them,
which resets their positions. This tool edits the `.clip` files directly, changing only
the text.

## Usage

Requires Python 3.10+, with no dependencies.

### GUI

```
python -m csp_story gui
```

Use `pythonw` instead of `python` on Windows to avoid a console window. Pick the
comic, pick the script file (defaults to `script.txt` in the current folder), then click
**Export** or **Import**. Tick *Dry run* to preview an import without writing anything.
Output appears in the log at the bottom.

### Command line

1. **Save and close the comic in Clip Studio.** Unsaved pages (marked `*`) are not on disk yet.
2. Export:
   ```
   python -m csp_story export path/to/Comic.cmc -o story.txt
   ```
3. Edit `story.txt` (paste it into a spellchecker and paste the result back). Keep every
   `[[page:layer:n]]` marker line intact. Text on hidden layers (or inside hidden
   folders) is exported as `[[page:layer:n hidden]]`. The tag is for information only:
   removing it doesn't unhide the layer.
4. Preview, then apply:
   ```
   python -m csp_story import story.txt --dry-run
   python -m csp_story import story.txt
   ```
   Each modified page is backed up as `pageNNNN.clip.bak` first.
5. Reopen the comic in Clip Studio. **The canvas will still show the old text** until each
   text layer is touched (click into it with the Text tool). The Story Editor and layer
   names show the new text right away. Clip Studio saves a rendered image of every text
   layer and never redraws it on load. The import prints which pages need refreshing.

## Safety

- A block is skipped (and reported) if its text was changed in Clip Studio after the
  export, or if its styling can't be updated safely.
- `story.txt.snapshot.json` (written next to the export) records the original text. Keep it with the document.

## How it works

A `.clip` file is a `CSFCHUNK` container holding a SQLite database (`CHNKSQLi`).
Text is stored in `Layer.TextLayerString` (UTF-8, CRLF line breaks). Additional text
objects in the same layer are in `TextLayerStringArray`. Styling is in
`TextLayerAttributes`, a little-endian TLV stream. The style-run records (ids
`0x0b 0x0c 0x0d 0x12 0x1a 0x1d`) store `(start, length)` character ranges in UTF-16
units, and these are remapped when text length changes. Position and size records are left alone.

## Tests

```
python -m unittest discover -s tests -t .
```
