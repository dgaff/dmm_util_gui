# DMM Utility GUI

This project started from the forked `dmm_util` in this repo. I asked Claude Fable (Mythos with cyber/bio protections) to build a nice GUI around the DMM meter commands
because there's no Fluke Connect Mac app. This app blows Fluke Connect away. It's
engineer-centric, so no frills on the UI, but it exposes everything in the meter
protocol we could find. If you're curious about how I started Claude on the work,
read the prd.md file. Claude also produced a summary.md file of the work completed.
I've only tested this on Mac, but since it uses the QT library, it should run on
Windows, too. You'll need to build your own binary (see the packaging folder) if
you want a self-contained app rather than running from source.

**Live View** — big live readout, rolling plot, and session recording at a
  configurable sample rate with optional auto-stop duration; save sessions as CSV.

<img src="images/live_view.png" width="800">

**Memory View** — list everything stored on the meter (recordings, min/max, peak,
  saved measurements), download recordings with progress/cancel, plot
  primary/min/max, export any item to CSV, and delete all memory (undocumented command).

<img src="images/memory_view.png" width="800">

**Meter View** — identity and configuration, sync the meter clock to the Mac,
  edit owner info (company/contact/operator/site) and the 8 save-name slots,
  and send the DS/RMP/RI reset commands (with confirmation).

<img src="images/meter_settings.png" width="800">

**Console** — send any protocol command raw, with a picker and tooltips for
  every known command (documented and reverse engineered); binary responses
  are shown as a hex dump.

<img src="images/console_view.png" width="800">

Note that aettings (port, auto-connect, sample interval, window layout) persist between
launches. Tooltips throughout explain what each control sends to the meter.

## Running It

```sh
python3 -m venv venv                      # once, if you don't have the venv
venv/bin/pip install -r requirements.txt  # once
venv/bin/python -m dmm_gui
```

Pick the IR cable's port (it shows up as `/dev/cu.usbserial-…`, FTDI ports are
listed first), press **Connect**, and check *Connect at launch* if you want the
app to reconnect automatically next time.

## Building a Mac app bundle

```sh
venv/bin/pip install pyinstaller   # once
packaging/build_app.sh
```

This regenerates the app icon and produces `dist/DMM Utility.app`
(ad-hoc signed); drag it to /Applications to install. The bundled app and
the run-from-source version share the same saved settings.

## Testing
(no meter needed — runs against a simulated Fluke 289):

```sh
venv/bin/python tests/test_protocol.py
QT_QPA_PLATFORM=offscreen venv/bin/python tests/test_gui.py
```

## Original docs from the forked CLI dmm_util repo
dmm_util is a utility for interacting with Fluke 289 and 287 Series multimeters.
You can:
- download saved measurements
- download saved recordings
- download saved min-max
- download peaks
- set date and time
- set internal data such as company,contact,operator,or site
- display realtime measurements
- set available record names list
- display configuration informations

This is a complete rewrite of [Fredrik Valeur dmm_util](https://github.com/fvaleur/dmm_util)  
Previous one was written in Ruby. This one is written in Python 3.  
There are also some changes to the code.

**Prerequisites:**

**Python 3.10+** and pyserial must have been installed before use.

many things have been added as we go along. So the code is not optimal.

**Important**  
All the data displayed are those returned by the multimeter.  
None is calculated or processed


**How to install it:**

1. - Using PyPi (the simplest way)  
Latest **release** can be installed by typing:  
`python -m pip install fluke_28x_dmm_util`  
[![PyPI version](https://badge.fury.io/py/fluke-28x-dmm-util.svg)](https://badge.fury.io/py/fluke-28x-dmm-util)  
[![Downloads](https://pepy.tech/badge/fluke-28x-dmm-util)](https://pepy.tech/project/fluke-28x-dmm-util)


2. - From github (newest version):  
Latest **commit** can be used by typing:  
`git clone https://github.com/N0ury/dmm_util.git`  



3. -  From Github [release](https://github.com/N0ury/dmm_util/releases)  
![GitHub release (latest by date)](https://img.shields.io/github/v/release/N0ury/dmm_util)  
Get the latest release and unzip (or gunzip) it  
cd to the folder that has been created and run the utility as shown above.


**How to run the utility**  
Main command is:  
`python -m fluke_28x_dmm_util -p PORT ...`  

**If you have installed the release (.zip or /tgz), you need to change directory to the one of installation. The directory before running the utility MUST be dmm_util.**

If you have installed the PyPi version this is not necessary, and you can run the utility anywhere.  

Syntax:  

`python -m fluke_28x_dmm_util options command`  

**options**  

{-p|--port} PORT  
This is mandatory, it's the port to which the DMM is connected (eg: COM3)   

{-s|--separator} SEPARATOR  
Separator is optional. Default is TAB  

{-t|--timeout} TIMEOUT  
Timeout is optional. Default is 0.09 (in seconds)  
You need to change this only if timeouts occur.

{-o|--overloads}  
Don't display lines containing overloads (lines with values 9.99999999e+37) or invalid values  
Applie to `get recordings` only  

**command**  
This depends on what you want to do  
- get:  
get recordings {name | index} [,{name | index}...]  
get minmax {name | index} [,{name | index}...]  
get peak {name | index} [,{name | index}...]  
get measurements {name | index} [,{name | index}...]  
get current: get current measured values  
get config: get DMM configuration  
get names: get DMM names prefix used for storing data  

'name' is the name used for a recording, 'index' is a number  
These data can be displayed with 'list' command,  
'name' can be surrounded by quotes in case it contains spaces.  
If this parameter contains only digits, value is assumed to be an index.  
Otherwise, it will be taken as a name. Multiple names or indexes are permitted, they must be comma separated, with no spaces before or after the commas.  

Example:  
get recordings 1,"Record 2",5  
get recordings 3  

This command displays detailed recordings informations

- set:  
set company <value>: set DMM company name  
set operator <value>: set DMM operator name  
set site <value>: set DMM site name  
set contact <value>: set DMM contact name  
set datetime: set DMM date and time to the PC current date/time  
set names <index> <name>: set the name of recording at given index  

'index' is a value between 1 and 8. List can be obtained using 'get names'.  

Example:  
set operator N0ury  
set name 2 Min_Max  

- list  
list recordings: list recording type recordings  
list minmax: list min/max type recordings  
list peak: list peak type recordings  
list measurements: list all the measurements  
list all: list all the memory stored values  

This command displays general informations about recordings  

**Common issues**
```
  File "python3_dmm_util.py", line nn
    match len(name):
            ^
SyntaxError: invalid syntax
```

You are using Python version less than 3.10, consider upgrading.

```
Traceback (most recent call last):
  File "python3_dmm_util.py", line 4, in <module>
    import serial
ModuleNotFoundError: No module named 'serial'
```

pyserial module is needed.
Install it this way  
`python -m pip install pyserial`

**Copyright**

Copyright © 2011 Fredrik Valeur.  
Copyright © 2017-2022 N0ury.  
Copyright © 2026 Doug Gaff.
