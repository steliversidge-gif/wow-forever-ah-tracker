# Reference data

`drops.json` maps an item id to the three enemies most likely to drop it:
`[npc id, name, level, zone, drop chance %]`, highest chance first. Enemies with
at least five spawn points are preferred, so one-off named creatures only
appear when nothing commoner drops the item.

It is built from the database of the pfQuest addon (https://github.com/shagu/pfQuest,
MIT licence, copyright Eric Mauser), which describes the original game.
Forever's own drop rates are not published and may differ, and items new to
Forever are missing.
