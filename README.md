# GTA IV GTA Connected Patcher GUI

A small Windows GUI tool that automatically installs the GTA Connected Retail patch for **Grand Theft Auto IV: The Complete Edition**.

It places the `Retail` folder in the correct location:

```text
Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
````

Not:

```text
Grand Theft Auto IV\Retail\GTAIV.exe
```

## Purpose

GTA Connected requires a patched GTA IV executable for multiplayer compatibility.

This tool automates the manual patching process by:

* Detecting your GTA IV Steam installation when possible
* Letting you browse for the GTA IV folder manually
* Downloading the selected Retail patch
* Extracting the patch safely
* Installing it into the correct `GTAIV\Retail` folder
* Backing up any existing `Retail` folder before replacing it
* Showing and copying the final `GTAIV.exe` path for GTA Connected

## Supported Game Version

This tool is intended for:

```text
Grand Theft Auto IV: The Complete Edition
```

Default Steam path:

```text
C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto IV
```

Correct final executable path:

```text
C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
```

## Requirements

### Running from Source

* Windows
* Python 3.9 or newer
* GTA IV: Complete Edition installed
* Internet access to download the patch

The app uses only Python standard library modules.

No external Python packages are required to run the source version.

### Building an EXE

To build a standalone Windows executable, install PyInstaller:

```bat
py -m pip install pyinstaller
```

## Usage

### Option 1: Run from Python

```bat
py gtaiv_gtac_patcher_gui.py
```

### Option 2: Build Standalone EXE

```bat
py -m PyInstaller --onefile --windowed --name GTAIV-GTAConnected-Patcher gtaiv_gtac_patcher_gui.py
```

The built executable will be created at:

```text
dist\GTAIV-GTAConnected-Patcher.exe
```

## Installation Flow

1. Open the patcher.
2. Select the Retail patch version:

   * `1.0.8.0` recommended
   * `1.0.7.0` optional
3. Confirm or browse for your GTA IV installation folder.
4. Click the install button.
5. Wait for the download and extraction to complete.
6. Copy the generated `GTAIV.exe` path.
7. Open GTA Connected.
8. Go to:

```text
Tools → Game Settings
```

9. Select:

```text
Grand Theft Auto IV
```

10. Set `Game EXE Path` to the generated path:

```text
...\Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
```

## Accepted Folder Selection

You may select either of these folders:

```text
C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto IV
```

Or:

```text
C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto IV\GTAIV
```

The tool will normalize the path and install the patch to:

```text
...\Grand Theft Auto IV\GTAIV\Retail
```

## Correct Folder Structure

After patching, the folder should look like this:

```text
Grand Theft Auto IV\
└── GTAIV\
    └── Retail\
        └── GTAIV.exe
```

## Incorrect Folder Structure

Do not install the patch like this:

```text
Grand Theft Auto IV\
└── Retail\
    └── GTAIV.exe
```

That path is wrong and GTA Connected will not use the correct executable.

## Existing Retail Folder

If a `Retail` folder already exists, the tool backs it up before installing the new one.

Backup folders use a timestamped name similar to:

```text
Retail.backup-20260516-153000
```

This prevents accidental deletion of an existing patched setup.

## Patch Versions

The tool can download:

```text
Retail 1.0.8.0
Retail 1.0.7.0
```

Recommended version:

```text
1.0.8.0
```

Use `1.0.7.0` only if a specific server or setup requires it.

## File Integrity

The patcher does not overwrite your original GTA IV installation files.

It installs a separate patched copy inside:

```text
GTAIV\Retail
```

Your original Complete Edition files remain untouched, and you can still launch the normal GTA IV campaigns through Steam or Rockstar Launcher.

## Troubleshooting

### The app cannot find GTA IV automatically

Use the Browse button and select your GTA IV install folder manually.

Usually this is:

```text
C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto IV
```

### GTA Connected does not launch the game

Verify that `Game EXE Path` points to:

```text
...\Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
```

Not:

```text
...\Grand Theft Auto IV\Retail\GTAIV.exe
```

### The patch download fails

Check:

* Internet connection
* Firewall or antivirus restrictions
* Whether the download server is reachable

Then run the patcher again.

### The app cannot write to the GTA IV folder

The Steam install folder may require administrator permissions.

Try one of the following:

* Run the patcher as administrator
* Move GTA IV to a writable Steam library folder
* Check folder permissions

## Building Notes

Using uv:

```bat
uv sync
uv run pyinstaller --onefile --windowed --name GTAIV-GTAConnected-Patcher gtaiv_gtac_patcher_gui.py
```

Output will be located at:

```text
dist\GTAIV-GTAConnected-Patcher.exe
```

## License

This project is licensed under the MIT License.

## Disclaimer

This project is an unofficial helper utility for installing the GTA Connected GTA IV Retail patch.

This repository is NOT an official GTA Connected release unless explicitly stated otherwise.

This tool does NOT include or redistribute Grand Theft Auto IV, GTA Connected, Rockstar Games files, or patch archives. It only automates downloading and installing publicly provided patch files into the correct local folder.

Grand Theft Auto IV is property of Rockstar Games and Take-Two Interactive. This project is not affiliated with or endorsed by Rockstar Games, Take-Two Interactive, or Steam.