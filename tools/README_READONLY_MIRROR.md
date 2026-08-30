# Q-ALPHA — read-only mirror for Cursor Chat A

## Layout

| Folder | Role | Cursor chat |
|--------|------|-------------|
| `Documents\Q-ALPHA` | Editable source of truth | **Chat B** (Agent — can edit) |
| `Documents\Q-ALPHA-READONLY` | Auto-synced mirror, `attrib +R` | **Chat A** (Ask — reference only) |

**Do not use symlinks for this.** A symlink still writes through to the real file.

## Commands

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA

# One-shot sync
.\tools\sync_readonly_mirror.ps1

# Live watch (keeps mirror updated; leave a terminal open)
.\tools\sync_readonly_mirror.ps1 -Watch
```

## Cursor setup

1. **File → Open Folder** → `Q-ALPHA-READONLY` for Chat A  
2. **File → Open Folder** → `Q-ALPHA` for Chat B (or a second Cursor window)  
3. Chat A has `.cursor/rules/read-only-mirror.mdc` (`alwaysApply`) forbidding edits.

## Scheduled backup sync (optional)

Registered as task **`QAlpha Readonly Mirror Sync`** (every 5 minutes while logged in), if you ran the register snippet in `sync_readonly_mirror.ps1` comments / setup.
