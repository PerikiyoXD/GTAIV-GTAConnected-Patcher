# GTA IV GTA Connected Patcher GUI

A small Windows GUI tool that automatically installs the GTA Connected Retail patch for **Grand Theft Auto IV: The Complete Edition**.

The patch is installed to the correct location:

```
Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
```

## Purpose

GTA Connected requires a patched GTA IV executable for multiplayer compatibility. This tool automates the manual patching process by:

- Detecting your GTA IV Steam installation when possible
- Letting you browse for the GTA IV folder manually
- Downloading and extracting the selected Retail patch
- Installing it into the correct `GTAIV\Retail` folder
- Backing up any existing `Retail` folder before replacing it
- Showing and copying the final `GTAIV.exe` path for use in GTA Connected

## Download

A prebuilt Windows executable is available on the [releases page](https://github.com/PerikiyoXD/GTAIV-GTAConnected-Patcher/releases/download/1.0/GTAIV-GTAConnected-Patcher.exe).

## Requirements

- Windows
- [uv](https://github.com/astral-sh/uv)
- GTA IV: The Complete Edition installed
- Internet access to download the patch

## Usage

### Run from source

```bat
uv run gtaiv_gtac_patcher_gui.py
```

### Build a standalone EXE

```bat
uv sync
uv run --no-project pyinstaller --onefile --windowed --name GTAIV-GTAConnected-Patcher gtaiv_gtac_patcher_gui.py
```

Output: `dist\GTAIV-GTAConnected-Patcher.exe`

## Installation Flow

1. Open the patcher.
2. Select a Retail patch version. `1.0.8.0` is recommended; use `1.0.7.0` only if a specific server requires it.
3. Confirm or browse for your GTA IV installation folder.
4. Click **Install** and wait for the download and extraction to complete.
5. Copy the generated `GTAIV.exe` path.
6. In GTA Connected, go to **Tools > Game Settings > Grand Theft Auto IV** and set `Game EXE Path` to the copied path.

## Folder Structure

After patching, the structure should look like this:

```
Grand Theft Auto IV\
+-- GTAIV\
    +-- Retail\
        +-- GTAIV.exe
```

The default Steam path resolves to:

```
C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
```

You can select either the `Grand Theft Auto IV` or `Grand Theft Auto IV\GTAIV` folder. The tool normalizes the path automatically.

> **Note:** If a `Retail` folder already exists, it is backed up with a timestamped name (e.g. `Retail.backup-20260516-153000`) before the new patch is installed.

## File Integrity

The patcher installs into a separate `GTAIV\Retail` subfolder and does not touch your original GTA IV files. Your Complete Edition campaigns remain fully intact and launchable through Steam or Rockstar Launcher.

## Troubleshooting

**GTA IV not detected automatically:** Click **Browse** and select your install folder manually (usually `C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto IV`).

**GTA Connected won't launch the game:** Verify that `Game EXE Path` points to `...\Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe`, not `...\Grand Theft Auto IV\Retail\GTAIV.exe`.

**Download fails:** Check your internet connection and firewall/antivirus settings, then re-run the patcher.

**Cannot write to the GTA IV folder:** Run the patcher as administrator, or move GTA IV to a Steam library folder with write permissions.

## License

MIT

## Disclaimer

This is an unofficial helper utility and is not affiliated with or endorsed by Rockstar Games, Take-Two Interactive, or Steam. It does not include or redistribute any GTA IV or GTA Connected files. It only automates downloading and installing publicly provided patch files into the correct local folder.