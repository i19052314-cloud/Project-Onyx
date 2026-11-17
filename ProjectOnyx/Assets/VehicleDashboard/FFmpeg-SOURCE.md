# FFmpeg audio helper

This separately licensed helper runs only as an audio-preparation subprocess.
The Onyx ASI and managed dashboard do not link against its libraries. The files
remain unmodified and replaceable; Project Onyx's alpha terms do not restrict
the rights granted by the helper's own licenses.

Version: n9.0.2-22-g46d8f462ee-20261001, Windows x64, LGPL shared build.
Binary package: https://github.com/BtbN/FFmpeg-Builds/releases/tag/latest
Release publication: 2026-10-01T13:27:49Z.
Original archive: ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip
SHA256: d892076519d72a343355b32b5206aafab053d4b5377c48a96183315d1b087369

Corresponding FFmpeg source:
https://github.com/FFmpeg/FFmpeg/tree/46d8f462ee
https://github.com/FFmpeg/FFmpeg/archive/46d8f462ee.tar.gz

Build recipes, dependency sources, patches and configuration:
https://github.com/BtbN/FFmpeg-Builds
https://github.com/BtbN/FFmpeg-Builds/tree/master/scripts.d
The original archive's include and lib directories remain available from the
binary package above. `ffmpeg.exe -buildconf` prints its exact build configuration.

Copyright (c) the FFmpeg developers and respective dependency authors.
See FFmpeg-LICENSE.txt (original LGPLv3 notice) and FFmpeg-GPLv3.txt (the GPLv3
terms incorporated by LGPLv3). These notices apply to this external helper,
not to the rest of Project Onyx. No ffplay application is included.
