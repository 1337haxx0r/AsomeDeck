# Agent instructions

## Working alongside other sessions

Other terminals over this checkout may be running their own agents in the same working
tree -- including ones in a different deck in the sidebar. Coordinate through `.asomedeck/`:

- `.asomedeck/panes.md` lists the panes running here: number, name and deck. Your pane
  number is in `$ASOMEDECK_PANE_ID`; your name, if you have one, is beside it there. When
  you are asked about a pane by name -- "check with designer" -- that is where the name
  resolves.
- Read `.asomedeck/board.md` before you start work. Its full path is in `$ASOMEDECK_BOARD`.
- Add a claim for what you are taking: `- pane N (name) -- files -- what`.
- Delete your claim when you are done.
- If a file you need is claimed by another pane, work on something else rather than
  editing it underneath them. Say which pane is holding it.
