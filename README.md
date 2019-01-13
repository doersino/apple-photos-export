# apple-photos-export

**Please note that this script is written to fit my (admittedly weird) use case. No care was taken to make it particularly useful to anyone else, most notably it's *not an all-purpose backup tool*. Continue reading to find out what exactly it does.**

Back when I was using an Android-powered Nexus 5 and Dropbox's "Camera Uploads" feature, everything was great:

1. The phone would save photos (whether HDR or not) as `.jpg`, videos as `.mp4` and screenshots as `.png`.
2. Dropbox would continuously collect them and they'd end up on a folder on my laptop (with more or less sensible, time-based filenames), from where I could periodically archive them to a big external disk.

Then I got myself an iPhone, which – in addition to "normal" photos and videos – takes live photos, HDR photos etc., for which Dropbox only uploads the "base" photo. Also, some apps such as WhatsApp store received images in the camera roll, which messes everything up. And I also wanted a JPEG version of all HEIC files for future proofing slash portability.

TODO instagram: prev via manual dropbox file upload, now they also just end up in the camera roll

Wanting to keep my previous archival scheme running (and having it be complete, i.e. also containing the short videos corresponding to live photos), I've come up with the following workflow:

1. Connect the iPhone to my laptop.
2. Import all new photos into Photos.app.
3. Magic (i.e. run the code contained in this repository, which is convoluted and will invariably break once a major update comes along).
4. Success.
5. TODO Delete everything from the phone and photos.app?


## Talk is cheap. Show me (how to use) the code.

1. Install `exiftool`.
2. Install `python3`.
3. Make sure `sips` is working (this should be included in your macOS installation).
4. `pip3 install configfile`
5. Copy `apple-photo-export.ini.example` to `apple-photo-export.ini` in your target path
6. Call as `python3 apple-photo-export.py TARGET [-q]`

TODO ...

Note: Only tested with photos imported from an iPhone via USB. No idea if, and how, this needs to be adjusted for iCloud use.

Note: All of the above (and below) probably works/holds for whatever version of mscOS/Photos was most recent at the time of the most recent commit to this repository. Commit TODO was current in January 2019.


## Notes on `photos.db` and the folder structure of `.photoslibrary`

In order to ... reverse-engineer some of the structure. This proved a bit frustrating since the database really doesn't seem to contains much detail re. discerning different types of media. At least I couldn't find a "clean" way of doing that, so my way will inevitably break on updates.

As of January 2019, TODO

TODO


## Future work

* # TODO faces into filenames? i don't use this (yet)
