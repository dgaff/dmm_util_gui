# Product requirements for DMM Utility GUI

## Introduction

This is a GUI app that makes interfacing with the Fluke 289 DMM easier. There's currently no
Fluke Connect app for Mac, so this app is meant to be a free replacement.

The Fluke 289 DMM using a serial to optical cable to allow a computer to talk to the meter.
The specification for that protocol is in Fluke289_remote_spec28X.pdf. It's a simple text-based
protocol for which commands either return text or binary.

A command line example of interfacing to the DMM is shown in the fluke_28x_dmm_util module in
this project.

## Goals for the app

1. Modern looking Mac user interface.
2. Ability to pick the correct serial port. The usb-to-optical cable uses a standard FTDI chip. So the serial ports are in /dev as one would expect.
3. Ability to see live data from the meter.
4. Ability to start a recording of live data, including the plotting of the data as it's read from the meter.
5. Optionally have the ability to set sampling rate and duration of recording of live data. Or just start/stop recording.
6. Ability to list and download recorded data stored on the meter.
7. Ability to display this downloaded data in the app and also create a CSV file.
8. Where possible, the ability to use all of the commands described in the spec.
9. Tool tips on commands to make it clear what they do.
10. Settings saved so that subsequent restarts of the app will make connection easy.
